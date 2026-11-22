"""Parse V2Ray-style share links into Xray-core outbound JSON.

The public subscription this project targets is not perfectly formed: some
entries are base64 JSON blobs without a ``vmess://`` prefix, and a few blobs are
glued to the next link.  ``subscription.py`` normalises those cases and this
module only deals with one share link at a time.

Only protocols supported by Xray-core are translated.  Unsupported links are
reported to the caller instead of being silently ignored.
"""

from __future__ import annotations

import base64
import hashlib
import ipaddress
import json
import re
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import parse_qs, unquote, urlsplit


class ParseError(ValueError):
    """The share link cannot be translated into a usable Xray outbound."""


@dataclass
class ParsedConfig:
    uri: str
    protocol: str
    host: str
    port: int
    name: str
    outbound: dict[str, Any]
    key: str
    warnings: list[str] = field(default_factory=list)

    @property
    def display_name(self) -> str:
        return self.name or f"{self.protocol}://{self.host}:{self.port}"


# Xray-core 25.x is pinned by bootstrap.py.  It still accepts allowInsecure,
# which is used by a large part of the public subscription.  Xray 26 removed it.
SUPPORTED_PROTOCOLS = {"vless", "vmess", "trojan", "ss"}

NETWORK_ALIASES = {
    "tcp": "tcp",
    "raw": "tcp",
    "ws": "ws",
    "websocket": "ws",
    "grpc": "grpc",
    "gun": "grpc",
    "http": "http",
    "h2": "http",
    "h2c": "http",
    "kcp": "kcp",
    "mkcp": "kcp",
    "quic": "quic",
    "xhttp": "xhttp",
    "splithttp": "xhttp",
    "httpupgrade": "httpupgrade",
}

SECURITY_ALIASES = {
    "": "none",
    "none": "none",
    "tls": "tls",
    "xtls": "tls",
    "reality": "reality",
}

VALID_FLOWS = {"xtls-rprx-vision", "xtls-rprx-vision-udp443"}
VALID_PACKET_ENCODINGS = {"xudp", "packet", "none"}
VALID_VMESS_SECURITY = {"auto", "aes-128-gcm", "chacha20-poly1305", "none", "zero"}

# uTLS fingerprints accepted by Xray 25.x.  Unknown/custom names are mapped to
# chrome so a single odd fingerprint cannot make an otherwise good node fail.
VALID_FINGERPRINTS = {
    "chrome",
    "firefox",
    "safari",
    "ios",
    "android",
    "edge",
    "360",
    "qq",
    "random",
    "randomized",
    "unsafe",
}

SS_METHODS = {
    "aes-128-gcm",
    "aes-256-gcm",
    "chacha20-poly1305",
    "chacha20-ietf-poly1305",
    "xchacha20-poly1305",
    "xchacha20-ietf-poly1305",
    "2022-blake3-aes-128-gcm",
    "2022-blake3-aes-256-gcm",
    "2022-blake3-chacha20-poly1305",
    "none",
    "plain",
}


def _qget(query: dict[str, list[str]], *names: str, default: str = "") -> str:
    for name in names:
        values = query.get(name)
        if values:
            value = values[0]
            if value is not None:
                return value
    return default


def _bool(value: Any) -> bool:
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return default


def _split_alpn(value: Any) -> list[str]:
    if isinstance(value, (list, tuple)):
        result = [str(item).strip() for item in value if str(item).strip()]
    else:
        result = [item.strip() for item in str(value or "").split(",") if item.strip()]
    return result


def _clean_host_header(value: str) -> str:
    value = (value or "").strip()
    # Some share links put a path/query into the host parameter.  Only the
    # authority part is a valid Host header.
    value = value.split("/", 1)[0].split("?", 1)[0].strip()
    if value.startswith("[") and value.endswith("]"):
        value = value[1:-1]
    return value


def _is_ip(host: str) -> bool:
    try:
        ipaddress.ip_address(host)
        return True
    except ValueError:
        return False


def is_private_host(host: str) -> bool:
    host = (host or "").strip().lower()
    if host in {"localhost", "localhost.localdomain"}:
        return True
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False
    return (
        address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_multicast
        or address.is_reserved
        or address.is_unspecified
    )


def _fingerprint(value: str) -> str:
    value = (value or "").strip().lower()
    if not value:
        return ""
    if value in VALID_FINGERPRINTS:
        return value
    # Custom uTLS names (for example hellochrome_120) are not guaranteed to be
    # accepted by every Xray build.  chrome is the safest fallback.
    if value.startswith("hellochrome") or value.startswith("hello"):
        return "chrome"
    return "chrome"


def _security(value: str) -> str:
    return SECURITY_ALIASES.get((value or "").strip().lower(), "none")


def _network(value: str, default: str = "tcp") -> str:
    key = (value or default).strip().lower()
    try:
        return NETWORK_ALIASES[key]
    except KeyError as exc:
        raise ParseError(f"unsupported transport: {value}") from exc


def _build_stream(
    *,
    network: str,
    security: str,
    address: str,
    host_header: str = "",
    path: str = "",
    sni: str = "",
    alpn: list[str] | None = None,
    fingerprint: str = "",
    allow_insecure: bool = False,
    header_type: str = "",
    service_name: str = "",
    mode: str = "",
    max_early_data: str = "",
    early_data_header_name: str = "",
    public_key: str = "",
    short_id: str = "",
    spider_x: str = "",
    seed: str = "",
    quic_security: str = "",
    quic_key: str = "",
) -> dict[str, Any]:
    alpn = alpn or []
    host_header = _clean_host_header(host_header)

    if security == "tls":
        # Do not silently add allowInsecure here.  The published share link
        # would not contain that option, so a node that only passed because we
        # disabled certificate checks might fail in PassWall.  Honour only the
        # explicit option from the source link.
        if not sni and host_header and not _is_ip(host_header):
            sni = host_header
        if not sni and not _is_ip(address):
            sni = address
    elif security == "reality":
        if not public_key:
            raise ParseError("reality link is missing pbk/publicKey")
        if not sni:
            if host_header and not _is_ip(host_header):
                sni = host_header
            elif not _is_ip(address):
                sni = address
        if not sni:
            raise ParseError("reality link is missing serverName/sni")

    stream: dict[str, Any] = {"network": network, "security": security}

    if security == "tls":
        tls_settings: dict[str, Any] = {}
        if sni:
            tls_settings["serverName"] = sni
        if alpn:
            tls_settings["alpn"] = alpn
        if fingerprint:
            tls_settings["fingerprint"] = _fingerprint(fingerprint)
        if allow_insecure:
            tls_settings["allowInsecure"] = True
        stream["tlsSettings"] = tls_settings
    elif security == "reality":
        reality_settings: dict[str, Any] = {
            "serverName": sni,
            "fingerprint": _fingerprint(fingerprint) or "chrome",
            "publicKey": public_key,
            "shortId": short_id or "",
            "spiderX": spider_x or "/",
        }
        stream["realitySettings"] = reality_settings

    if network == "ws":
        ws_settings: dict[str, Any] = {"path": path or "/"}
        if host_header:
            ws_settings["host"] = host_header
        if max_early_data:
            ws_settings["maxEarlyData"] = _int(max_early_data)
        if early_data_header_name:
            ws_settings["earlyDataHeaderName"] = early_data_header_name
        stream["wsSettings"] = ws_settings
    elif network == "grpc":
        grpc_settings: dict[str, Any] = {}
        service = (service_name or path or "").strip().lstrip("/")
        if service:
            grpc_settings["serviceName"] = service
        if host_header:
            grpc_settings["authority"] = host_header
        stream["grpcSettings"] = grpc_settings
    elif network == "http":
        http_settings: dict[str, Any] = {"path": path or "/"}
        if host_header:
            http_settings["host"] = [host_header]
        stream["httpSettings"] = http_settings
    elif network == "xhttp":
        xhttp_settings: dict[str, Any] = {"path": path or "/"}
        if host_header:
            xhttp_settings["host"] = host_header
        if mode:
            xhttp_settings["mode"] = mode
        stream["xhttpSettings"] = xhttp_settings
    elif network == "httpupgrade":
        upgrade_settings: dict[str, Any] = {"path": path or "/"}
        if host_header:
            upgrade_settings["host"] = host_header
        stream["httpupgradeSettings"] = upgrade_settings
    elif network == "kcp":
        kcp_settings: dict[str, Any] = {
            "header": {"type": header_type or "none"},
        }
        if seed:
            kcp_settings["seed"] = seed
        stream["kcpSettings"] = kcp_settings
    elif network == "quic":
        stream["quicSettings"] = {
            "security": quic_security or "none",
            "key": quic_key or "",
            "header": {"type": header_type or "none"},
        }
    elif network == "tcp":
        if header_type.lower() == "http":
            request: dict[str, Any] = {"path": [path or "/"]}
            if host_header:
                request["headers"] = {"Host": [host_header]}
            stream["tcpSettings"] = {
                "header": {
                    "type": "http",
                    "request": request,
                }
            }

    return stream


def _raw_userinfo(parts: Any) -> str:
    netloc = parts.netloc
    if "@" not in netloc:
        return ""
    return unquote(netloc.rsplit("@", 1)[0])


def _base_name(parts: Any, fallback: str) -> str:
    name = unquote(parts.fragment or "").strip()
    name = name.replace("\r", " ").replace("\n", " ")
    name = re.sub(r"\s+", " ", name)
    return name or fallback


def _b64decode_any(value: str) -> bytes:
    value = re.sub(r"\s+", "", value or "")
    value = value.replace("-", "+").replace("_", "/")
    value += "=" * (-len(value) % 4)
    return base64.b64decode(value, validate=False)


def _common_query(parts: Any) -> dict[str, list[str]]:
    return parse_qs(parts.query, keep_blank_values=True)


def _stream_from_query(
    *,
    parts: Any,
    query: dict[str, list[str]],
    address: str,
    network: str,
    security: str,
) -> dict[str, Any]:
    host_header = _qget(query, "host", "headerHost", "hostHeader")
    path = _qget(query, "path", "serviceName")
    sni = _qget(query, "sni", "serverName", "peer", "servername")
    alpn = _split_alpn(_qget(query, "alpn"))
    fingerprint = _qget(query, "fp", "fingerprint")
    allow_insecure = _bool(
        _qget(query, "allowInsecure", "allowinsecure", "insecure", "skip-cert-verify", "skipCertVerify")
    )
    header_type = _qget(query, "headerType", "header_type")
    service_name = _qget(query, "serviceName", "service_name")
    mode = _qget(query, "mode")
    max_early_data = _qget(query, "max_early_data", "maxEarlyData")
    early_data_header_name = _qget(query, "early_data_header_name", "earlyDataHeaderName")
    public_key = _qget(query, "pbk", "publicKey", "public_key")
    short_id = _qget(query, "sid", "shortId", "short_id")
    spider_x = _qget(query, "spx", "spiderX", "spider_x")
    seed = _qget(query, "seed")
    quic_security = _qget(query, "quicSecurity")
    quic_key = _qget(query, "key")

    return _build_stream(
        network=network,
        security=security,
        address=address,
        host_header=host_header,
        path=path,
        sni=sni,
        alpn=alpn,
        fingerprint=fingerprint,
        allow_insecure=allow_insecure,
        header_type=header_type,
        service_name=service_name,
        mode=mode,
        max_early_data=max_early_data,
        early_data_header_name=early_data_header_name,
        public_key=public_key,
        short_id=short_id,
        spider_x=spider_x,
        seed=seed,
        quic_security=quic_security,
        quic_key=quic_key,
    )


def _host_and_port(parts: Any) -> tuple[str, int]:
    host = (parts.hostname or "").strip().lower().rstrip(".")
    if not host:
        raise ParseError("missing server address")
    try:
        port = parts.port
    except ValueError as exc:
        raise ParseError(f"invalid port: {exc}") from exc
    if port is None:
        raise ParseError("missing port")
    if not (1 <= port <= 65535):
        raise ParseError(f"port out of range: {port}")
    return host, port


def _outbound(protocol: str, settings: dict[str, Any], stream: dict[str, Any]) -> dict[str, Any]:
    return {
        "tag": "proxy",
        "protocol": protocol,
        "settings": settings,
        "streamSettings": stream,
    }


def _make_config(
    uri: str,
    protocol: str,
    host: str,
    port: int,
    name: str,
    outbound: dict[str, Any],
    warnings: list[str] | None = None,
) -> ParsedConfig:
    canonical = json.loads(json.dumps(outbound))
    canonical.pop("tag", None)
    key = hashlib.sha256(
        json.dumps(canonical, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return ParsedConfig(
        uri=uri,
        protocol=protocol,
        host=host,
        port=port,
        name=name,
        outbound=outbound,
        key=key,
        warnings=warnings or [],
    )


def parse_vless(uri: str) -> ParsedConfig:
    parts = urlsplit(uri)
    host, port = _host_and_port(parts)
    user_id = unquote(parts.username or "").strip()
    if not user_id:
        raise ParseError("vless link has no id")

    query = _common_query(parts)
    network = _network(_qget(query, "type", "network", "net", default="tcp"))
    security = _security(_qget(query, "security", "tls", default="none"))
    stream = _stream_from_query(
        parts=parts,
        query=query,
        address=host,
        network=network,
        security=security,
    )

    user: dict[str, Any] = {"id": user_id, "encryption": "none"}
    flow = _qget(query, "flow").strip()
    if flow in VALID_FLOWS:
        user["flow"] = flow

    settings: dict[str, Any] = {
        "vnext": [
            {
                "address": host,
                "port": port,
                "users": [user],
            }
        ]
    }
    packet_encoding = _qget(query, "packetEncoding", "packet_encoding").strip().lower()
    if packet_encoding in VALID_PACKET_ENCODINGS:
        settings["packetEncoding"] = packet_encoding

    warnings: list[str] = []
    if flow and flow not in VALID_FLOWS:
        warnings.append(f"ignored unknown flow {flow!r}")

    return _make_config(
        uri=uri,
        protocol="vless",
        host=host,
        port=port,
        name=_base_name(parts, f"vless://{host}:{port}"),
        outbound=_outbound("vless", settings, stream),
        warnings=warnings,
    )


def parse_vmess(uri: str) -> ParsedConfig:
    payload = uri[len("vmess://") :].strip()
    payload = payload.split("#", 1)[0].strip()
    try:
        decoded = _b64decode_any(unquote(payload)).decode("utf-8")
    except Exception as exc:
        raise ParseError(f"vmess payload is not valid base64: {exc}") from exc
    try:
        obj = json.loads(decoded)
    except json.JSONDecodeError as exc:
        raise ParseError(f"vmess payload is not valid JSON: {exc}") from exc
    if not isinstance(obj, dict):
        raise ParseError("vmess payload is not a JSON object")

    address = str(obj.get("add") or obj.get("address") or "").strip().lower().rstrip(".")
    if not address:
        raise ParseError("vmess link has no server address")
    try:
        port = int(str(obj.get("port") or "").strip())
    except ValueError as exc:
        raise ParseError(f"vmess link has invalid port: {obj.get('port')!r}") from exc
    if not (1 <= port <= 65535):
        raise ParseError(f"vmess port out of range: {port}")

    user_id = str(obj.get("id") or "").strip()
    if not user_id:
        raise ParseError("vmess link has no id")

    network = _network(str(obj.get("net") or "tcp"))
    tls_value = str(obj.get("tls") or "").strip().lower()
    if tls_value in {"tls", "reality"}:
        security = tls_value
    else:
        security = "none"

    host_header = str(obj.get("host") or obj.get("sni") or "")
    path = str(obj.get("path") or "")
    sni = str(obj.get("sni") or "")
    alpn = _split_alpn(obj.get("alpn"))
    fingerprint = str(obj.get("fp") or "")
    allow_insecure = _bool(
        obj.get("skip-cert-verify") or obj.get("insecure") or obj.get("allowInsecure")
    )
    header_type = ""
    if network in {"tcp", "kcp", "quic"}:
        header_type = str(obj.get("type") or "")
    service_name = str(obj.get("serviceName") or "")
    mode = str(obj.get("mode") or "")
    max_early_data = str(obj.get("max_early_data") or obj.get("maxEarlyData") or "")
    early_data_header_name = str(
        obj.get("early_data_header_name") or obj.get("earlyDataHeaderName") or ""
    )
    public_key = str(obj.get("pbk") or obj.get("publicKey") or "")
    short_id = str(obj.get("sid") or obj.get("shortId") or "")
    spider_x = str(obj.get("spx") or obj.get("spiderX") or "")
    seed = str(obj.get("seed") or "")
    quic_security = str(obj.get("quicSecurity") or "")
    quic_key = str(obj.get("key") or "")

    stream = _build_stream(
        network=network,
        security=security,
        address=address,
        host_header=host_header,
        path=path,
        sni=sni,
        alpn=alpn,
        fingerprint=fingerprint,
        allow_insecure=allow_insecure,
        header_type=header_type,
        service_name=service_name,
        mode=mode,
        max_early_data=max_early_data,
        early_data_header_name=early_data_header_name,
        public_key=public_key,
        short_id=short_id,
        spider_x=spider_x,
        seed=seed,
        quic_security=quic_security,
        quic_key=quic_key,
    )

    alter_id = _int(obj.get("aid"), 0)
    if alter_id < 0:
        alter_id = 0
    vmess_security = str(obj.get("scy") or obj.get("security") or "auto").strip().lower()
    if vmess_security not in VALID_VMESS_SECURITY:
        vmess_security = "auto"

    user = {
        "id": user_id,
        "alterId": alter_id,
        "security": vmess_security,
    }
    settings: dict[str, Any] = {
        "vnext": [
            {
                "address": address,
                "port": port,
                "users": [user],
            }
        ]
    }
    packet_encoding = str(obj.get("packetEncoding") or obj.get("packet_encoding") or "").strip().lower()
    if packet_encoding in VALID_PACKET_ENCODINGS:
        settings["packetEncoding"] = packet_encoding

    name = str(obj.get("ps") or obj.get("name") or "").strip()
    name = re.sub(r"\s+", " ", name) or f"vmess://{address}:{port}"

    return _make_config(
        uri=uri,
        protocol="vmess",
        host=address,
        port=port,
        name=name,
        outbound=_outbound("vmess", settings, stream),
    )


def parse_trojan(uri: str) -> ParsedConfig:
    parts = urlsplit(uri)
    host, port = _host_and_port(parts)
    password = _raw_userinfo(parts).strip()
    if not password:
        raise ParseError("trojan link has no password")

    query = _common_query(parts)
    network = _network(_qget(query, "type", "network", "net", default="tcp"))
    security_value = _qget(query, "security", default="tls").strip().lower()
    security = _security(security_value or "tls")
    stream = _stream_from_query(
        parts=parts,
        query=query,
        address=host,
        network=network,
        security=security,
    )

    settings: dict[str, Any] = {
        "servers": [
            {
                "address": host,
                "port": port,
                "password": password,
            }
        ]
    }
    return _make_config(
        uri=uri,
        protocol="trojan",
        host=host,
        port=port,
        name=_base_name(parts, f"trojan://{host}:{port}"),
        outbound=_outbound("trojan", settings, stream),
    )


def _parse_ss_userinfo(raw_userinfo: str) -> tuple[str, str]:
    raw_userinfo = raw_userinfo.strip()
    if not raw_userinfo:
        raise ParseError("ss link has no credentials")
    if ":" in raw_userinfo:
        method, password = raw_userinfo.split(":", 1)
        return method.strip().lower(), unquote(password)
    try:
        decoded = _b64decode_any(raw_userinfo).decode("utf-8")
    except Exception as exc:
        raise ParseError(f"ss credentials are not valid base64: {exc}") from exc
    if ":" not in decoded:
        raise ParseError("ss credentials do not contain method:password")
    method, password = decoded.split(":", 1)
    return method.strip().lower(), password


def parse_ss(uri: str) -> ParsedConfig:
    parts = urlsplit(uri)
    query = _common_query(parts)
    plugin = _qget(query, "plugin")
    if plugin:
        raise ParseError("shadowsocks plugin is not supported")

    raw_userinfo = _raw_userinfo(parts)
    if raw_userinfo:
        method, password = _parse_ss_userinfo(raw_userinfo)
        host, port = _host_and_port(parts)
    else:
        # Legacy SIP002 variant: ss://base64(method:password@host:port)
        try:
            decoded = _b64decode_any(parts.netloc).decode("utf-8")
            credentials, host_port = decoded.rsplit("@", 1)
            method, password = credentials.split(":", 1)
            method = method.strip().lower()
            host_text, port_text = host_port.rsplit(":", 1)
            host = host_text.strip().strip("[]").lower().rstrip(".")
            port = int(port_text)
        except Exception as exc:
            raise ParseError(f"could not parse legacy ss link: {exc}") from exc
        if not host or not (1 <= port <= 65535):
            raise ParseError("legacy ss link has an invalid server address")

    if method not in SS_METHODS:
        raise ParseError(f"unsupported shadowsocks method: {method}")
    if not password:
        raise ParseError("ss link has no password")

    settings: dict[str, Any] = {
        "servers": [
            {
                "address": host,
                "port": port,
                "method": method,
                "password": password,
            }
        ]
    }
    stream = {"network": "tcp", "security": "none"}
    return _make_config(
        uri=uri,
        protocol="ss",
        host=host,
        port=port,
        name=_base_name(parts, f"ss://{host}:{port}"),
        outbound=_outbound("shadowsocks", settings, stream),
    )


def parse_config(uri: str) -> ParsedConfig:
    uri = uri.strip()
    scheme = uri.split("://", 1)[0].lower() if "://" in uri else ""
    if scheme == "vless":
        return parse_vless(uri)
    if scheme == "vmess":
        return parse_vmess(uri)
    if scheme == "trojan":
        return parse_trojan(uri)
    if scheme == "ss":
        return parse_ss(uri)
    raise ParseError(f"unsupported protocol: {scheme or 'unknown'}")
