"""Wyoming server environment configuration."""

from __future__ import annotations

import os
import socket
from dataclasses import dataclass
from urllib.parse import urlparse

from dotenv import load_dotenv

from config import PROJECT_ROOT

load_dotenv(PROJECT_ROOT / ".env")

DEFAULT_URI = "tcp://127.0.0.1:10300"
SAHIR_ATTRIBUTION_NAME = "Sahir AI"


def _bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or not str(raw).strip():
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


@dataclass
class WyomingSettings:
    uri: str = DEFAULT_URI
    allow_lan: bool = False
    handle_enabled: bool = False
    max_audio_seconds: float = 30.0
    project_name: str = "Wyoming"
    conversation_title: str = "Home Assistant"

    @classmethod
    def from_env(cls) -> "WyomingSettings":
        return cls(
            uri=(os.getenv("WYOMING_URI") or DEFAULT_URI).strip(),
            allow_lan=_bool("WYOMING_ALLOW_LAN", False),
            handle_enabled=_bool("WYOMING_HANDLE_ENABLED", False),
            max_audio_seconds=float(os.getenv("WYOMING_MAX_AUDIO_SECONDS", "30")),
            project_name=(os.getenv("WYOMING_PROJECT_NAME") or "Wyoming").strip()[:120],
            conversation_title=(os.getenv("WYOMING_CONVERSATION_TITLE") or "Home Assistant").strip()[:200],
        )


def parse_tcp_bind(uri: str) -> tuple[str, int]:
    parsed = urlparse(uri)
    if parsed.scheme != "tcp":
        raise ValueError(f"WYOMING_URI must use tcp:// (got {uri!r})")
    if not parsed.hostname or parsed.port is None:
        raise ValueError(f"tcp URI must include host and port: {uri!r}")
    return parsed.hostname, int(parsed.port)


def host_requires_lan_consent(host: str) -> bool:
    """True when the bind address is not loopback-only (needs WYOMING_ALLOW_LAN)."""
    if host in {"127.0.0.1", "localhost", "::1"}:
        return False
    if host == "0.0.0.0":
        return True
    try:
        addr = socket.gethostbyname(host)
    except OSError:
        return True
    return not addr.startswith("127.")


def validate_bind(settings: WyomingSettings) -> tuple[str, int]:
    host, port = parse_tcp_bind(settings.uri)
    if host_requires_lan_consent(host) and not settings.allow_lan:
        raise SystemExit(
            f"Refusing to bind Wyoming server to {host}:{port} without WYOMING_ALLOW_LAN=true. "
            "Wyoming has no shared-secret auth; use loopback or secure the network."
        )
    return host, port