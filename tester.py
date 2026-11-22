"""Connectivity and throughput tests executed through real Xray outbounds.

Every configuration is tested in its own short-lived Xray process with a local
SOCKS inbound.  This is slower than one giant Xray instance, but it is much more
robust: a single malformed node can never prevent the rest of the batch from
being tested, and a process can be killed cleanly when a test times out.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import socket
import subprocess
import time
import warnings
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

import httpx

from config_parser import ParsedConfig

warnings.filterwarnings("ignore", message=".*Unverified HTTPS request.*")

LOGGER = logging.getLogger(__name__)

HTTP_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Accept": "*/*",
    "Accept-Encoding": "identity",
}


@dataclass
class TestSettings:
    xray_path: Path
    concurrency: int = 12
    xray_start_timeout: float = 6.0
    latency_timeout: float = 7.0
    speed_timeout: float = 14.0
    speed_bytes: int = 2_000_000
    min_speed_bytes: int = 24_000
    latency_urls: list[str] = field(
        default_factory=lambda: [
            "https://www.gstatic.com/generate_204",
            "https://cp.cloudflare.com/generate_204",
        ]
    )
    speed_urls: list[str] = field(
        default_factory=lambda: [
            "https://speed.cloudflare.com/__down?bytes=2000000",
            "https://cachefly.cachefly.net/1mb.test",
        ]
    )
    keep_runtime: bool = False


@dataclass
class TestResult:
    config: ParsedConfig
    ok: bool
    latency_ms: float | None = None
    speed_mbps: float | None = None
    bytes_read: int = 0
    error: str = ""
    score: float = 0.0


@dataclass
class PrefilterResult:
    config: ParsedConfig
    reachable: bool
    error: str = ""


def _creation_flags() -> int:
    return getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0


def _find_free_ports(count: int, start: int = 24000) -> list[int]:
    ports: list[int] = []
    port = start
    while len(ports) < count and port < 40000:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                sock.bind(("127.0.0.1", port))
                ports.append(port)
            except OSError:
                pass
        port += 1
    if len(ports) < count:
        raise RuntimeError(f"could not reserve {count} local ports")
    return ports


class PortPool:
    def __init__(self, size: int) -> None:
        self._queue: asyncio.Queue[int] = asyncio.Queue()
        for port in _find_free_ports(size):
            self._queue.put_nowait(port)

    async def acquire(self) -> int:
        return await self._queue.get()

    def release(self, port: int) -> None:
        self._queue.put_nowait(port)


def _proxy_url(port: int) -> str:
    # socks5h sends the hostname to Xray instead of resolving it locally, which
    # matches how the router will use the config and avoids local DNS leaks.
    return f"socks5h://127.0.0.1:{port}"


async def _tcp_reachable(host: str, port: int, timeout: float) -> tuple[bool, str]:
    writer = None
    try:
        _, writer = await asyncio.wait_for(asyncio.open_connection(host, port), timeout=timeout)
        return True, ""
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"
    finally:
        if writer is not None:
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass


async def prefilter_configs(
    configs: Iterable[ParsedConfig],
    *,
    concurrency: int,
    timeout: float,
) -> list[PrefilterResult]:
    configs = list(configs)
    semaphore = asyncio.Semaphore(max(1, concurrency))

    async def check(config: ParsedConfig) -> PrefilterResult:
        async with semaphore:
            reachable, error = await _tcp_reachable(config.host, config.port, timeout)
            return PrefilterResult(config=config, reachable=reachable, error=error)

    results = await asyncio.gather(*(check(config) for config in configs))
    reachable_count = sum(1 for item in results if item.reachable)
    LOGGER.info("TCP pre-filter: %d/%d reachable", reachable_count, len(results))
    return list(results)


async def _wait_for_socks_port(
    process: asyncio.subprocess.Process,
    port: int,
    log_path: Path,
    timeout: float,
) -> tuple[bool, str]:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        if process.returncode is not None:
            break
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection("127.0.0.1", port), timeout=0.5
            )
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass
            return True, ""
        except (OSError, asyncio.TimeoutError):
            await asyncio.sleep(0.05)

    if process.returncode is None:
        return False, f"xray did not open port {port} within {timeout:.0f}s"
    try:
        tail = log_path.read_text(encoding="utf-8", errors="replace")[-2000:].strip()
    except Exception:
        tail = "no xray log available"
    return False, f"xray exited with code {process.returncode}: {tail}"


async def _measure_latency(port: int, settings: TestSettings) -> tuple[float | None, str]:
    errors: list[str] = []
    for url in settings.latency_urls:
        client = httpx.AsyncClient(
            proxy=_proxy_url(port),
            timeout=httpx.Timeout(
                connect=settings.latency_timeout,
                read=settings.latency_timeout,
                write=settings.latency_timeout,
                pool=settings.latency_timeout,
            ),
            verify=False,
            trust_env=False,
            follow_redirects=True,
        )
        try:
            started = time.perf_counter()
            response = await client.get(url, headers=HTTP_HEADERS)
            elapsed_ms = (time.perf_counter() - started) * 1000.0
            if response.status_code < 500:
                return elapsed_ms, ""
            errors.append(f"{url}: HTTP {response.status_code}")
        except Exception as exc:
            errors.append(f"{url}: {type(exc).__name__}: {exc}")
        finally:
            await client.aclose()
    return None, " | ".join(errors)


async def _measure_speed(port: int, settings: TestSettings) -> tuple[float | None, int, str]:
    errors: list[str] = []
    for url in settings.speed_urls:
        bytes_read = 0
        started: float | None = None
        client = httpx.AsyncClient(
            proxy=_proxy_url(port),
            timeout=httpx.Timeout(
                connect=settings.latency_timeout,
                read=settings.speed_timeout,
                write=settings.speed_timeout,
                pool=settings.speed_timeout,
            ),
            verify=False,
            trust_env=False,
            follow_redirects=True,
            limits=httpx.Limits(max_connections=1, max_keepalive_connections=0),
        )
        try:
            async with asyncio.timeout(settings.speed_timeout):
                async with client.stream("GET", url, headers=HTTP_HEADERS) as response:
                    response.raise_for_status()
                    started = time.perf_counter()
                    async for chunk in response.aiter_bytes():
                        bytes_read += len(chunk)
                        if bytes_read >= settings.speed_bytes:
                            break
        except Exception as exc:
            errors.append(f"{url}: {type(exc).__name__}: {exc}")
        finally:
            await client.aclose()

        if started is not None and bytes_read >= settings.min_speed_bytes:
            elapsed = max(time.perf_counter() - started, 0.001)
            speed_mbps = bytes_read * 8.0 / elapsed / 1_000_000.0
            return speed_mbps, bytes_read, ""
        if bytes_read:
            errors.append(f"{url}: only {bytes_read} bytes received")
    return None, 0, " | ".join(errors)


async def _test_one(
    config: ParsedConfig,
    port: int,
    settings: TestSettings,
    runtime_dir: Path,
) -> TestResult:
    config_path = runtime_dir / f"xray-{port}.json"
    log_path = runtime_dir / f"xray-{port}.log"
    xray_config = {
        "log": {"loglevel": "warning"},
        "inbounds": [
            {
                "tag": "socks-in",
                "listen": "127.0.0.1",
                "port": port,
                "protocol": "socks",
                "settings": {"auth": "noauth", "udp": False},
            }
        ],
        "outbounds": [config.outbound],
    }
    config_path.write_text(
        json.dumps(xray_config, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    log_handle = log_path.open("wb")
    process: asyncio.subprocess.Process | None = None
    try:
        process = await asyncio.create_subprocess_exec(
            str(settings.xray_path),
            "run",
            "-c",
            str(config_path),
            stdout=log_handle,
            stderr=asyncio.subprocess.STDOUT,
            creationflags=_creation_flags(),
        )
        ready, error = await _wait_for_socks_port(
            process, port, log_path, settings.xray_start_timeout
        )
        if not ready:
            return TestResult(config=config, ok=False, error=error)

        latency_ms, latency_error = await _measure_latency(port, settings)
        if latency_ms is None:
            return TestResult(config=config, ok=False, error=latency_error)

        speed_mbps, bytes_read, speed_error = await _measure_speed(port, settings)
        return TestResult(
            config=config,
            ok=True,
            latency_ms=latency_ms,
            speed_mbps=speed_mbps,
            bytes_read=bytes_read,
            error=speed_error,
        )
    finally:
        if process is not None and process.returncode is None:
            process.terminate()
            try:
                await asyncio.wait_for(process.wait(), timeout=2.0)
            except asyncio.TimeoutError:
                process.kill()
                await process.wait()
        log_handle.close()
        if not settings.keep_runtime:
            config_path.unlink(missing_ok=True)
            log_path.unlink(missing_ok=True)


async def test_configs(
    configs: Iterable[ParsedConfig],
    settings: TestSettings,
    runtime_dir: Path,
) -> list[TestResult]:
    configs = list(configs)
    runtime_dir.mkdir(parents=True, exist_ok=True)
    if not settings.xray_path.exists():
        raise FileNotFoundError(
            f"Xray binary not found at {settings.xray_path}. Run: python bootstrap.py"
        )

    port_pool = PortPool(max(1, settings.concurrency))
    semaphore = asyncio.Semaphore(max(1, settings.concurrency))
    completed = 0
    total = len(configs)

    async def worker(config: ParsedConfig) -> TestResult:
        nonlocal completed
        async with semaphore:
            port = await port_pool.acquire()
            try:
                result = await _test_one(config, port, settings, runtime_dir)
            except Exception as exc:  # pragma: no cover - defensive path
                result = TestResult(
                    config=config,
                    ok=False,
                    error=f"{type(exc).__name__}: {exc}",
                )
            finally:
                port_pool.release(port)

            completed += 1
            if result.ok:
                LOGGER.debug(
                    "[%d/%d] OK   %-6s %-40s %6.0f ms  %6.2f Mbps",
                    completed,
                    total,
                    result.config.protocol,
                    f"{result.config.host}:{result.config.port}",
                    result.latency_ms or 0,
                    result.speed_mbps or 0,
                )
            else:
                LOGGER.debug(
                    "[%d/%d] FAIL %-6s %-40s %s",
                    completed,
                    total,
                    result.config.protocol,
                    f"{result.config.host}:{result.config.port}",
                    result.error[:300],
                )
            if completed % 10 == 0 or completed == total:
                LOGGER.info("Tested %d/%d configurations", completed, total)
            return result

    results = await asyncio.gather(*(worker(config) for config in configs))
    working = sum(1 for result in results if result.ok)
    LOGGER.info("Connectivity test: %d/%d working", working, total)
    return list(results)
