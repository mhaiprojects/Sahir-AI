"""Wrap Cursor CLI for voice-triggered dev tasks."""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

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

    cmd = [
        resolve_agent_bin(agent_bin),
        "-p",
        "--output-format",
        "stream-json" if stream_partial else "text",
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

    logger.info("Running Cursor agent: %s", " ".join(cmd[:-1]))
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            cwd=str(workspace),
            env=env,
        )
    except subprocess.TimeoutExpired:
        return CursorResult(
            text="The Cursor agent timed out before finishing.",
            returncode=124,
        )
    except FileNotFoundError:
        return CursorResult(
            text="cursor-agent was not found. Install Cursor CLI and ensure it is on PATH.",
            returncode=127,
        )

    if result.returncode != 0:
        err = (result.stderr or result.stdout or "Unknown error").strip()
        return CursorResult(
            text=f"Cursor agent failed: {err[:300]}",
            returncode=result.returncode,
        )

    if stream_partial:
        text = _extract_stream_json_text(result.stdout)
    else:
        text = result.stdout.strip()

    if not text:
        text = "The Cursor agent finished but returned no text."

    return CursorResult(text=_truncate_for_speech(text), returncode=0)


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
