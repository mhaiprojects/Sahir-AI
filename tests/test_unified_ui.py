"""Unified UI shell (item 6): HTML structure and project create API."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from agent.config import AgentConfig
from agent.loop import Agent
from agent.store import AgentStore
from config import Settings
from tests.conftest import FakeEmbedder, FakeLLM
from web.agent_api import create_agent_router


def _index_html() -> str:
    return (Path(__file__).resolve().parents[1] / "web" / "static" / "index.html").read_text(encoding="utf-8")


def test_unified_html_single_controls() -> None:
    html = _index_html()
    assert html.count('id="chat-mode"') == 1
    assert 'id="home-mode"' not in html
    assert 'id="rail-new-chat"' in html
    assert 'id="home-chat-new"' not in html
    assert 'id="conv-new"' not in html
    assert 'id="conv-new-top"' not in html
    assert 'id="speak-replies-toggle"' in html
    assert 'id="voice-tts"' not in html
    assert 'id="cursor-btn"' not in html
    assert 'id="hands-free-toggle"' in html
    assert 'id="project-rail"' in html
    assert 'id="view-chat"' in html
    assert 'id="view-home"' not in html
    assert 'id="view-agent"' not in html
    assert 'id="nav-btn-home"' not in html
    assert 'id="nav-btn-agent"' not in html
    assert 'id="view-settings"' in html
    assert 'id="nav-btn-settings"' in html


def test_create_project_api(tmp_path) -> None:
    store = AgentStore(tmp_path / "ui.db")
    agent = Agent(Settings(), AgentConfig(db_path=store.db_path), store, FakeEmbedder(), FakeLLM())
    app = FastAPI()
    app.include_router(create_agent_router(lambda: agent))
    client = TestClient(app)
    created = client.post("/api/projects", json={"name": "Travel plans"}).json()
    assert created["name"] == "Travel plans"
    assert created["supervisor_conversation_id"]
    listed = client.get("/api/projects").json()["projects"]
    assert any(p["slug"].startswith("travel") for p in listed)
    conv = client.post("/api/conversations", json={"project_id": created["id"]}).json()
    assert conv["project_id"] == created["id"]