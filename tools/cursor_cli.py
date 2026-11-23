"""Wrap Cursor CLI for voice-triggered dev tasks."""

from __future__ import annotations

import json
import logging
import os
import shutil
import queue
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from activity import publish

logger = logging.getLogger(__name__)


@dataclass
class CursorResult:
    text: str
    returncode: int


def resolve_agent_bin(agent_bin: str) -> str:
    """Find cursor-agent even when PATH lacks ~/.local/bin (e.g. systemd)."""
    found = shutil.which(agent_bin)
    if found:
        return found
    fallback = Path.home() / ".local" / "bin" / agent_bin
    if fallback.is_file() and os.access(fallback, os.X_OK):
        return str(fallback)
    return agent_bin


def run_cursor_agent(
    prompt: str,
    *,
    workspace: Path,
    model: str,
    agent_bin: str = "cursor-agent",
    api_key: str = "",
    timeout_seconds: int = 300,
    plan_mode: bool = False,
    stream_partial: bool = False,
) -> CursorResult:
    """Run cursor-agent in print mode and return spoken summary text."""
    if not Path(workspace).is_dir():
        return CursorResult(
            text=f"Cursor workspace does not exist: {workspace}. Check CURSOR_WORKSPACE in .env.",
            returncode=2,
        )

    # Always run with stream-json so the live activity panel can show the agent's tool calls
    # and shell commands as they happen; the reply text is rebuilt from the events below.
    cmd = [
        resolve_agent_bin(agent_bin),
        "-p",
        "--output-format",
        "stream-json",
        "--trust",
        "--workspace",
        str(workspace),
        "--model",
        model,
    ]
    if plan_mode:
        cmd.append("--plan")
    if stream_partial:
        cmd.append("--stream-partial-output")
    cmd.append(prompt)

    env = os.environ.copy()
    if api_key:
        env["CURSOR_API_KEY"] = api_key

    logger.info("Running Cursor agent: %s", " ".join(cmd[:-1]), extra={"no_activity": True})
    shown = " ".join(cmd[:-1]).replace(str(Path.home()), "~")
    publish("cmd", f"$ {shown} {_quote_prompt(prompt)}", source="cursor")
    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
            cwd=str(workspace),
            env=env,
        )
    except FileNotFoundError:
        publish("error", "cursor-agent was not found on PATH", source="cursor", level="error")
        return CursorResult(
            text="cursor-agent was not found. Install Cursor CLI and ensure it is on PATH.",
            returncode=127,
        )

    lines: queue.SimpleQueue[str | None] = queue.SimpleQueue()
    stderr_parts: list[str] = []

    def _pump_stdout() -> None:
        assert proc.stdout is not None
        for line in proc.stdout:
            lines.put(line)
        lines.put(None)

    def _pump_stderr() -> None:
        assert proc.stderr is not None
        for line in proc.stderr:
            stderr_parts.append(line)
            publish("error", f"cursor-agent: {line.rstrip()}", source="cursor", level="warning")

    threading.Thread(target=_pump_stdout, name="cursor-stdout", daemon=True).start()
    threading.Thread(target=_pump_stderr, name="cursor-stderr", daemon=True).start()

    events = _StreamEvents()
    stdout_parts: list[str] = []
    deadline = time.monotonic() + timeout_seconds
    timed_out = False
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            timed_out = True
            break
        try:
            line = lines.get(timeout=min(remaining, 1.0))
        except queue.Empty:
            continue
        if line is None:
            break
        stdout_parts.append(line)
        events.feed(line)
    if timed_out:
        proc.kill()
        proc.wait(timeout=10)
        publish("error", f"cursor-agent timed out after {timeout_seconds}s", source="cursor", level="error")
        return CursorResult(
            text="The Cursor agent timed out before finishing.",
            returncode=124,
        )
    try:
        returncode = proc.wait(timeout=30)
    except subprocess.TimeoutExpired:
        proc.kill()
        returncode = proc.wait(timeout=10)
    events.flush()
    stdout = "".join(stdout_parts)
    stderr = "".join(stderr_parts)
    publish("step" if returncode == 0 else "error", f"cursor-agent exited with code {returncode}",
            source="cursor", level="info" if returncode == 0 else "error")

    if returncode != 0:
        err = (stderr or events.text() or stdout or "Unknown error").strip()
        return CursorResult(
            text=f"Cursor agent failed: {err[:300]}",
            returncode=returncode,
        )

    if stream_partial:
        text = _extract_stream_json_text(stdout)
    else:
        text = events.text() or stdout.strip()

    if not text:
        text = "The Cursor agent finished but returned no text."

    return CursorResult(text=_truncate_for_speech(text), returncode=0)


def _quote_prompt(prompt: str, limit: int = 140) -> str:
    one = " ".join(prompt.split())
    if len(one) > limit:
        one = one[: limit - 1] + "…"
    return '"' + one.replace('"', '\\"') + '"'


_TOOL_ARG_KEYS = ("command", "path", "targetFile", "pattern", "globPattern", "query", "url", "description")


def _tool_label(tool_call: dict[str, Any]) -> tuple[str, dict[str, Any], dict[str, Any] | None]:
    for key, val in tool_call.items():
        if key.endswith("ToolCall") and isinstance(val, dict):
            return key[: -len("ToolCall")], val.get("args") or {}, val.get("result")
    return "tool", {}, None


class _StreamEvents:
    """Parse cursor-agent stream-json lines, publish them to the activity panel, keep the reply."""

    def __init__(self) -> None:
        self._result: str | None = None
        self._assistant: list[str] = []
        self._thinking: list[str] = []

    def text(self) -> str:
        if self._result and self._result.strip():
            return self._result.strip()
        return "".join(self._assistant).strip()

    def flush(self) -> None:
        if self._thinking:
            thought = " ".join("".join(self._thinking).split())
            self._thinking = []
            if thought:
                publish("log", f"thinking: {thought[:400]}{'…' if len(thought) > 400 else ''}", source="cursor")

    def feed(self, line: str) -> None:
        line = line.strip()
        if not line:
            return
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            publish("out", line, source="cursor")
            return
        if not isinstance(ev, dict):
            return
        t = ev.get("type")
        sub = ev.get("subtype")
        if t == "thinking":
            if sub == "delta" and isinstance(ev.get("text"), str):
                self._thinking.append(ev["text"])
            elif sub == "completed":
                self.flush()
            return
        self.flush()
        if t == "system" and sub == "init":
            publish("step", f"cursor session started (model {ev.get('model', '?')})", source="cursor")
        elif t == "tool_call":
            name, args, result = _tool_label(ev.get("tool_call") or {})
            arg = next((str(args[k]) for k in _TOOL_ARG_KEYS if args.get(k)), "")
            if sub == "started":
                if name == "shell":
                    publish("cmd", f"$ {arg}", source="cursor", tool=name)
                else:
                    publish("tool", f"{name}: {arg}".rstrip(": "), source="cursor", tool=name)
            elif sub == "completed":
                publish(*_describe_result(name, arg, result), source="cursor", tool=name)
        elif t == "assistant":
            parts = [c.get("text", "") for c in (ev.get("message") or {}).get("content") or []
                     if isinstance(c, dict) and c.get("type") == "text"]
            text = "".join(parts)
            if text:
                self._assistant.append(text)
                publish("out", text, source="cursor")
        elif t == "result":
            if isinstance(ev.get("result"), str):
                self._result = ev["result"]
            secs = (ev.get("duration_ms") or 0) / 1000
            publish("error" if ev.get("is_error") else "step",
                    f"cursor result: {sub or 'done'} in {secs:.1f}s", source="cursor")


def _describe_result(name: str, arg: str, result: Any) -> tuple[str, str]:
    if not isinstance(result, dict) or not result:
        return "out", f"{name} done"
    status, body = next(iter(result.items()))
    body = body if isinstance(body, dict) else {}
    if status == "success":
        out = body.get("stdout") or body.get("output") or body.get("content") or ""
        code = body.get("exitCode", body.get("exit_code"))
        head = f"{name} ok" + (f" (exit {code})" if code is not None else "")
        if isinstance(out, str) and out.strip():
            snippet = out.strip()
            if len(snippet) > 1500:
                snippet = snippet[:1500] + " …"
            return "out", f"{head}\n{snippet}"
        return "out", head
    reason = body.get("reason") or body.get("error") or body.get("message") or ""
    return "error", f"{name} {status}{': ' + str(reason) if reason else ''}{' — ' + arg if arg else ''}"


def _extract_stream_json_text(stdout: str) -> str:
    parts: list[str] = []
    for line in stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            chunk = payload.get("text") or payload.get("content") or payload.get("message")
            if isinstance(chunk, str):
                parts.append(chunk)
    return "".join(parts).strip() or stdout.strip()


def _truncate_for_speech(text: str, max_chars: int = 500) -> str:
    text = " ".join(text.split())
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 3].rsplit(" ", 1)[0] + "..."
