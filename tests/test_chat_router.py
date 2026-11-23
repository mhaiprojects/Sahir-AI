"""Table-driven tests for agent/chat_router Tier 0 + sticky default."""

from __future__ import annotations

from pathlib import Path

import pytest

from agent.chat_router import (
    STICKY_CONFIDENCE,
    RouteContext,
    build_route_context,
    route,
    try_tier0_voice_control,
    typed_should_route_tier0,
)
from agent.config import AgentConfig
from agent.store import AgentStore
from web.commands import VoiceCommandStore


@pytest.fixture
def router_env(tmp_path):
    db = tmp_path / "agent.db"
    store = AgentStore(db)
    proj = store.get_project_by_slug("general")
    pid = int(proj["id"])
    sup_id = int(proj["supervisor_conversation_id"])
    a = store.create_conversation("Groceries", project_id=pid, kind="sub", parent_id=sup_id)
    b = store.create_conversation("Backend", project_id=pid, kind="sub", parent_id=sup_id)
    store.set_conversation_aliases(int(b["id"]), ["api"])
    cmd_db = tmp_path / "cmds.db"
    cmds = VoiceCommandStore(cmd_db)
    return store, cmds, pid, sup_id, int(a["id"]), int(b["id"])


def _ctx(store, sup_id, active_id, chats, *, input_type="voice", cmds=None, tier0=True, allow_run=False):
    return RouteContext(
        active_project_id=int(store.get_project_by_slug("general")["id"]),
        active_chat_id=active_id,
        supervisor_chat_id=sup_id,
        chats=chats,
        input_type=input_type,
        command_store=cmds,
        tier0_enabled=tier0,
        allow_run_command=allow_run,
    )


def test_chat_note_routes_supervisor(router_env):
    store, cmds, _pid, sup_id, a_id, _b_id = router_env
    chats = [{"id": a_id, "title": "Groceries", "aliases": []}]
    ctx = _ctx(store, sup_id, a_id, chats, cmds=cmds)
    d = route("refactor note: start with auth", ctx, store=store)
    assert d.label == "D"
    assert d.target_chat_id == sup_id


@pytest.mark.parametrize(
    "utterance,label,command",
    [
        ("switch to groceries", "S", "switch_chat"),
        ("open backend", "S", "switch_chat"),
        ("go back to groceries", "S", "switch_chat"),
        ("go to the supervisor", "S", "switch_chat"),
        ("close the chat", "S", "switch_chat"),
        ("new chat", "S", "new_chat"),
        ("stop speaking", "S", "stop_speech"),
        ("Supervisor, what chats do we have?", "D", None),
        ("Sahir, summarise everything", "D", None),
        ("tell the api chat add logging", "C", None),
        ("run uptime", "S", "run_command"),
        ("stop", "S", "stop_speech"),
        ("pause listening", "S", "pause_listening"),
    ],
)
def test_tier0_patterns(router_env, utterance, label, command):
    store, cmds, _pid, sup_id, a_id, b_id = router_env
    chats = [
        {"id": a_id, "title": "Groceries", "aliases": []},
        {"id": b_id, "title": "Backend", "aliases": ["api"]},
    ]
    ctx = _ctx(store, sup_id, a_id, chats, cmds=cmds)
    d = route(utterance, ctx, store=store)
    assert d.label == label
    assert d.tier == 0
    if command:
        assert d.command == command
    assert d.ms < 5.0, f"Tier 0 took {d.ms} ms"


def test_sticky_default(router_env):
    store, cmds, _pid, sup_id, a_id, b_id = router_env
    chats = [{"id": a_id, "title": "Groceries", "aliases": []}]
    ctx = _ctx(store, sup_id, a_id, chats, cmds=cmds)
    d = route("remind me to buy milk", ctx, store=store)
    assert d.label == "C"
    assert d.confidence == STICKY_CONFIDENCE
    assert d.target_chat_id == a_id
    assert d.payload.get("sticky")


def test_typed_bypass_tier0(router_env):
    store, _cmds, _pid, sup_id, a_id, _b_id = router_env
    chats = [{"id": a_id, "title": "Groceries", "aliases": []}]
    ctx = _ctx(store, sup_id, a_id, chats, input_type="text", tier0=False)
    d = route("switch to backend", ctx, store=store)
    assert d.label == "C" and d.confidence == STICKY_CONFIDENCE
    assert typed_should_route_tier0("/sup hello")
    assert typed_should_route_tier0("/switch groceries")
    assert typed_should_route_tier0("@api ping")


def test_typed_run_on_legacy(router_env):
    store, _cmds, _pid, sup_id, a_id, _b_id = router_env
    chats = [{"id": a_id, "title": "Groceries", "aliases": []}]
    ctx = _ctx(store, sup_id, a_id, chats, input_type="text", tier0=False, allow_run=True)
    d = route("run uptime", ctx, store=store)
    assert d.label == "S" and d.command == "run_command"
    assert d.payload["command"] == "uptime"


def test_route_event_published(router_env, monkeypatch):
    store, cmds, _pid, sup_id, a_id, _b_id = router_env
    published = []

    def fake_publish(kind, message, **kwargs):
        published.append((kind, kwargs))

    monkeypatch.setattr("agent.chat_router.publish", fake_publish)
    chats = [{"id": a_id, "title": "Groceries", "aliases": []}]
    ctx = _ctx(store, sup_id, a_id, chats, cmds=cmds)
    d = route("stop", ctx, store=store)
    assert d.ms < 5.0
    assert published and published[0][0] == "route"
    kw = published[0][1]
    assert kw["label"] == "S"
    assert kw["tier"] == 0
    assert "ms" in kw


def test_legacy_route_tier0_run_command(router_env, tmp_path, monkeypatch):
    from agent.dispatcher import legacy_route_tier0
    from agent.loop import Agent
    from config import Settings
    from tests.conftest import FakeEmbedder, FakeLLM

    store, cmds, _pid, _sup_id, a_id, _b_id = router_env
    settings = Settings()
    agent = Agent(settings, AgentConfig(db_path=store.db_path), store, FakeEmbedder(), FakeLLM())
    monkeypatch.setattr(
        "tools.pc_control.run_command",
        lambda cmd, args, _s: type("R", (), {"message": "ok", "success": True})(),
    )
    result = legacy_route_tier0(agent, "run echo hello", conversation_id=a_id, command_store=cmds)
    assert result is not None
    assert result.action == "run_command"


@pytest.mark.parametrize(
    "utterance,command",
    [
        ("stop", "stop_speech"),
        ("Stop.", "stop_speech"),
        ("Stop!", "stop_speech"),
        ("stop talking", "stop_speech"),
        ("be quiet", "stop_speech"),
        ("cancel", "cancel_request"),
        ("Top.", "stop_speech"),
    ],
)
def test_stop_voice_control_always_tier0(router_env, utterance, command):
    store, cmds, _pid, sup_id, a_id, _b_id = router_env
    chats = [{"id": a_id, "title": "Groceries", "aliases": []}]
    ctx = _ctx(store, sup_id, sup_id, chats, cmds=cmds)
    ctl = try_tier0_voice_control(utterance)
    assert ctl is not None and ctl.command == command
    d = route(utterance, ctx, store=store, max_tier=2)
    assert d.label == "S" and d.tier == 0 and d.command == command


def test_build_route_context(router_env):
    store, _cmds, _pid, sup_id, a_id, _b_id = router_env
    ctx = build_route_context(store, a_id)
    assert ctx.active_chat_id == a_id
    assert ctx.supervisor_chat_id == sup_id
    assert any(c["title"] == "Groceries" for c in ctx.chats)