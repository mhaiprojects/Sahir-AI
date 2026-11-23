"""Schema migration, General project bootstrap, per-chat mode, dispatcher routing."""

from __future__ import annotations

import sqlite3

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from agent.dispatcher import agent_mode_to_ui, dispatch_voice_or_text, effective_mode, storage_mode_to_agent
from agent.loop import Agent
from agent.store import SCHEMA, AgentStore, utcnow
from agent.migrations import STORE_SCHEMA_VERSION
from config import Settings
from tests.conftest import FakeEmbedder, FakeLLM
from web.agent_api import create_agent_router


def _legacy_db(path) -> None:
    """Pre-migration schema (conversations only) with one chat row."""
    conn = sqlite3.connect(path)
    try:
        conn.executescript(SCHEMA)
        now = utcnow()
        conn.execute(
            "INSERT INTO conversations (title, created_at, updated_at, source) VALUES (?,?,?,?)",
            ("Legacy chat", now, now, "web"),
        )
        conn.commit()
    finally:
        conn.close()


def test_fresh_db_migration_and_general_project(tmp_path) -> None:
    store = AgentStore(tmp_path / "fresh.db")
    stats = store.stats()
    assert stats["schema_version"] == STORE_SCHEMA_VERSION
    assert stats["projects"] == 1
    general = store.get_project_by_slug("general")
    assert general and general["slug"] == "general"
    sup = store.get_conversation(general["supervisor_conversation_id"])
    assert sup and sup["kind"] == "supervisor" and sup["project_id"] == general["id"]


def test_legacy_db_migrates_idempotently(tmp_path) -> None:
    db = tmp_path / "legacy.db"
    _legacy_db(db)
    store = AgentStore(db)
    convs = store.list_conversations()
    assert len(convs) >= 2  # legacy + supervisor
    legacy = next(c for c in convs if c["title"] == "Legacy chat")
    assert legacy["project_id"] == store.general_project_id()
    assert legacy["kind"] == "chat"
    store2 = AgentStore(db)
    assert store2.stats()["schema_version"] == STORE_SCHEMA_VERSION
    g1 = store.get_project_by_slug("general")
    g2 = store2.get_project_by_slug("general")
    assert g1 and g2 and g1["id"] == g2["id"]


def test_per_chat_mode_get_set(store: AgentStore) -> None:
    c = store.create_conversation("Mode test", mode="route")
    assert c["mode"] == "route"
    updated = store.set_conversation_mode(c["id"], "stream")
    assert updated and updated["mode"] == "chat"
    assert store.get_conversation_mode(c["id"]) == "chat"
    assert storage_mode_to_agent("chat") == "chat"
    assert agent_mode_to_ui("chat") == "stream"


def test_effective_mode_inherits_default(store: AgentStore) -> None:
    c = store.create_conversation(mode="agent")
    assert effective_mode(store, c["id"]) == "agent"
    assert effective_mode(store, None) == "agent"


def test_dispatch_agent_mode_uses_orchestrator(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = AgentStore(tmp_path / "d.db")
    c = store.create_conversation(mode="agent")
    llm = FakeLLM()
    agent = Agent(Settings(), AgentConfig(db_path=tmp_path / "d.db"), store, FakeEmbedder(), llm)

    class Orch:
        def run(self, text, **kwargs):
            from agent.orchestrator import OrchestrationResult

            return OrchestrationResult(reply="from orch", action="answer", action_details={"model": "fake"})

    agent.orchestrator = Orch()
    raw, mode = dispatch_voice_or_text(agent, "hello", conversation_id=c["id"])
    from agent.loop import AgentReply

    assert mode == "agent"
    assert isinstance(raw, AgentReply)
    assert raw.reply == "from orch"
    assert raw.action == "answer"


def test_api_exposes_mode_and_projects(tmp_path, store: AgentStore) -> None:
    agent = Agent(Settings(), AgentConfig(db_path=store.db_path), store, FakeEmbedder(), FakeLLM())
    app = FastAPI()
    app.include_router(create_agent_router(lambda: agent))
    client = TestClient(app)
    proj = client.get("/api/projects").json()
    assert proj["general_project_id"]
    conv = client.post("/api/conversations", json={"title": "x"}).json()
    assert conv.get("project_id") == proj["general_project_id"]
    patched = client.patch(f"/api/conversations/{conv['id']}", json={"mode": "route"}).json()
    assert patched["mode"] == "route"


# noqa for AgentConfig import in test
from agent.config import AgentConfig  # noqa: E402