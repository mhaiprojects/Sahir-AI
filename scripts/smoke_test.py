#!/usr/bin/env python3
"""Smoke tests that do not require a microphone or Ollama."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from config import Settings
from tools.cursor_cli import run_cursor_agent
from tools.pc_control import run_command


def main() -> int:
    settings = Settings.from_env()
    errors: list[str] = []

    if not settings.piper_model_path.exists():
        errors.append(f"Missing Piper model: {settings.piper_model_path}")

    uptime = run_command("uptime", [], settings)
    if not uptime.success:
        errors.append(f"PC control failed: {uptime.message}")
    else:
        print("PC control:", uptime.message[:60])

    cursor = run_cursor_agent(
        "Reply with the single word: ready",
        workspace=settings.cursor_workspace,
        model=settings.cursor_model,
        agent_bin=settings.cursor_agent_bin,
        api_key=settings.cursor_api_key,
        timeout_seconds=90,
    )
    if cursor.returncode != 0:
        errors.append(f"Cursor CLI failed: {cursor.text}")
    else:
        print("Cursor CLI:", cursor.text[:80])

    if errors:
        for err in errors:
            print("ERROR:", err, file=sys.stderr)
        return 1

    print("Smoke tests passed (Ollama/mic not required).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
