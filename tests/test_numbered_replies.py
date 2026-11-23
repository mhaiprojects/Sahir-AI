"""Numbered-list replies: parser, persistence, tier-0 do-action, API execute, UI hooks."""

from __future__ import annotations

from pathlib import Path

pytest_plugins = ["tests.test_agent_api"]
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from agent.chat_router import RouteContext, route
from agent.config import AgentConfig
from agent.loop import Agent
from agent.numbered_actions import begin_do_action, resolve_pending_confirm, set_pending_confirm
from agent.numbered_actions import clear_pending_confirm, get_pending_confirm
from agent.numbered_replies import (
    attach_actions_from_turn,
    build_numbered_reply,
    format_tts,
    parse_confirm_answer,
    parse_do_action_index,
    parse_numbered_text,
    tts_text_for_reply,
)
from agent.store import AgentStore
from config import Settings
from fastapi import FastAPI
from tests.conftest import FakeEmbedder, FakeLLM
from web.agent_api import create_agent_router


def test_parse_numbered_text_variants() -> None:
    assert parse_numbered_text("Hello world") == [{"n": 1, "text": "Hello world", "action": None}]
    multi = parse_numbered_text("1. First point\n2) Second point\n3. Third")
    assert [it["n"] for it in multi] == [1, 2, 3]
    assert multi[0]["text"] == "First point"
    assert multi[1]["text"] == "Second point"
    assert all(it["action"] is None for it in multi)


def test_parser_never_invents_actions() -> None:
    items = parse_numbered_text("1. Run uptime\n2. Open files")
    attached = attach_actions_from_turn(items, action="chat", action_details={})
    assert all(it.get("action") is None for it in attached)


def test_attach_action_from_tool_turn_only() -> None:
    items = parse_numbered_text("1. Check load\n2. Summary")
    out = attach_actions_from_turn(
        items,
        action="run_command",
        action_details={"command": "uptime", "args": []},
    )
    assert out[0]["action"] == {"kind": "run_command", "command": "uptime", "args": []}
    assert out[1].get("action") is None


def test_format_tts_ordinals_and_punctuation() -> None:
    items = [{"n": 1, "text": "Alpha"}, {"n": 3, "text": "Gamma"}]
    assert format_tts(items) == "One: Alpha. Three: Gamma."


def test_single_paragraph_no_tts_or_list_ui() -> None:
    display, items, numbered_ui = build_numbered_reply("Hello world", action="chat", action_details={})
    assert display == "Hello world"
    assert len(items) == 1
    assert numbered_ui is False
    assert tts_text_for_reply(raw_reply="Hello world", items=items) is None


@pytest.mark.parametrize(
    "text,idx",
    [
        ("do action 2", 2),
        ("run item 3", 3),
        ("do number 1", 1),
        ("do the third one", 3),
        ("Do the 2nd one", 2),
        ("run item two", 2),
    ],
)
def test_parse_do_action_index(text: str, idx: int) -> None:
    assert parse_do_action_index(text) == idx


@pytest.mark.parametrize(
    "text,ans",
    [
        ("yes", "yes"),
        ("confirm", "yes"),
        ("do it", "yes"),
        ("no", "no"),
        ("cancel", "no"),
        ("never mind", "no"),
    ],
)
def test_parse_confirm_answer(text: str, ans: str) -> None:
    assert parse_confirm_answer(text) == ans


def test_parse_confirm_rejects_long_no_sentence() -> None:
    assert parse_confirm_answer("no, that was for the supervisor") is None


def test_persistence_in_message_meta(tmp_path) -> None:
    from agent.loop import _save_assistant_reply

    store = AgentStore(tmp_path / "n.db")
    conv = store.create_conversation("Test")
    cid = int(conv["id"])
    display, items, _aid, tts, numbered_ui = _save_assistant_reply(
        store,
        cid,
        "1. Buy milk\n2. Buy eggs",
        action="chat",
        action_details={},
        sources={},
    )
    assert len(items) == 2
    assert numbered_ui is True
    assert tts and tts.startswith("One:")
    msgs = store.get_messages(cid)
    ast = [m for m in msgs if m["role"] == "assistant"][-1]
    meta = ast["meta"] or {}
    assert len(meta["numbered_items"]) == 2
    assert meta["tts_text"] is not None
    assert meta["show_numbered_list"] is True
    assert ast["content"] == display
    assert ast["content"].startswith("1.")


def test_single_paragraph_persistence_keeps_action_no_tts(tmp_path) -> None:
    from agent.loop import _save_assistant_reply

    store = AgentStore(tmp_path / "one.db")
    conv = store.create_conversation("Solo")
    cid = int(conv["id"])
    _display, items, _aid, tts, numbered_ui = _save_assistant_reply(
        store,
        cid,
        "Run this when ready",
        action="run_command",
        action_details={"command": "uptime", "args": []},
        sources={},
    )
    assert len(items) == 1 and items[0].get("action")
    assert tts is None
    assert numbered_ui is False


def test_tier0_do_action_routing(tmp_path) -> None:
    store = AgentStore(tmp_path / "r.db")
    proj = store.get_project_by_slug("general")
    sup_id = int(proj["supervisor_conversation_id"])
    chat = store.create_conversation("Home", project_id=int(proj["id"]))
    cid = int(chat["id"])
    store.add_message(
        cid,
        "assistant",
        "1. Check uptime",
        {
            "numbered_items": [
                {"n": 1, "text": "Check uptime", "action": {"kind": "run_command", "command": "echo", "args": ["ok"]}},
            ],
            "tts_text": "One: Check uptime",
        },
    )
    ctx = RouteContext(
        active_project_id=int(proj["id"]),
        active_chat_id=cid,
        supervisor_chat_id=sup_id,
        chats=[{"id": cid, "title": "Home", "aliases": []}],
        input_type="voice",
        command_store=None,
        tier0_enabled=True,
        allow_run_command=True,
    )
    d = route("do action 1", ctx, store=store)
    assert d.label == "S" and d.command == "do_numbered_action"
    assert d.payload["index"] == 1


def test_do_action_no_payload(tmp_path) -> None:
    store = AgentStore(tmp_path / "na.db")
    conv = store.create_conversation("X")
    cid = int(conv["id"])
    store.add_message(
        cid,
        "assistant",
        "1. Just text",
        {"numbered_items": [{"n": 1, "text": "Just text", "action": None}]},
    )
    agent = Agent(Settings(), AgentConfig(db_path=store.db_path), store, FakeEmbedder(), FakeLLM())
    r = begin_do_action(agent, cid, 1)
    assert "no action" in r.reply.lower()
    assert r.action_details.get("has_action") is False


def test_voice_confirm_flow(tmp_path, monkeypatch) -> None:
    store = AgentStore(tmp_path / "cf.db")
    conv = store.create_conversation("Y")
    cid = int(conv["id"])
    item = {
        "n": 1,
        "text": "Run uptime",
        "action": {"kind": "run_command", "command": "echo", "args": ["confirmed"]},
    }
    set_pending_confirm(cid, item=item, message_id=99)
    agent = Agent(Settings(), AgentConfig(db_path=store.db_path), store, FakeEmbedder(), FakeLLM())
    monkeypatch.setattr(
        "tools.pc_control.run_command",
        lambda cmd, args, settings: MagicMock(success=True, message=f"ran {cmd}"),
    )
    cancelled = resolve_pending_confirm(agent, cid, "no")
    assert cancelled and "cancel" in cancelled.reply.lower()
    set_pending_confirm(cid, item=item, message_id=99)
    ok = resolve_pending_confirm(agent, cid, "yes")
    assert ok and "ran echo" in ok.reply


def test_numbered_execute_api(tmp_path, monkeypatch) -> None:
    store = AgentStore(tmp_path / "api.db")
    conv = store.create_conversation("Z")
    cid = int(conv["id"])
    mid = store.add_message(
        cid,
        "assistant",
        "1. Ping",
        {
            "numbered_items": [
                {"n": 1, "text": "Ping", "action": {"kind": "run_command", "command": "echo", "args": ["pong"]}},
            ]
        },
    )
    agent = Agent(Settings(), AgentConfig(db_path=store.db_path), store, FakeEmbedder(), FakeLLM())
    monkeypatch.setattr(
        "tools.pc_control.run_command",
        lambda cmd, args, settings: MagicMock(success=True, message="pong"),
    )
    app = FastAPI()
    app.include_router(create_agent_router(lambda: agent))
    client = TestClient(app)
    need = client.post(
        "/api/agent/numbered/execute",
        json={"conversation_id": cid, "message_id": mid, "index": 1, "confirmed": False},
    ).json()
    assert need["needs_confirm"] is True
    assert "yes" in need["prompt"].lower()
    done = client.post(
        "/api/agent/numbered/execute",
        json={"conversation_id": cid, "message_id": mid, "index": 1, "confirmed": True},
    ).json()
    assert done["reply"] == "pong"


def test_ui_app_js_numbered_markers() -> None:
    js = (Path(__file__).resolve().parents[1] / "web" / "static" / "app.js").read_text(encoding="utf-8")
    assert "numbered-reply-list" in js
    assert "numbered-reply-marker" in js
    assert "numbered-action-chip" in js
    assert " · ${actionChipLabel" in js or " · `" in js
    assert "runNumberedAction" in js
    assert "shouldRenderNumberedList" in js
    assert "sahirOpenConversation" in js and "loadHomeConversation" in js
    assert "bootstrapConvFromQueryParam" not in js
    assert "/api/agent/numbered/execute" in js


def test_unified_layout_controls_at_bottom() -> None:
    html = (Path(__file__).resolve().parents[1] / "web" / "static" / "index.html").read_text(encoding="utf-8")
    log_pos = html.find('id="chat-log"')
    composer_pos = html.find('id="chat-form"')
    assert log_pos >= 0 and composer_pos > log_pos, "chat log should appear before composer (results above controls)"


def test_numbered_screenshot_script() -> None:
    import subprocess
    import sys

    script = Path(__file__).resolve().parents[1] / "scripts" / "capture_numbered_screenshots.py"
    out = Path(__file__).resolve().parents[1] / "data" / "test-artifacts"
    out.mkdir(parents=True, exist_ok=True)
    env = {**__import__("os").environ, "NUMBERED_SCREENSHOT_DIR": str(out)}
    try:
        subprocess.run(
            [sys.executable, str(script)],
            cwd=str(script.parent.parent),
            env=env,
            check=True,
            timeout=600,
        )
    except (subprocess.CalledProcessError, RuntimeError, OSError) as exc:
        pytest.skip(f"headless screenshot unavailable: {exc}")
    assert (out / "numbered-reply-desktop.png").is_file()
    assert (out / "numbered-reply-mobile.png").is_file()


def test_build_numbered_reply_display() -> None:
    display, items, numbered_ui = build_numbered_reply(
        "1. Alpha\n2. Beta",
        action="run_command",
        action_details={"command": "ls", "args": []},
    )
    assert numbered_ui is True
    assert "1. Alpha" in display
    assert items[0]["action"]["kind"] == "run_command"


def test_pending_confirm_expires(tmp_path, monkeypatch) -> None:
    import agent.numbered_actions as na

    monkeypatch.setattr(na, "PENDING_CONFIRM_TTL_S", 0.01)
    store = AgentStore(tmp_path / "exp.db")
    conv = store.create_conversation("E")
    cid = int(conv["id"])
    set_pending_confirm(cid, item={"n": 1, "text": "x", "action": {"kind": "run_command", "command": "echo"}}, message_id=1)
    assert get_pending_confirm(cid) is not None
    import time

    time.sleep(0.05)
    assert get_pending_confirm(cid) is None
    clear_pending_confirm(cid)


def test_ws_do_action_confirm_yes(client, monkeypatch) -> None:
    import json

    import web_server

    store = web_server._agent_stack.agent.store
    conv = store.create_conversation("Voice numbered")
    cid = int(conv["id"])
    store.add_message(
        cid,
        "assistant",
        "1. Ping\n2. Echo ok",
        {
            "numbered_items": [
                {"n": 1, "text": "Ping", "action": None},
                {"n": 2, "text": "Echo ok", "action": {"kind": "run_command", "command": "echo", "args": ["ok"]}},
            ],
            "show_numbered_list": True,
        },
    )
    monkeypatch.setattr(
        "tools.pc_control.run_command",
        lambda cmd, args, settings: MagicMock(success=True, message="voice-ok"),
    )

    def send_voice(ws, text: str) -> dict:
        ws.send_text(json.dumps({"type": "text", "text": text, "as_voice": True}))
        while True:
            msg = ws.receive()
            if msg.get("bytes"):
                continue
            ev = json.loads(msg["text"])
            if ev["type"] == "reply":
                return ev
            if ev["type"] == "error":
                raise AssertionError(ev)

    with client.websocket_connect("/ws/voice") as ws:
        ws.receive_json()
        ws.send_text(json.dumps({"type": "hello", "conversation_id": cid, "mode": "agent", "tts": False}))
        ws.receive_json()
        confirm = send_voice(ws, "do action 2")
        assert "yes" in (confirm.get("text") or "").lower()
        done = send_voice(ws, "yes")
        assert "voice-ok" in (done.get("text") or "")
        assert get_pending_confirm(cid) is None

    with client.websocket_connect("/ws/voice") as ws:
        ws.receive_json()
        ws.send_text(json.dumps({"type": "hello", "conversation_id": cid, "mode": "agent", "tts": False}))
        ws.receive_json()
        prompt2 = send_voice(ws, "do action 2")
        assert "yes" in (prompt2.get("text") or "").lower()
        cancelled = send_voice(ws, "never mind")
        assert "cancel" in (cancelled.get("text") or "").lower()
        assert get_pending_confirm(cid) is None