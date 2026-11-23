"""Cursor CLI wrapper: argv construction and stream-json shell rejection handling."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from activity import ActivityBus
import activity
from tools.cursor_cli import (
    _StreamEvents,
    _append_shell_rejection_notes,
    build_cursor_agent_command,
)


def test_build_cursor_agent_command_flags() -> None:
    cmd = build_cursor_agent_command(
        "do task",
        workspace=Path("/tmp/ws"),
        model="composer-2.5",
        force=True,
        sandbox="disabled",
        auto_review=True,
    )
    assert "--force" in cmd
    assert cmd[cmd.index("--sandbox") + 1] == "disabled"
    assert "--auto-review" in cmd
    assert cmd[-1] == "do task"


def test_build_cursor_agent_command_respects_force_off() -> None:
    cmd = build_cursor_agent_command("x", workspace=Path("/w"), model="m", force=False)
    assert "--force" not in cmd


def test_shell_rejection_fixture_activity_and_reply(monkeypatch: pytest.MonkeyPatch) -> None:
    bus = ActivityBus()
    monkeypatch.setattr(activity, "bus", bus)
    fixture = Path(__file__).parent / "fixtures" / "cursor_shell_rejected.jsonl"
    ev = _StreamEvents()
    for line in fixture.read_text(encoding="utf-8").splitlines():
        ev.feed(line + "\n")
    assert ev.text() == "Could not run shell."
    notes = ev.shell_rejection_reply_lines()
    assert len(notes) == 1
    assert notes[0].startswith("Cursor declined to run: echo sahir-shell-ok && ls —")
    merged = _append_shell_rejection_notes(ev.text(), ev)
    assert "Cursor declined to run:" in merged
    errors = [e for e in bus.history() if e["kind"] == "error"]
    assert any("Cursor declined to run:" in e["text"] for e in errors)
    assert all(e["level"] == "error" for e in errors)


def test_settings_pass_force_from_config() -> None:
    from config import Settings

    s = Settings(cursor_agent_force=False, cursor_agent_sandbox="enabled", cursor_agent_auto_review=True)
    sandbox = s.cursor_agent_sandbox or None
    cmd = build_cursor_agent_command(
        "p",
        workspace=Path("/w"),
        model="m",
        force=s.cursor_agent_force,
        sandbox=sandbox,
        auto_review=s.cursor_agent_auto_review,
    )
    assert "--force" not in cmd
    assert "--sandbox" in cmd and "enabled" in cmd