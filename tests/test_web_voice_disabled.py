"""Minimal Docker image: voice endpoints unavailable when WEB_VOICE_ENABLED=false."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import web_server


def _reset() -> None:
    web_server._settings = None
    web_server._pipeline = None
    import web.auth as auth_mod

    auth_mod._service = None


def test_voice_endpoints_503_when_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WEB_AUTH_ENABLED", "false")
    monkeypatch.setenv("WEB_VOICE_ENABLED", "false")
    _reset()
    client = TestClient(web_server.create_app())
    health = client.get("/api/health").json()
    assert health.get("voice_stack", {}).get("enabled") is False
    assert health["whisper"]["ok"] is False
    assert client.post("/api/speak", json={"message": "hi"}).status_code == 503