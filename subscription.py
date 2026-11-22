"""Fetch and normalise subscription content.

The source file used by this project contains a mix of:

* ordinary ``vless://``, ``vmess://``, ``trojan://`` and ``ss://`` links;
* base64-encoded VMess JSON blobs without a ``vmess://`` prefix;
* a few blobs glued directly to the next link (no newline).

The extractor is deliberately defensive and returns every candidate it can
find.  Invalid candidates are rejected later by ``config_parser``.
"""

from __future__ import annotations

import base64
import json
import re
import urllib.request
from dataclasses import dataclass

SCHEME_RE = re.compile(
    r"(?i)(vless|vmess|trojan|ssr|ss|hysteria2|hysteria|hy2|tuic|wireguard|wg)://"
)
BASE64_RE = re.compile(r"^[A-Za-z0-9+/=_-]+$")


@dataclass
class RawConfig:
    uri: str
    source: str = "link"
    name: str = ""


def _decode_base64(value: str) -> bytes:
    value = re.sub(r"\s+", "", value or "")
    value = value.replace("-", "+").replace("_", "/")
    value += "=" * (-len(value) % 4)
    return base64.b64decode(value, validate=False)


def _vmess_from_blob(value: str) -> RawConfig | None:
    value = (value or "").strip()
    if len(value) < 40 or not BASE64_RE.match(value):
        return None
    try:
        decoded = _decode_base64(value).decode("utf-8")
        obj = json.loads(decoded)
    except Exception:
        return None
    if not isinstance(obj, dict):
        return None
    if not (obj.get("add") and obj.get("id") and obj.get("port")):
        return None
    compact = json.dumps(obj, ensure_ascii=False, separators=(",", ":"))
    uri = "vmess://" + base64.b64encode(compact.encode("utf-8")).decode("ascii")
    return RawConfig(uri=uri, source="base64-vmess", name=str(obj.get("ps") or ""))


def _looks_like_whole_subscription(text: str) -> str:
    """Decode a base64-encoded subscription when no scheme is visible."""
    stripped = text.strip()
    if not stripped or "://" in stripped:
        return text
    try:
        decoded = _decode_base64(stripped).decode("utf-8")
    except Exception:
        return text
    if "://" in decoded:
        return decoded
    return text


def extract_raw_configs(text: str) -> list[RawConfig]:
    text = text.replace("\r\n", "\n").replace("\r", "\n").lstrip("\ufeff")
    text = _looks_like_whole_subscription(text)

    result: list[RawConfig] = []
    for line in text.split("\n"):
        line = line.strip()
        if not line:
            continue

        matches = list(SCHEME_RE.finditer(line))
        if not matches:
            candidate = _vmess_from_blob(line)
            if candidate:
                result.append(candidate)
            continue

        # A base64 VMess blob can be glued to the front of a normal link.
        prefix = line[: matches[0].start()].strip()
        if prefix:
            candidate = _vmess_from_blob(prefix)
            if candidate:
                result.append(candidate)

        for index, match in enumerate(matches):
            end = matches[index + 1].start() if index + 1 < len(matches) else len(line)
            uri = line[match.start() : end].strip()
            if uri:
                result.append(RawConfig(uri=uri, source="link"))
    return result


def fetch_subscription(url: str, timeout: float = 30.0, retries: int = 3) -> str:
    last_error: Exception | None = None
    for attempt in range(1, retries + 1):
        request = urllib.request.Request(
            url,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) configscaner/1.0",
                "Cache-Control": "no-cache",
                "Pragma": "no-cache",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                data = response.read()
            return data.decode("utf-8", errors="replace")
        except Exception as exc:  # pragma: no cover - network diagnostic path
            last_error = exc
            if attempt < retries:
                import time

                time.sleep(min(2 * attempt, 8))
    raise RuntimeError(f"could not download subscription after {retries} attempts: {last_error}")
