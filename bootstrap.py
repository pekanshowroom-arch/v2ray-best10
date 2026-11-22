#!/usr/bin/env python3
"""Download a local Xray-core binary used by the scanner.

The scanner starts one short-lived Xray process per configuration.  Keeping the
binary inside the project makes the setup reproducible and avoids requiring a
system-wide Xray installation.
"""

from __future__ import annotations

import argparse
import os
import platform
import shutil
import stat
import subprocess
import sys
import tempfile
import urllib.request
import zipfile
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DEFAULT_BIN_DIR = BASE_DIR / "bin"

# Xray 26 removed the allowInsecure TLS option, which is still present in a
# large part of the public subscription.  Pin the last 25.x release instead of
# silently losing most nodes.  Override with --version if you know what you need.
PINNED_VERSION = "v25.12.8"
RELEASE_URL = "https://github.com/XTLS/Xray-core/releases/download/{version}/{asset}"
MIRRORS = (
    "https://ghproxy.net/",
    "https://gh-proxy.com/",
)


def asset_name() -> str:
    system = platform.system().lower()
    machine = platform.machine().lower()

    if system == "windows":
        if machine in {"arm64", "aarch64"}:
            return "Xray-windows-arm64-v8a.zip"
        return "Xray-windows-64.zip"
    if system == "linux":
        if machine in {"arm64", "aarch64"}:
            return "Xray-linux-arm64-v8a.zip"
        return "Xray-linux-64.zip"
    if system == "darwin":
        if machine in {"arm64", "aarch64"}:
            return "Xray-macos-arm64-v8a.zip"
        return "Xray-macos-64.zip"

    raise SystemExit(f"Unsupported platform: {system} {machine}")


def binary_name() -> str:
    return "xray.exe" if os.name == "nt" else "xray"


def download(url: str, destination: Path) -> None:
    print(f"Downloading {url}")
    request = urllib.request.Request(url, headers={"User-Agent": "configscaner/1.0"})
    with urllib.request.urlopen(request, timeout=120) as response:
        total = int(response.headers.get("Content-Length") or 0)
        downloaded = 0
        with destination.open("wb") as handle:
            while True:
                chunk = response.read(1024 * 256)
                if not chunk:
                    break
                handle.write(chunk)
                downloaded += len(chunk)
                if total:
                    percent = downloaded * 100 / total
                    print(f"\r  {percent:5.1f}%  {downloaded / 1048576:6.1f} MiB", end="")
                else:
                    print(f"\r  {downloaded / 1048576:6.1f} MiB", end="")
        print()


def download_with_fallback(url: str, destination: Path) -> None:
    """Try GitHub directly, then a few release mirrors.

    Direct GitHub release downloads are reset on some Iranian networks even
    though raw.githubusercontent.com works.  The mirrors serve the same asset.
    """

    candidates = [url, *(mirror + url for mirror in MIRRORS)]
    last_error: Exception | None = None
    for candidate in candidates:
        try:
            download(candidate, destination)
            if destination.exists() and destination.stat().st_size > 1_000_000:
                return
            raise RuntimeError("downloaded file is too small")
        except Exception as exc:  # pragma: no cover - network diagnostic path
            last_error = exc
            print(f"  failed: {type(exc).__name__}: {exc}")
            destination.unlink(missing_ok=True)
    raise SystemExit(f"could not download Xray: {last_error}")


def find_extracted_binary(directory: Path) -> Path | None:
    target = binary_name()
    for path in directory.rglob(target):
        if path.is_file():
            return path
    return None


def version_of(binary: Path) -> str:
    try:
        completed = subprocess.run(
            [str(binary), "version"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=20,
        )
        output = (completed.stdout or completed.stderr or "").strip()
        return output.splitlines()[0] if output else "unknown version"
    except Exception as exc:  # pragma: no cover - diagnostic path
        return f"could not run binary: {exc}"


def main() -> int:
    parser = argparse.ArgumentParser(description="Download Xray-core")
    parser.add_argument("--force", action="store_true", help="replace an existing binary")
    parser.add_argument("--version", default=PINNED_VERSION, help="Xray release tag")
    parser.add_argument("--bin-dir", type=Path, default=DEFAULT_BIN_DIR)
    parser.add_argument("--url", help="override the download URL")
    args = parser.parse_args()

    bin_dir = args.bin_dir.resolve()
    bin_dir.mkdir(parents=True, exist_ok=True)
    target = bin_dir / binary_name()

    if target.exists() and not args.force:
        current = version_of(target)
        if f" {args.version} " in f" {current} " or (args.version == PINNED_VERSION and " 25." in current):
            print(f"Xray {args.version} already exists: {target}")
            print(current)
            return 0
        print(f"Existing Xray is not the pinned version ({args.version}); replacing it.")
        print(current)

    url = args.url or RELEASE_URL.format(version=args.version, asset=asset_name())
    with tempfile.TemporaryDirectory(prefix="xray-download-") as temporary:
        temporary_path = Path(temporary)
        archive = temporary_path / "xray.zip"
        download_with_fallback(url, archive)

        if not zipfile.is_zipfile(archive):
            raise SystemExit("The downloaded file is not a valid zip archive.")

        with zipfile.ZipFile(archive) as zipped:
            zipped.extractall(temporary_path / "extracted")

        extracted = find_extracted_binary(temporary_path / "extracted")
        if extracted is None:
            raise SystemExit(f"Could not find {binary_name()} inside the archive.")

        shutil.copy2(extracted, target)

        # Copy geoip.dat / geosite.dat when the archive contains them.  They are
        # not needed by the scanner, but some users also reuse this binary.
        for extra in ("geoip.dat", "geosite.dat"):
            source = extracted.parent / extra
            if source.exists():
                shutil.copy2(source, bin_dir / extra)

    if os.name != "nt":
        target.chmod(target.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

    print(f"Installed: {target}")
    print(version_of(target))
    return 0


if __name__ == "__main__":
    sys.exit(main())
