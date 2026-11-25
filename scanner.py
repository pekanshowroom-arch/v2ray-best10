#!/usr/bin/env python3
"""Fetch a V2Ray subscription, test its nodes through real Xray, and publish
the best ones as a small subscription file.

Typical local use:

    python scanner.py                # scan and push
    python scanner.py --no-push      # scan only
    python scanner.py --limit 30 -v  # quick test
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from config_parser import ParseError, is_private_host, parse_config
from publisher import publish_to_git, raw_urls, write_outputs
from subscription import extract_raw_configs, fetch_subscription
from tester import TestSettings, prefilter_configs, test_configs

BASE_DIR = Path(__file__).resolve().parent
LOGGER = logging.getLogger("scanner")

DEFAULT_CONFIG: dict = {
    "source_url": "https://raw.githubusercontent.com/miladtahanian/V2RayCFGDumper/refs/heads/main/sub.txt",
    "source_urls": [],
    "fetch_timeout": 30,
    "top_n": 10,
    "max_per_host": 2,
    "min_speed_mbps": 0.3,
    "protocols": ["vless", "vmess", "trojan", "ss"],
    "concurrency": 12,
    "prefilter": True,
    "prefilter_concurrency": 40,
    "prefilter_timeout": 3.0,
    "xray_start_timeout": 6.0,
    "latency_timeout": 7.0,
    "speed_timeout": 12.0,
    "speed_bytes": 2_000_000,
    "min_speed_bytes": 24_000,
    "latency_urls": [
        "https://www.gstatic.com/generate_204",
        "https://cp.cloudflare.com/generate_204",
    ],
    "speed_urls": [
        "https://speed.cloudflare.com/__down?bytes=2000000",
        "https://cachefly.cachefly.net/1mb.test",
    ],
    "xray_path": "bin/xray.exe",
    "output_dir": "output",
    "git": {
        "enabled": True,
        "branch": "main",
        "remote": "origin",
        "commit_message": "chore: update best V2Ray configs",
    },
}


def _resolve(base: Path, value: str) -> Path:
    path = Path(value).expanduser()
    return path if path.is_absolute() else (base / path)


def _resolve_xray(base: Path, value: str) -> Path:
    path = _resolve(base, value)
    candidates = [path]
    if path.suffix.lower() == ".exe":
        candidates.append(path.with_suffix(""))
    elif os.name == "nt":
        candidates.append(path.with_suffix(".exe"))
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return path


def _load_config(path: Path) -> dict:
    config = json.loads(json.dumps(DEFAULT_CONFIG))
    if path.exists():
        with path.open("r", encoding="utf-8") as handle:
            loaded = json.load(handle)
        if not isinstance(loaded, dict):
            raise ValueError("config.json must contain a JSON object")
        for key, value in loaded.items():
            if key == "git" and isinstance(value, dict):
                config["git"].update(value)
            else:
                config[key] = value
    else:
        LOGGER.warning("Config file not found, using defaults: %s", path)
    return config


def _setup_logging(base: Path, verbose: bool) -> Path:
    logs_dir = base / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    log_path = logs_dir / f"scan-{datetime.now().strftime('%Y%m%d-%H%M%S')}.log"

    root = logging.getLogger()
    root.setLevel(logging.DEBUG)
    root.handlers.clear()

    console = logging.StreamHandler(sys.stdout)
    console.setLevel(logging.DEBUG if verbose else logging.INFO)
    console.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)-7s %(message)s", datefmt="%H:%M:%S")
    )
    file_handler = logging.FileHandler(log_path, encoding="utf-8")
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(
        logging.Formatter(
            "%(asctime)s %(levelname)-7s %(name)s: %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    )
    root.addHandler(console)
    root.addHandler(file_handler)

    for noisy in ("httpx", "httpcore", "asyncio", "urllib3"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    return log_path


def _prepare_configs(
    text: str, allowed_protocols: set[str]
) -> tuple[list, Counter]:
    from subscription import extract_raw_configs

    raw = extract_raw_configs(text)
    reasons: Counter = Counter()
    unique: dict[str, object] = {}
    private = 0
    duplicates = 0
    protocol_skipped = 0

    for item in raw:
        try:
            config = parse_config(item.uri)
        except ParseError as exc:
            reasons[str(exc)] += 1
            continue

        if is_private_host(config.host):
            private += 1
            continue
        if config.protocol not in allowed_protocols:
            protocol_skipped += 1
            continue
        if config.key in unique:
            duplicates += 1
            continue
        unique[config.key] = config

    LOGGER.info(
        "Parsed %d unique configurations (raw=%d, private=%d, duplicates=%d, "
        "protocol-skipped=%d, errors=%d)",
        len(unique),
        len(raw),
        private,
        duplicates,
        protocol_skipped,
        sum(reasons.values()),
    )
    if reasons:
        for reason, count in reasons.most_common(8):
            LOGGER.debug("  skipped %dx: %s", count, reason)
    return list(unique.values()), Counter(
        {
            "source_candidates": len(raw),
            "unique": len(unique),
            "private_skipped": private,
            "duplicates": duplicates,
            "protocol_skipped": protocol_skipped,
            "parse_errors": sum(reasons.values()),
        }
    )


def _rank(results: list, top_n: int, max_per_host: int, min_speed_mbps: float) -> list:
    working = [result for result in results if result.ok and result.latency_ms is not None]
    if not working:
        return []

    speeds = [result.speed_mbps for result in working if result.speed_mbps and result.speed_mbps > 0]
    latencies = [result.latency_ms for result in working if result.latency_ms]
    max_speed = max(speeds) if speeds else 1.0
    min_latency = min(latencies) if latencies else 1.0

    for result in working:
        speed_score = (result.speed_mbps or 0.0) / max_speed if max_speed else 0.0
        latency_score = min_latency / result.latency_ms if result.latency_ms else 0.0
        # Throughput is the primary signal.  A node that is fast but has a
        # 2-second handshake is still more useful than a 0.2 Mbps node with a
        # 700 ms handshake.
        result.score = 0.85 * speed_score + 0.15 * latency_score

    ranked = sorted(
        working,
        key=lambda result: (
            1 if (result.speed_mbps and result.speed_mbps > 0) else 0,
            result.score,
            result.speed_mbps or 0.0,
            -(result.latency_ms or 1e9),
        ),
        reverse=True,
    )

    # Prefer nodes that reach a minimum useful throughput.  If there are not
    # enough of them, fill the remaining slots with slower/latency-only nodes.
    preferred = [
        result
        for result in ranked
        if result.speed_mbps is not None and result.speed_mbps >= min_speed_mbps
    ]
    preferred_ids = {id(result) for result in preferred}
    fallback = [result for result in ranked if id(result) not in preferred_ids]
    ordered = preferred + fallback

    selected: list = []
    per_host: Counter = Counter()
    for result in ordered:
        if max_per_host > 0 and per_host[result.config.host] >= max_per_host:
            continue
        selected.append(result)
        per_host[result.config.host] += 1
        if len(selected) >= top_n:
            break

    if len(selected) < top_n:
        chosen = {id(result) for result in selected}
        for result in ordered:
            if id(result) in chosen:
                continue
            selected.append(result)
            if len(selected) >= top_n:
                break
    LOGGER.info(
        "Ranking: %d preferred (>= %.2f Mbps), %d fallback",
        len(preferred),
        min_speed_mbps,
        len(fallback),
    )
    return selected


def _print_selected(selected: list) -> None:
    LOGGER.info("Selected configurations:")
    LOGGER.info("%-4s %-7s %10s %12s  %s", "#", "PROTO", "LATENCY", "SPEED", "SERVER")
    for rank, result in enumerate(selected, start=1):
        latency = f"{result.latency_ms:.0f} ms" if result.latency_ms else "n/a"
        speed = f"{result.speed_mbps:.2f} Mbps" if result.speed_mbps else "n/a"
        LOGGER.info(
            "%-4d %-7s %10s %12s  %s:%d",
            rank,
            result.config.protocol.upper(),
            latency,
            speed,
            result.config.host,
            result.config.port,
        )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Scan V2Ray subscription and publish the best nodes")
    parser.add_argument("--config", default="config.json", help="path to config.json")
    parser.add_argument("--source", help="override source_url")
    parser.add_argument("--limit", type=int, default=0, help="test at most N unique nodes")
    parser.add_argument("--top", type=int, help="number of nodes to publish")
    parser.add_argument("--concurrency", type=int, help="parallel Xray tests")
    parser.add_argument("--protocols", help="comma separated protocols (default: vless,vmess,trojan,ss)")
    parser.add_argument("--xray", help="path to the Xray binary")
    parser.add_argument("--no-prefilter", action="store_true", help="skip the TCP reachability filter")
    parser.add_argument("--no-push", action="store_true", help="write output but do not commit/push")
    parser.add_argument("--keep-runtime", action="store_true", help="keep temporary Xray files")
    parser.add_argument("--verbose", "-v", action="store_true", help="debug logging")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    base = BASE_DIR
    config_path = _resolve(base, args.config)
    if not config_path.exists() and args.config == "config.json":
        config_path = base / "config.json"
    log_path = _setup_logging(base, args.verbose)

    try:
        config = _load_config(config_path)
    except Exception as exc:
        LOGGER.error("Could not load config: %s", exc)
        return 1

    if args.source:
        config["source_url"] = args.source
        config["source_urls"] = []
    if args.top is not None:
        config["top_n"] = max(1, args.top)
    if args.concurrency is not None:
        config["concurrency"] = max(1, args.concurrency)
    if args.xray:
        config["xray_path"] = args.xray
    if args.no_prefilter:
        config["prefilter"] = False
    if args.no_push:
        config["git"]["enabled"] = False

    if args.protocols:
        allowed_protocols = {item.strip().lower() for item in args.protocols.split(",") if item.strip()}
    else:
        allowed_protocols = {str(item).strip().lower() for item in config.get("protocols", [])}
    allowed_protocols &= {"vless", "vmess", "trojan", "ss"}
    if not allowed_protocols:
        LOGGER.error("No supported protocols selected")
        return 1

    output_dir = _resolve(base, str(config.get("output_dir", "output")))
    runtime_dir = base / "runtime"
    xray_path = _resolve_xray(base, str(config.get("xray_path", "bin/xray.exe")))
    top_n = max(1, int(config.get("top_n", 10)))
    max_per_host = max(0, int(config.get("max_per_host", 2)))
    started = time.perf_counter()

    LOGGER.info("Log file: %s", log_path)
    source_urls: list[str] = []
    for value in config.get("source_urls") or []:
        value = str(value).strip()
        if value and value not in source_urls:
            source_urls.append(value)
    source_url = str(config["source_url"]).strip()
    if source_url and source_url not in source_urls:
        source_urls.append(source_url)
    if not source_urls:
        LOGGER.error("No source URL configured")
        return 1
    LOGGER.info("Source: %s", source_urls[0])
    if len(source_urls) > 1:
        LOGGER.info("Fallback source: %s", ", ".join(source_urls[1:]))
    LOGGER.info("Xray: %s", xray_path)

    if not xray_path.exists():
        LOGGER.error("Xray binary not found: %s", xray_path)
        LOGGER.error("Run: python bootstrap.py")
        return 1

    try:
        LOGGER.info("Downloading subscription...")
        text = fetch_subscription(
            source_urls, timeout=float(config.get("fetch_timeout", 30))
        )
        configs, stats = _prepare_configs(text, allowed_protocols)
        if not configs:
            LOGGER.error("No usable configurations were found in the subscription")
            return 1

        if args.limit and args.limit > 0:
            configs = configs[: args.limit]
            LOGGER.info("Limited to %d configurations by --limit", len(configs))

        if bool(config.get("prefilter", True)):
            prefilter_results = asyncio.run(
                prefilter_configs(
                    configs,
                    concurrency=int(config.get("prefilter_concurrency", 40)),
                    timeout=float(config.get("prefilter_timeout", 3.0)),
                )
            )
            reachable = [item.config for item in prefilter_results if item.reachable]
            stats["prefiltered"] = len(reachable)
            if not reachable:
                LOGGER.error("None of the configurations were reachable over TCP")
                return 1
        else:
            reachable = configs

        settings = TestSettings(
            xray_path=xray_path,
            concurrency=int(config.get("concurrency", 12)),
            xray_start_timeout=float(config.get("xray_start_timeout", 6.0)),
            latency_timeout=float(config.get("latency_timeout", 7.0)),
            speed_timeout=float(config.get("speed_timeout", 12.0)),
            speed_bytes=int(config.get("speed_bytes", 2_000_000)),
            min_speed_bytes=int(config.get("min_speed_bytes", 24_000)),
            latency_urls=list(config.get("latency_urls", DEFAULT_CONFIG["latency_urls"])),
            speed_urls=list(config.get("speed_urls", DEFAULT_CONFIG["speed_urls"])),
            keep_runtime=bool(args.keep_runtime),
        )

        LOGGER.info("Testing %d configurations through Xray...", len(reachable))
        results = asyncio.run(test_configs(reachable, settings, runtime_dir))
        working = [result for result in results if result.ok]
        stats["tested"] = len(reachable)
        stats["working"] = len(working)
        if not working:
            LOGGER.error("No configuration passed the connectivity test")
            return 1

        selected = _rank(
            results,
            top_n=top_n,
            max_per_host=max_per_host,
            min_speed_mbps=float(config.get("min_speed_mbps", 0.3)),
        )
        if not selected:
            LOGGER.error("No configuration could be selected")
            return 1
        stats["selected"] = len(selected)
        if len(selected) < top_n:
            LOGGER.warning(
                "Only %d nodes passed the tests; requested %d", len(selected), top_n
            )
        _print_selected(selected)

        duration = time.perf_counter() - started
        outputs = write_outputs(
            output_dir=output_dir,
            results=selected,
            source_url=str(config["source_url"]),
            stats=stats,
            duration=duration,
        )
        LOGGER.info("Wrote %d files to %s", len(outputs), output_dir)

        branch = str(config["git"].get("branch", "main"))
        remote = str(config["git"].get("remote", "origin"))
        urls = raw_urls(base, branch)
        if urls:
            LOGGER.info("PassWall 2 subscription (base64): %s", urls["base64"])
            LOGGER.info("Plain links:                      %s", urls["plain"])
        else:
            LOGGER.info("Local subscription: %s", outputs["sub_base64.txt"])

        exit_code = 0
        if bool(config["git"].get("enabled", True)):
            message = str(config["git"].get("commit_message", "chore: update best V2Ray configs"))
            message = f"{message} ({datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')})"
            try:
                published, detail = publish_to_git(
                    repo_dir=base,
                    files=outputs.values(),
                    branch=branch,
                    remote=remote,
                    commit_message=message,
                )
            except Exception as exc:  # pragma: no cover - git diagnostic path
                published, detail = False, f"{type(exc).__name__}: {exc}"
            if published:
                LOGGER.info("Git publish: %s", detail)
            else:
                if detail.startswith(("not a git repository", "git remote")):
                    LOGGER.warning("Git publish skipped: %s", detail)
                else:
                    LOGGER.error("Git publish failed: %s", detail)
                    exit_code = 2

        LOGGER.info("Done in %.1f seconds", time.perf_counter() - started)
        return exit_code
    except KeyboardInterrupt:
        LOGGER.warning("Interrupted by user")
        return 130
    except Exception as exc:
        LOGGER.error("Fatal error: %s", exc)
        LOGGER.debug("Traceback", exc_info=True)
        return 1


if __name__ == "__main__":
    sys.exit(main())
