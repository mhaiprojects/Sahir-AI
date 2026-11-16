#!/usr/bin/env python3
"""Unit tests for router history and Cursor delegation helpers."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools.router import (
    _build_cursor_prompt,
    _looks_like_code_task,
    _normalize_history,
    _reply_suggests_incomplete,
)


def main() -> int:
    failed = 0

    history = _normalize_history(
        [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "hi"},
            {"role": "system", "content": "ignored"},
            {"role": "user", "content": ""},
        ]
    )
    if len(history) != 2:
        print("FAIL: normalize history length", history)
        failed += 1
    else:
        print("ok: normalize history")

    if not _looks_like_code_task("fix the bug in app.py"):
        print("FAIL: code task detection")
        failed += 1
    else:
        print("ok: code task detection")

    if not _reply_suggests_incomplete("I can't access files on your machine."):
        print("FAIL: incomplete reply detection")
        failed += 1
    else:
        print("ok: incomplete reply detection")

    prompt = _build_cursor_prompt("update tests", history)
    if "Conversation history:" not in prompt or "update tests" not in prompt:
        print("FAIL: cursor prompt", prompt)
        failed += 1
    else:
        print("ok: cursor prompt")

    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
