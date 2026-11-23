"""Item 11 correction: /ws/voice supervisor spawn / switch / Q&A sequence."""

from __future__ import annotations

import json

import pytest

import web_server
from tests.conftest import FakeLLM

pytest_plugins = ["tests.test_agent_api"]


def _await_reply(ws) -> dict:
    for _ in range(40):
        msg = ws.receive()
        if msg.get("bytes") is not None:
            continue
        ev = json.loads(msg["text"])
        if ev["type"] == "reply":
            return ev
        if ev["type"] == "error":
            raise AssertionError(ev)
    raise AssertionError("timed out waiting for reply")


def test_ws_supervisor_three_turns_as_voice(client, monkeypatch) -> None:
    payload = client.get("/api/projects").json()
    proj_id = int(payload["general_project_id"])
    gen = next(p for p in payload["projects"] if int(p["id"]) == proj_id)
    sup_id = int(gen["supervisor_conversation_id"])

    llm = web_server._agent_stack.agent.llm
    assert isinstance(llm, FakeLLM)
    llm.json_reply = json.dumps({"tool": "answer", "reply": "Four."})

    def send_text(ws, text: str, *, as_voice: bool) -> dict:
        ws.send_text(json.dumps({"type": "text", "text": text, "as_voice": as_voice}))
        while True:
            msg = ws.receive()
            if msg.get("bytes"):
                continue
            ev = json.loads(msg["text"])
            if ev["type"] == "reply":
                return ev
            if ev["type"] == "transcript" and ev.get("text"):
                continue
            if ev["type"] == "status":
                continue
            if ev["type"] == "error":
                raise AssertionError(ev)

    with client.websocket_connect("/ws/voice") as ws:
        ws.receive_json()
        ws.send_text(json.dumps({"type": "hello", "conversation_id": sup_id, "mode": "agent", "tts": False}))
        ws.receive_json()

        r1 = send_text(ws, "Supervisor, spin up a chat for the API refactor", as_voice=True)
        assert r1["action"] == "spawn_sub_chat"

        store = web_server._agent_stack.agent.store
        subs = [
            c
            for c in store.list_sub_chats(proj_id, include_archived=False)
            if "api refactor" in (c.get("title") or "").lower()
        ]
        assert len(subs) == 1
        api_id = int(subs[0]["id"])

        r2 = send_text(ws, "switch to API refactor", as_voice=True)
        assert r2["action"] == "switch_chat"
        assert r2["conversation_id"] == api_id
        subs_after = [
            c
            for c in store.list_sub_chats(proj_id, include_archived=False)
            if "api refactor" in (c.get("title") or "").lower()
        ]
        assert len(subs_after) == 1

        ws.send_text(json.dumps({"type": "hello", "conversation_id": sup_id, "mode": "agent", "tts": False}))
        ws.receive_json()
        r3 = send_text(ws, "what is two plus two", as_voice=True)
        assert r3["action"] != "supervisor_clarify"
        text_l = (r3.get("text") or "").lower()
        assert "four" in text_l or text_l.strip() == "4"


def test_ws_plain_text_inject_skips_full_voice_route(client) -> None:
    payload = client.get("/api/projects").json()
    proj_id = int(payload["general_project_id"])
    gen = next(p for p in payload["projects"] if int(p["id"]) == proj_id)
    sup_id = int(gen["supervisor_conversation_id"])
    from agent.supervisor import SupervisorService

    svc = SupervisorService(
        web_server._agent_stack.agent.store,
        web_server._agent_stack.agent.llm,
        web_server._agent_stack.agent.settings,
    )
    svc.spawn_sub_chat(proj_id, title="API refactor", supervisor_conv_id=sup_id)
    with client.websocket_connect("/ws/voice") as ws:
        ws.receive_json()
        ws.send_text(json.dumps({"type": "hello", "conversation_id": sup_id, "mode": "agent", "tts": False}))
        ws.receive_json()
        ws.send_text(json.dumps({"type": "text", "text": "switch to API refactor"}))
        reply = _await_reply(ws)
        assert reply["action"] == "switch_chat"


def test_supervisor_plain_math_not_clarify(client) -> None:
    payload = client.get("/api/projects").json()
    proj_id = int(payload["general_project_id"])
    gen = next(p for p in payload["projects"] if int(p["id"]) == proj_id)
    sup_id = int(gen["supervisor_conversation_id"])
    llm = web_server._agent_stack.agent.llm
    llm.json_reply = json.dumps({"tool": "spawn_sub_chat", "title": "API refactor"})
    svc = web_server._agent_stack.agent.supervisor
    if svc is None:
        from agent.supervisor import SupervisorService

        svc = SupervisorService(
            web_server._agent_stack.agent.store,
            llm,
            web_server._agent_stack.agent.settings,
        )
        web_server._agent_stack.agent.supervisor = svc
    reply = svc.run_turn(
        "what is two plus two",
        project_id=proj_id,
        supervisor_conv_id=sup_id,
        history=[],
    )
    assert reply.action != "supervisor_clarify"
    llm.json_reply = json.dumps({"tool": "answer", "reply": "Four."})
    reply2 = svc.run_turn(
        "what is two plus two",
        project_id=proj_id,
        supervisor_conv_id=sup_id,
        history=[],
    )
    assert reply2.action == "supervisor_answer"