#!/usr/bin/env python3
"""Tests for markdown speech stripping."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from markdown_speech import strip_markdown_for_speech


def main() -> int:
    cases = [
        ("**Hello** world", "Hello world"),
        ("Use `npm install` here", "Use npm install here"),
        ("See [docs](https://example.com)", "See docs"),
        ("# Title\n\nParagraph", "Title Paragraph"),
        ("- one\n- two", "one two"),
        ("~~old~~ new", "old new"),
        ("```python\nprint('x')\n```\nDone", "Done"),
    ]
    failed = 0
    for source, expected in cases:
        got = strip_markdown_for_speech(source)
        if got != expected:
            print(f"FAIL: {source!r}\n  expected: {expected!r}\n  got:      {got!r}")
            failed += 1
        else:
            print(f"ok: {source!r} -> {got!r}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
