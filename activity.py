"""Live activity bus for the UI's routine / WIP terminal panel.

Every part of the backend can call ``publish(kind, text)``: commands run, Cursor CLI
output, tool calls, worker hand-offs, model changes, benchmark progress and selected
log lines. Events go into a bounded in-memory history and out to every subscriber of
``GET /api/activity/stream`` (Server-Sent Events). Thread-safe; publishing never blocks
and never logs (so the logging bridge can't recurse).

Kinds used by the UI for colouring: cmd, out, cli, tool, step, model, worker, log, error.
"""

from __future__ import annotations

import asyncio
import itertools
import json
import logging
import threading
import time
from collections import deque
from typing import Any, Callable

MAX_HISTORY = 600
MAX_TEXT = 4000
QUEUE_SIZE = 2000


def _jsonable(data: dict[str, Any]) -> dict[str, Any]:
    try:
        return json.loads(json.dumps(data, default=str))
    except Exception:
        return {"repr": repr(data)[:500]}


def _put(q: asyncio.Queue, ev: dict[str, Any]) -> None:
    if q.full():
        try:
            q.get_nowait()
        except asyncio.QueueEmpty:
            pass
    q.put_nowait(ev)


class ActivityBus:
    def __init__(self, maxlen: int = MAX_HISTORY) -> None:
        self._lock = threading.Lock()
        self._history: deque[dict[str, Any]] = deque(maxlen=maxlen)
        self._seq = itertools.count(1)
        self._subs: list[tuple[asyncio.AbstractEventLoop, asyncio.Queue]] = []

    def publish(self, kind: str, text: str, *, source: str = "", level: str = "info",
                **data: Any) -> dict[str, Any] | None:
        text = str(text or "").rstrip()
        if not text:
            return None
        if len(text) > MAX_TEXT:
            text = text[:MAX_TEXT] + " …[truncated]"
        with self._lock:
            ev: dict[str, Any] = {"id": next(self._seq), "ts": round(time.time(), 3), "kind": kind,
                                  "source": source, "level": level, "text": text}
            if data:
                ev["data"] = _jsonable(data)
            self._history.append(ev)
            subs = list(self._subs)
        for loop, q in subs:
            try:
                loop.call_soon_threadsafe(_put, q, ev)
            except RuntimeError:  # loop closed
                pass
        return ev

    def history(self, since: int = 0, limit: int = MAX_HISTORY) -> list[dict[str, Any]]:
        with self._lock:
            items = [e for e in self._history if e["id"] > since]
        return items[-limit:]

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=QUEUE_SIZE)
        with self._lock:
            self._subs.append((asyncio.get_running_loop(), q))
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        with self._lock:
            self._subs = [(lp, s) for lp, s in self._subs if s is not q]

    @property
    def subscriber_count(self) -> int:
        with self._lock:
            return len(self._subs)


bus = ActivityBus()


def publish(kind: str, text: str, **kw: Any) -> dict[str, Any] | None:
    """Module-level shortcut; never raises."""
    try:
        return bus.publish(kind, text, **kw)
    except Exception:  # the panel must never break real work
        return None


# --------------------------------------------------------------- progress events
def _short(value: Any, limit: int = 160) -> str:
    if isinstance(value, (dict, list)):
        value = json.dumps(value, ensure_ascii=False, default=str)
    s = " ".join(str(value).split())
    return s if len(s) <= limit else s[: limit - 1] + "…"


def describe_progress(ev: dict[str, Any]) -> list[tuple[str, str]]:
    """Turn an agent on_progress event into (kind, text) lines for the panel."""
    t = ev.get("type")
    if t == "progress":
        stage = ev.get("stage") or "step"
        return [("cli" if stage == "cursor" else "step", f"[{stage}] {ev.get('message', '')}")]
    if t == "model":
        return [("model", ev.get("message") or f"model {ev.get('model')}")]
    if t == "tool_calls":
        rnd = f" (round {ev['round']})" if ev.get("round") else ""
        return [("tool", f"tool call{rnd}: {c.get('name') or 'tool'}({_short(c.get('args') or {})})")
                for c in ev.get("calls") or []]
    if t == "delegate":
        return [("worker", f"{ev.get('message') or 'Delegating'} → {', '.join(ev.get('workers') or [])}")]
    if t == "worker_result":
        return [("worker", f"worker {ev.get('worker')} finished: {ev.get('status')} (task {ev.get('task_id', '?')})")]
    if t == "error":
        return [("error", f"error: {ev.get('error')}")]
    return []


def tee_progress(on_progress: Callable[[dict[str, Any]], None] | None, *, source: str = "agent"
                 ) -> Callable[[dict[str, Any]], None]:
    """Wrap an on_progress callback so every event is also published to the panel."""
    def emit(ev: dict[str, Any]) -> None:
        try:
            for kind, text in describe_progress(ev):
                publish(kind, text, source=source, level="error" if kind == "error" else "info")
        except Exception:
            pass
        if on_progress:
            on_progress(ev)
    return emit


# --------------------------------------------------------------- logging bridge
_APP_LOGGERS = ("agent", "tools", "web", "audio_pipeline", "tts_backends", "web_server", "bot",
                "markdown_speech", "httpx", "__main__", "activity")
_SKIP_LOGGERS = ("audio_decode", "faster_whisper", "uvicorn.access", "web.voice_ws.vad")


class ActivityLogHandler(logging.Handler):
    """Mirror useful app log lines (and every warning/error) into the panel."""

    def emit(self, record: logging.LogRecord) -> None:
        try:
            if getattr(record, "no_activity", False):
                return
            name = record.name
            if any(name == s or name.startswith(s + ".") for s in _SKIP_LOGGERS):
                return
            is_app = any(name == a or name.startswith(a + ".") for a in _APP_LOGGERS)
            if record.levelno < logging.WARNING and not is_app:
                return
            msg = record.getMessage()
            if name == "audio_pipeline" and msg.startswith("Transcribed (live"):
                return  # live captions repeat every second; the final transcript is kept
            if name == "httpx":
                msg = msg.replace("HTTP Request: ", "HTTP ")
                if "/api/embed" in msg:
                    return
            kind = "error" if record.levelno >= logging.WARNING else "log"
            level = record.levelname.lower()
            if record.exc_info and record.levelno >= logging.ERROR:
                exc = record.exc_info[1]
                if exc is not None:
                    msg += f" ({type(exc).__name__}: {exc})"
            publish(kind, f"{name}: {msg}", source="log", level=level)
        except Exception:
            pass


def install_log_bridge() -> None:
    root = logging.getLogger()
    if not any(isinstance(h, ActivityLogHandler) for h in root.handlers):
        h = ActivityLogHandler(level=logging.INFO)
        root.addHandler(h)
