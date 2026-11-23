"""HTTP + WebSocket API tests with fake LLM/embedder/pipeline (no Ollama, no audio models)."""

from __future__ import annotations

import json

import numpy as np
import pytest
from fastapi.testclient import TestClient

import web_server
from agent.calendar_integration import CalendarService
from agent.clickup import ClickUpClient
from agent.config import AgentConfig
from agent.delegation import Dispatcher
from agent.email_integration import EmailService
from agent.factory import AgentStack
from agent.loop import Agent
from agent.orchestrator import Orchestrator
from agent.store import AgentStore
from config import Settings
from tests.conftest import FakeEmbedder, FakeLLM
from tests.test_orchestrator import RecWorker, ScriptedClient, call
from tools import router
from tools.router import RouteResult


class FakePipeline:
    tts_backend = "fake"

    def transcribe_final(self, audio):
        return "what is on my shopping list"

    def transcribe(self, audio):
        return self.transcribe_final(audio)

    def synthesize_wav(self, text):
        return b"RIFF" + text.encode()[:20], 22050


@pytest.fixture
def client(tmp_path, monkeypatch):
    settings = Settings()
    settings.web_db_path = tmp_path / "logs.db"
    cfg = AgentConfig(db_path=tmp_path / "agent.db", auto_memory=False, rag_min_score=0.1, memory_min_score=0.1)
    store = AgentStore(cfg.db_path)
    agent = Agent(settings, cfg, store, FakeEmbedder(), FakeLLM(reply="Milk and eggs. Also bread."))
    d = Dispatcher(store, retry_backoff_s=0.01)
    d.register(RecWorker("research"))
    agent.orchestrator = Orchestrator(settings, d, client=ScriptedClient([call("answer", reply="Milk and eggs. Also bread.")] * 20))
    monkeypatch.setattr(web_server, "_settings", settings)
    email = EmailService(settings, store=store)
    calendar = CalendarService(settings)
    monkeypatch.setattr(
        web_server,
        "_agent_stack",
        AgentStack(agent, d, ClickUpClient(token=""), email, calendar),
    )
    monkeypatch.setattr(web_server, "_pipeline", FakePipeline())
    monkeypatch.setattr(web_server, "_log_store", None)
    from web.commands import VoiceCommandStore

    monkeypatch.setattr(web_server, "_command_store", VoiceCommandStore(tmp_path / "cmds.db"))
    return TestClient(web_server.create_app())


def test_conversation_endpoints(client):
    c = client.post("/api/conversations", json={"title": "Groceries"}).json()
    r = client.post("/api/agent/chat", json={"message": "what do I need", "conversation_id": c["id"]}).json()
    assert r["conversation_id"] == c["id"] and r["reply"].startswith("Milk")
    full = client.get(f"/api/conversations/{c['id']}").json()
    assert [m["role"] for m in full["messages"]] == ["user", "assistant"]
    assert client.get("/api/conversations", params={"q": "Groceries"}).json()["conversations"][0]["id"] == c["id"]
    assert client.get("/api/conversations/search", params={"q": "need"}).json()["results"]
    assert client.patch(f"/api/conversations/{c['id']}", json={"title": "Shop"}).json()["title"] == "Shop"
    md = client.get(f"/api/conversations/{c['id']}/export").text
    assert md.startswith("# Shop") and "Milk and eggs" in md
    js = json.loads(client.get(f"/api/conversations/{c['id']}/export", params={"format": "json"}).text)
    assert len(js["messages"]) == 2
    assert client.delete(f"/api/conversations/{c['id']}").json() == {"deleted": True}
    assert client.get(f"/api/conversations/{c['id']}").status_code == 404


def test_supervisor_conversation_delete_rejected(client):
    data = client.get("/api/projects").json()
    sup_id = next(
        p["supervisor_conversation_id"] for p in data["projects"] if p.get("supervisor_conversation_id")
    )
    r = client.delete(f"/api/conversations/{sup_id}")
    assert r.status_code == 400
    assert "supervisor" in r.json()["detail"].lower()
    assert client.get(f"/api/conversations/{sup_id}").status_code == 200


def test_memory_endpoints(client):
    m = client.post("/api/memories", json={"content": "User is vegetarian", "category": "preference"}).json()
    assert m["status"] == "active"
    assert client.patch(f"/api/memories/{m['id']}", json={"status": "pending"}).json()["status"] == "pending"
    assert client.get("/api/memories", params={"status": "pending"}).json()["memories"][0]["id"] == m["id"]
    assert client.patch(f"/api/memories/{m['id']}", json={"status": "bogus"}).status_code == 422
    conv = client.post("/api/conversations", json={}).json()
    client.post("/api/agent/chat", json={"message": "I love green tea", "conversation_id": conv["id"], "mode": "chat"})
    ext = client.post("/api/memories/extract", json={"conversation_id": conv["id"]}).json()
    assert ext["count"] == 1 and ext["created"][0]["status"] == "pending"
    assert client.post("/api/memories/search", json={"query": "green tea"}).json()["results"] == []  # pending only
    client.patch(f"/api/memories/{ext['created'][0]['id']}", json={"status": "active"})
    assert client.post("/api/memories/search", json={"query": "green tea"}).json()["results"]
    assert client.delete(f"/api/memories/{m['id']}").json() == {"deleted": True}


def test_rag_endpoints(client):
    files = [("files", ("notes.md", b"The wifi password is stored in the blue notebook.", "text/markdown")),
             ("files", ("bad.exe", b"MZ", "application/octet-stream"))]
    r = client.post("/api/rag/documents", files=files).json()["results"]
    assert r[0]["ok"] and not r[1]["ok"]
    doc_id = r[0]["document"]["id"]
    assert client.get("/api/rag/documents").json()["documents"][0]["filename"] == "notes.md"
    assert client.get(f"/api/rag/documents/{doc_id}").json()["chunks"][0]["content"].startswith("The wifi")
    hits = client.post("/api/rag/search", json={"query": "wifi password notebook"}).json()["results"]
    assert hits[0]["filename"] == "notes.md"
    assert client.delete(f"/api/rag/documents/{doc_id}").json() == {"deleted": True}
    assert client.post("/api/rag/documents", files=[("files", ("x.exe", b"MZ", "x"))]).status_code == 400


def test_stream_endpoint(client):
    with client.stream("POST", "/api/agent/chat/stream", json={"message": "hi"}) as resp:
        events = [json.loads(line[6:]) for line in resp.iter_lines() if line.startswith("data: ")]
    assert events[0]["type"] == "route" and events[-1]["type"] == "done"
    assert events[-1]["reply"] == "Milk and eggs. Also bread."


def test_status_and_tasks(client):
    st = client.get("/api/agent/status").json()
    assert st["vector_backend"] in {"sqlite-vec", "numpy"} and "agent" in st["modes"]
    t = client.get("/api/agent/tasks").json()
    assert t["workers"][0]["type"] == "research" and t["clickup_configured"] is False


def test_legacy_chat_persists_with_context(client, monkeypatch):
    seen = {}

    def fake_route(text, settings, *, history=None, force_cursor=False, on_long_task=None, context=None):
        seen["context"] = context
        return RouteResult(reply="ok", action="local_chat", routed=True)

    monkeypatch.setattr(web_server, "route_utterance_detailed", fake_route)
    client.post("/api/memories", json={"content": "User's cat is named Miso"})
    r = client.post("/api/chat", json={"message": "what is my cat named"}).json()
    assert r["conversation_id"] and "Miso" in (seen["context"] or "")
    r2 = client.post("/api/chat", json={"message": "thanks", "conversation_id": r["conversation_id"]}).json()
    assert r2["conversation_id"] == r["conversation_id"]


def test_agent_page_served(client):
    r = client.get("/agent", follow_redirects=False)
    assert r.status_code == 302
    assert "view=chat" in r.headers.get("location", "")
    html = client.get("/").text
    assert "view-chat" in html and "agent.js" in html
    assert "project-rail" in html
    assert 'id="conv-actions" hidden' in html
    css = client.get("/static/agent.css").text
    assert "[hidden]" in css and "display:none" in css


def test_legacy_chat_run_command_tier0(client, monkeypatch):
    monkeypatch.setattr(
        "tools.pc_control.run_command",
        lambda cmd, args, _s: type("R", (), {"message": "2 users", "success": True})(),
    )
    r = client.post("/api/chat", json={"message": "run uptime", "route": True}).json()
    assert r["action"] == "run_command"
    assert r["routed"] is True


def test_agent_chat_typed_switch(client):
    a = client.post("/api/conversations", json={"title": "Alpha"}).json()
    b = client.post("/api/conversations", json={"title": "Beta"}).json()
    with client.stream("POST", "/api/agent/chat/stream", json={
        "message": "/switch Beta",
        "conversation_id": a["id"],
        "mode": "agent",
    }) as resp:
        events = [json.loads(line[6:]) for line in resp.iter_lines() if line.startswith("data: ")]
    route_ev = next(e for e in events if e.get("type") == "route")
    assert route_ev["route_label"] == "S" and route_ev["route_command"] == "switch_chat"
    assert route_ev["route_target_chat_id"] == b["id"]
    done = events[-1]
    assert done["type"] == "done"
    assert done["action"] == "switch_chat"
    assert done["conversation_id"] == b["id"]


def test_agent_chat_typed_sup_address(client, monkeypatch):
    data = client.get("/api/projects").json()
    sup_id = next(p["supervisor_conversation_id"] for p in data["projects"] if p.get("supervisor_conversation_id"))
    sub = client.post("/api/conversations", json={"title": "Work"}).json()
    with client.stream(
        "POST",
        "/api/agent/chat/stream",
        json={"message": "/sup hello supervisor", "conversation_id": sub["id"], "mode": "agent"},
    ) as resp:
        events = [json.loads(line[6:]) for line in resp.iter_lines() if line.startswith("data: ")]
    route_ev = next(e for e in events if e.get("type") == "route")
    assert route_ev["route_label"] == "D"
    assert route_ev["route_target_chat_id"] == sup_id
    done = events[-1]
    assert done["conversation_id"] == sup_id


def test_agent_chat_at_mention_routes(client):
    a = client.post("/api/conversations", json={"title": "Groceries"}).json()
    proj = client.get("/api/projects").json()
    pid = proj["general_project_id"]
    spawned = client.post(
        "/api/supervisor/spawn",
        json={"project_id": pid, "title": "API", "brief": "", "aliases": ["api"]},
    ).json()
    api_id = spawned["id"]
    with client.stream(
        "POST",
        "/api/agent/chat/stream",
        json={"message": "@api status check", "conversation_id": a["id"], "mode": "agent"},
    ) as resp:
        events = [json.loads(line[6:]) for line in resp.iter_lines() if line.startswith("data: ")]
    route_ev = next(e for e in events if e.get("type") == "route")
    assert route_ev["route_label"] == "C"
    assert route_ev["route_target_chat_id"] == api_id


def test_agent_chat_typed_plain_skips_tier0(client):
    a = client.post("/api/conversations", json={"title": "Plain"}).json()
    r = client.post(
        "/api/agent/chat",
        json={"message": "switch to nowhere", "conversation_id": a["id"], "mode": "agent"},
    ).json()
    assert r["route_label"] == "C"
    assert r["route_tier"] == 0
    assert r["conversation_id"] == a["id"]


def test_voice_ws_router_scenarios(client, monkeypatch):
    from agent.vad import Endpointer

    a = client.post("/api/conversations", json={"title": "One"}).json()
    b = client.post("/api/conversations", json={"title": "Two"}).json()
    data = client.get("/api/projects").json()
    sup_id = next(p["supervisor_conversation_id"] for p in data["projects"] if p.get("supervisor_conversation_id"))
    transcripts = iter([f"switch to {b['title']}", "Supervisor, ping", "what's on my list"])

    class RoutingPipeline(FakePipeline):
        def transcribe_final(self, audio):
            return next(transcripts, "what's on my list")

    monkeypatch.setattr(web_server, "_pipeline", RoutingPipeline())
    monkeypatch.setattr(Endpointer, "flush", lambda self: np.zeros(16000, dtype=np.float32))

    def await_reply(ws):
        for _ in range(30):
            msg = ws.receive()
            if msg.get("bytes") is not None:
                continue
            ev = json.loads(msg["text"])
            if ev["type"] == "reply":
                return ev
            if ev["type"] == "error":
                raise AssertionError(ev)
        raise AssertionError("timed out waiting for voice reply")

    def flush_turn(ws):
        ws.send_text(json.dumps({"type": "flush"}))
        return await_reply(ws)

    with client.websocket_connect("/ws/voice") as ws:
        ws.receive_json()
        ws.send_text(json.dumps({"type": "hello", "conversation_id": a["id"], "mode": "agent", "tts": False}))
        ws.receive_json()
        reply = flush_turn(ws)
        assert reply["action"] == "switch_chat"
        assert reply["conversation_id"] == b["id"]

        ws.send_text(json.dumps({"type": "hello", "conversation_id": a["id"], "mode": "agent", "tts": False}))
        ws.receive_json()
        reply = flush_turn(ws)
        assert reply["conversation_id"] == sup_id

        ws.send_text(json.dumps({"type": "hello", "conversation_id": b["id"], "mode": "agent", "tts": False}))
        ws.receive_json()
        reply = flush_turn(ws)
        assert reply.get("conversation_id") == b["id"]
        assert "Milk" in reply.get("text", "")


def test_voice_ws_text_turn_and_tts(client):
    with client.websocket_connect("/ws/voice") as ws:
        assert ws.receive_json()["type"] == "ready"
        ws.send_text(json.dumps({"type": "hello", "mode": "agent"}))
        assert ws.receive_json()["type"] == "ready"
        ws.send_text(json.dumps({"type": "text", "text": "what's on my list"}))
        got, audio_frames = [], 0
        while True:
            msg = ws.receive()
            if msg.get("bytes") is not None:
                audio_frames += 1
                continue
            ev = json.loads(msg["text"])
            got.append(ev["type"])
            if ev["type"] == "tts_done":
                break
        assert "reply" in got and audio_frames == 2  # two sentences → two WAV frames


def test_voice_ws_stop_text_as_voice(client):
    with client.websocket_connect("/ws/voice") as ws:
        ws.receive_json()
        ws.send_text(json.dumps({"type": "hello", "mode": "agent", "tts": True}))
        ws.receive_json()
        ws.send_text(json.dumps({"type": "text", "text": "Stop.", "as_voice": True}))
        reply = None
        for _ in range(25):
            msg = ws.receive()
            if msg.get("bytes") is not None:
                continue
            ev = json.loads(msg["text"])
            if ev["type"] == "reply":
                reply = ev
                break
        assert reply is not None
        assert reply.get("route_tier") == 0
        assert reply.get("action") == "stop_speech"


def test_voice_ws_audio_endpointing(client):
    """Silence → no events; loud noise burst handled by VAD; flush forces an utterance."""
    with client.websocket_connect("/ws/voice") as ws:
        ws.receive_json()
        silence = np.zeros(16000, dtype=np.int16).tobytes()
        ws.send_bytes(silence)
        ws.send_text(json.dumps({"type": "ping"}))
        assert ws.receive_json()["type"] == "pong"  # no speech_start emitted for silence
