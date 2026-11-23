"""Live activity panel: bus, progress mirroring, log bridge, SSE stream, Cursor CLI parsing."""

from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import activity
from activity import (
    ActivityBus,
    ActivityLogHandler,
    CLIENT_PUBLISH_KINDS,
    STRUCTURED_KINDS,
    describe_progress,
    tee_progress,
)
from tools.cursor_cli import _StreamEvents


def test_structured_event_kinds() -> None:
    assert STRUCTURED_KINDS == frozenset({"route", "switch", "supervisor", "inject", "spawn"})
    assert CLIENT_PUBLISH_KINDS == frozenset({"switch"})
    bus = ActivityBus()
    route = bus.publish("route", "", source="router", label="C", tier=0, confidence=0.92, ms=3)
    assert route is not None and route["kind"] == "route"
    assert route["data"]["label"] == "C"
    assert "T0" in route["text"] and "0.92" in route["text"]
    switch = bus.publish("switch", "", source="ui", conversation_id=9, project_name="General", chat_title="Notes")
    assert switch and switch["text"] == "General · Notes"
    sup = bus.publish("supervisor", "", source="supervisor", state="briefing sub-chats")
    assert sup and "briefing" in sup["text"]
    assert bus.publish("inject", "", source="sup", target="API refactor", count=3)
    assert bus.publish("spawn", "", source="sup", chat_title="Flight", project_name="Travel")
    assert bus.publish("route", "", source="x") is None  # no data → dropped


def test_index_html_removed_legacy_activity_boxes() -> None:
    html = (Path(__file__).resolve().parents[1] / "web" / "static" / "index.html").read_text(encoding="utf-8")
    assert 'id="activity-feed"' not in html
    assert 'id="activity-list"' not in html
    assert 'id="agent-activity"' not in html
    assert 'id="wip-context"' in html


def test_activity_publish_switch_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    import web_server

    bus = ActivityBus()
    monkeypatch.setattr(activity, "bus", bus)
    client = TestClient(web_server.create_app())
    bad = client.post("/api/activity/publish", json={"kind": "route", "data": {"label": "C"}})
    assert bad.status_code == 400
    ok = client.post("/api/activity/publish", json={
        "kind": "switch",
        "data": {"conversation_id": 2, "project_name": "General", "chat_title": "Hello"},
    })
    assert ok.status_code == 200 and ok.json()["ok"] is True
    assert bus.history()[-1]["kind"] == "switch"


def test_bus_history_ids_and_truncation() -> None:
    bus = ActivityBus(maxlen=3)
    for i in range(5):
        bus.publish("step", f"line {i}")
    hist = bus.history()
    assert [e["text"] for e in hist] == ["line 2", "line 3", "line 4"]
    assert hist[-1]["id"] == 5 and bus.history(since=4)[0]["text"] == "line 4"
    assert bus.publish("step", "   ") is None
    long = bus.publish("out", "x" * 10_000)
    assert len(long["text"]) < 4100 and long["text"].endswith("[truncated]")


def test_tee_progress_publishes_and_forwards(monkeypatch: pytest.MonkeyPatch) -> None:
    bus = ActivityBus()
    monkeypatch.setattr(activity, "bus", bus)
    seen: list[dict] = []
    emit = tee_progress(seen.append, source="agent")
    emit({"type": "tool_calls", "round": 1, "calls": [{"name": "run_command", "args": {"command": "uptime"}}]})
    emit({"type": "progress", "stage": "cursor", "message": "Cursor CLI agent running…"})
    emit({"type": "token", "text": "hi"})  # reply tokens stay in the chat, not the panel
    assert len(seen) == 3
    kinds = [(e["kind"], e["text"]) for e in bus.history()]
    assert kinds[0] == ("tool", 'tool call (round 1): run_command({"command": "uptime"})')
    assert kinds[1] == ("cli", "[cursor] Cursor CLI agent running…")
    assert len(kinds) == 2
    # tee without a downstream callback must still work
    tee_progress(None)({"type": "worker_result", "worker": "code", "status": "success", "task_id": "t1"})
    assert bus.history()[-1]["kind"] == "worker"


def test_describe_progress_delegate() -> None:
    assert describe_progress({"type": "delegate", "message": "Delegating", "workers": ["code", "research"]}) == [
        ("worker", "Delegating → code, research")]


def test_log_bridge_filters(monkeypatch: pytest.MonkeyPatch) -> None:
    bus = ActivityBus()
    monkeypatch.setattr(activity, "bus", bus)
    h = ActivityLogHandler()

    def rec(name: str, level: int, msg: str, **extra) -> logging.LogRecord:
        r = logging.LogRecord(name, level, __file__, 1, msg, None, None)
        r.__dict__.update(extra)
        return r

    h.emit(rec("tools.router", logging.INFO, "Delegating to Cursor CLI"))
    h.emit(rec("audio_decode", logging.INFO, "Decoding audio upload"))           # noisy: skipped
    h.emit(rec("audio_pipeline", logging.INFO, "Transcribed (live/tiny): 'hi'"))  # live captions: skipped
    h.emit(rec("audio_pipeline", logging.INFO, "Transcribed (final/base): 'hi'"))
    h.emit(rec("some.lib", logging.INFO, "chatter"))                             # third-party info: skipped
    h.emit(rec("some.lib", logging.WARNING, "careful"))
    h.emit(rec("tools.cursor_cli", logging.INFO, "dup", no_activity=True))
    texts = [(e["kind"], e["text"]) for e in bus.history()]
    assert texts == [("log", "tools.router: Delegating to Cursor CLI"),
                     ("log", "audio_pipeline: Transcribed (final/base): 'hi'"),
                     ("error", "some.lib: careful")]


def test_activity_endpoints_stream_backlog(monkeypatch: pytest.MonkeyPatch) -> None:
    import web_server

    bus = ActivityBus()
    monkeypatch.setattr(activity, "bus", bus)
    monkeypatch.setattr(web_server, "ACTIVITY_STREAM_MAX_S", 0.3)
    bus.publish("cmd", "$ uptime", source="pc")
    bus.publish("out", "up 3 days", source="pc")
    client = TestClient(web_server.create_app())
    assert [e["text"] for e in client.get("/api/activity").json()["events"]] == ["$ uptime", "up 3 days"]
    with client.stream("GET", "/api/activity/stream", headers={"Last-Event-ID": "1"}) as resp:
        assert resp.headers["content-type"].startswith("text/event-stream")
        body = "".join(resp.iter_text())
    data = [json.loads(line[6:]) for line in body.splitlines() if line.startswith("data: ")]
    assert [d["text"] for d in data] == ["up 3 days"]
    assert "id: 2" in body and body.startswith("retry:")


def test_cursor_stream_events_reply_and_panel(monkeypatch: pytest.MonkeyPatch) -> None:
    bus = ActivityBus()
    monkeypatch.setattr(activity, "bus", bus)
    import tools.cursor_cli as cc
    monkeypatch.setattr(cc, "publish", bus.publish)
    lines = [
        {"type": "system", "subtype": "init", "model": "Composer"},
        {"type": "thinking", "subtype": "delta", "text": "Let me "},
        {"type": "thinking", "subtype": "delta", "text": "look."},
        {"type": "thinking", "subtype": "completed"},
        {"type": "tool_call", "subtype": "started", "tool_call": {"shellToolCall": {"args": {"command": "ls -la"}}}},
        {"type": "tool_call", "subtype": "completed", "tool_call": {"shellToolCall": {
            "args": {"command": "ls -la"}, "result": {"success": {"stdout": "a.txt\n", "exitCode": 0}}}}},
        {"type": "tool_call", "subtype": "started", "tool_call": {"readToolCall": {"args": {"path": "a.txt"}}}},
        {"type": "assistant", "message": {"content": [{"type": "text", "text": "Done."}]}},
        {"type": "result", "subtype": "success", "result": "Done.", "duration_ms": 1200},
    ]
    ev = _StreamEvents()
    for line in lines:
        ev.feed(json.dumps(line) + "\n")
    ev.feed("plain text line\n")
    assert ev.text() == "Done."
    got = [(e["kind"], e["text"]) for e in bus.history()]
    assert ("log", "thinking: Let me look.") in got
    assert ("cmd", "$ ls -la") in got
    assert ("out", "shell ok (exit 0)\na.txt") in got
    assert ("tool", "read: a.txt") in got
    assert ("step", "cursor result: success in 1.2s") in got
    assert got[-1] == ("out", "plain text line")
