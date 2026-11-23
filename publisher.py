"""Write subscription files and optionally publish them to Git."""

from __future__ import annotations

import base64
import json
import logging
import os
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable
from urllib.parse import quote

from tester import TestResult

LOGGER = logging.getLogger(__name__)


def _clean_display_name(name: str) -> str:
    name = (name or "").replace("\r", " ").replace("\n", " ")
    name = re.sub(r"[\x00-\x1f\x7f]", "", name)
    name = name.replace("|", "-").replace("#", "")
    name = re.sub(r"\s+", " ", name).strip()
    return name[:90]


def format_name(result: TestResult, rank: int) -> str:
    config = result.config
    latency = f"{result.latency_ms:.0f}ms" if result.latency_ms is not None else "n/a"
    speed = f"{result.speed_mbps:.1f}Mbps" if result.speed_mbps else "n/a"
    original = _clean_display_name(config.name)
    base = f"{rank:02d} | {config.protocol.upper()} | {latency} | {speed}"
    return f"{base} | {original}" if original else base


def rewrite_uri(uri: str, name: str) -> str:
    base = uri.split("#", 1)[0]
    if base.lower().startswith("vmess://"):
        # Most clients display the "ps" field from the VMess JSON payload, not
        # the URI fragment.  Rewrite the payload so the rank/speed is visible
        # in PassWall as well.
        payload = base[len("vmess://") :].strip()
        try:
            compact = re.sub(r"\s+", "", payload).replace("-", "+").replace("_", "/")
            compact += "=" * (-len(compact) % 4)
            obj = json.loads(base64.b64decode(compact, validate=False).decode("utf-8"))
            if isinstance(obj, dict):
                obj["ps"] = name
                encoded = base64.b64encode(
                    json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
                ).decode("ascii")
                return f"vmess://{encoded}"
        except Exception:
            # Fall through to the fragment form; the original link is still valid.
            pass
    return f"{base}#{quote(name, safe='')}"


def build_links(results: Iterable[TestResult]) -> list[str]:
    links: list[str] = []
    for rank, result in enumerate(results, start=1):
        links.append(rewrite_uri(result.config.uri, format_name(result, rank)))
    return links


def _atomic_write(path: Path, text: str) -> None:
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(text, encoding="utf-8", newline="\n")
    os.replace(temporary, path)


def _metadata(
    results: list[TestResult],
    *,
    source_url: str,
    stats: dict[str, int],
    duration: float,
    generated_at: datetime,
) -> dict:
    return {
        "generated_at": generated_at.astimezone(timezone.utc).isoformat(timespec="seconds"),
        "source_url": source_url,
        "duration_seconds": round(duration, 1),
        "stats": stats,
        "selected": [
            {
                "rank": rank,
                "protocol": result.config.protocol,
                "host": result.config.host,
                "port": result.config.port,
                "latency_ms": round(result.latency_ms, 1) if result.latency_ms else None,
                "speed_mbps": round(result.speed_mbps, 3) if result.speed_mbps else None,
                "score": round(result.score, 4),
                "name": format_name(result, rank),
                "uri": rewrite_uri(result.config.uri, format_name(result, rank)),
            }
            for rank, result in enumerate(results, start=1)
        ],
    }


def _readme(
    results: list[TestResult],
    *,
    source_url: str,
    stats: dict[str, int],
    generated_at: datetime,
) -> str:
    lines = [
        "# Best V2Ray configs",
        "",
        f"Last update: **{generated_at.astimezone(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}**",
        "",
        f"Source: <{source_url}>",
        "",
        (
            f"Scanned **{stats.get('tested', 0)}** configs, "
            f"**{stats.get('working', 0)}** responded, "
            f"showing the best **{len(results)}**."
        ),
        "",
        "| # | Protocol | Latency | Speed | Server | Name |",
        "|---:|---|---:|---:|---|---|",
    ]
    for rank, result in enumerate(results, start=1):
        latency = f"{result.latency_ms:.0f} ms" if result.latency_ms else "n/a"
        speed = f"{result.speed_mbps:.2f} Mbps" if result.speed_mbps else "n/a"
        name = _clean_display_name(result.config.name).replace("|", "-")
        lines.append(
            f"| {rank} | {result.config.protocol.upper()} | {latency} | {speed} | "
            f"`{result.config.host}:{result.config.port}` | {name} |"
        )
    lines += [
        "",
        "## Subscription URLs",
        "",
        "- `sub_base64.txt` - standard base64 subscription (recommended for PassWall 2)",
        "- `sub.txt` - plain links, one per line",
        "",
        "Use the **raw** GitHub URL of one of these files in your router. "
        "The repository is updated automatically by the scanner.",
        "",
        "> This file is generated automatically. Do not edit it by hand.",
        "",
    ]
    return "\n".join(lines)


def write_outputs(
    *,
    output_dir: Path,
    results: list[TestResult],
    source_url: str,
    stats: dict[str, int],
    duration: float,
) -> dict[str, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    generated_at = datetime.now().astimezone()
    links = build_links(results)

    plain = "\n".join(links) + "\n"
    encoded = base64.b64encode("\n".join(links).encode("utf-8")).decode("ascii")
    metadata = _metadata(
        results,
        source_url=source_url,
        stats=stats,
        duration=duration,
        generated_at=generated_at,
    )
    readme = _readme(results, source_url=source_url, stats=stats, generated_at=generated_at)

    files = {
        "sub.txt": output_dir / "sub.txt",
        "sub_base64.txt": output_dir / "sub_base64.txt",
        "best10.json": output_dir / "best10.json",
        "README.md": output_dir / "README.md",
    }
    _atomic_write(files["sub.txt"], plain)
    _atomic_write(files["sub_base64.txt"], encoded)
    _atomic_write(files["best10.json"], json.dumps(metadata, ensure_ascii=False, indent=2) + "\n")
    _atomic_write(files["README.md"], readme)
    return files


def _run_git(repo_dir: Path, *args: str, timeout: float = 120.0) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    environment["GIT_TERMINAL_PROMPT"] = "0"
    return subprocess.run(
        ["git", *args],
        cwd=repo_dir,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        env=environment,
    )


def _git_output(repo_dir: Path, *args: str) -> str:
    completed = _run_git(repo_dir, *args)
    if completed.returncode != 0:
        return ""
    return (completed.stdout or "").strip()


def publish_to_git(
    *,
    repo_dir: Path,
    files: Iterable[Path],
    branch: str,
    remote: str,
    commit_message: str,
) -> tuple[bool, str]:
    if not (repo_dir / ".git").exists():
        return False, "not a git repository (run setup_repo.ps1 first)"
    if not _git_output(repo_dir, "remote", "get-url", remote):
        return False, f"git remote '{remote}' is not configured"

    if not _git_output(repo_dir, "config", "user.name"):
        _run_git(repo_dir, "config", "user.name", "Config Scanner")
    if not _git_output(repo_dir, "config", "user.email"):
        _run_git(repo_dir, "config", "user.email", "config-scanner@localhost")

    relative_files = [str(Path(path).relative_to(repo_dir)).replace("\\", "/") for path in files]
    added = _run_git(repo_dir, "add", "--", *relative_files)
    if added.returncode != 0:
        return False, f"git add failed: {added.stderr.strip()}"

    diff = _run_git(repo_dir, "diff", "--cached", "--quiet")
    if diff.returncode == 0:
        return True, "no changes to commit"
    if diff.returncode not in (0, 1):
        return False, f"git diff failed: {diff.stderr.strip()}"

    commit = _run_git(repo_dir, "commit", "-m", commit_message)
    if commit.returncode != 0:
        return False, f"git commit failed: {(commit.stderr or commit.stdout).strip()}"

    upstream = _git_output(repo_dir, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}")
    if upstream:
        pull = _run_git(repo_dir, "pull", "--rebase", "--autostash")
        if pull.returncode != 0:
            _run_git(repo_dir, "rebase", "--abort")
            return False, f"git pull --rebase failed: {(pull.stderr or pull.stdout).strip()}"

    if upstream:
        push = _run_git(repo_dir, "push")
    else:
        push = _run_git(repo_dir, "push", "-u", remote, branch)
    if push.returncode != 0:
        return False, f"git push failed: {(push.stderr or push.stdout).strip()}"
    return True, "pushed"


def raw_urls(repo_dir: Path, branch: str) -> dict[str, str]:
    remote_url = _git_output(repo_dir, "remote", "get-url", "origin")
    if not remote_url:
        return {}
    match = re.search(r"github\.com[:/](?P<owner>[^/]+)/(?P<repo>[^/]+?)(?:\.git)?$", remote_url)
    if not match:
        return {}
    owner = match.group("owner")
    repo = match.group("repo")
    base = f"https://raw.githubusercontent.com/{owner}/{repo}/refs/heads/{branch}/output"
    return {
        "base64": f"{base}/sub_base64.txt",
        "plain": f"{base}/sub.txt",
        "metadata": f"{base}/best10.json",
    }
