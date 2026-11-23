"""Item 11: Hands-free on /ws/voice; retired HTTP always-listening path."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import web_server
from agent.config import AgentConfig
from agent.factory import AgentStack
from agent.loop import Agent
from agent.store import AgentStore
from config import Settings
from tests.conftest import FakeEmbedder, FakeLLM


def _read_static(name: str) -> str:
    root = Path(__file__).resolve().parents[1] / "web" / "static"
    return (root / name).read_text(encoding="utf-8")


def test_html_single_hands_free_controls() -> None:
    html = _read_static("index.html")
    app_js = _read_static("app.js")
    voice_js = _read_static("voice_client.js")
    assert html.count('id="hands-free-toggle"') == 1
    assert 'id="hands-free-stop"' in html
    assert "voice_client.js" in html
    assert "always-listening" not in app_js
    assert "startAlwaysListening" not in app_js
    assert "/ws/voice" in voice_js
    assert "open-live-voice-btn" not in html
    assert 'id="voice-toggle"' not in html


def test_transcribe_rejects_always_mode(client: TestClient) -> None:
    tiny = b"\x00" * 64
    res = client.post(
        "/api/transcribe?mode=always&route=false",
        files={"audio": ("x.webm", tiny, "audio/webm")},
    )
    assert res.status_code == 400
    assert "ws/voice" in res.json()["detail"].lower()


@pytest.fixture
def client(tmp_path, monkeypatch):
    from tests.test_agent_api import FakePipeline

    settings = Settings()
    settings.web_db_path = tmp_path / "logs.db"
    cfg = AgentConfig(db_path=tmp_path / "agent.db", auto_memory=False)
    store = AgentStore(cfg.db_path)
    agent = Agent(settings, cfg, store, FakeEmbedder(), FakeLLM())
    stack = AgentStack(agent, None, None)
    monkeypatch.setattr(web_server, "_settings", settings)
    monkeypatch.setattr(web_server, "_agent_stack", stack)
    monkeypatch.setattr(web_server, "_pipeline", FakePipeline())
    monkeypatch.setattr(web_server, "_log_store", None)
    return TestClient(web_server.create_app())