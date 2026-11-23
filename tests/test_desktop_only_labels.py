"""Item 12: desktop-only labels for wake word and Right Ctrl PTT (bot.py, not browser)."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "web" / "static"
DESKTOP_PHRASE = "Desktop app only"
BOT_PY = "bot.py"


def _read(name: str) -> str:
    return (STATIC / name).read_text(encoding="utf-8")


def test_index_html_desktop_only_wake_and_ptt() -> None:
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    assert "desktop-only-voice-note" in html
    assert "settings-desktop-only-voice" in html
    assert DESKTOP_PHRASE in html
    assert BOT_PY in html
    assert "hey_jarvis" in html
    assert "Right Ctrl" in html
    assert "not available in the browser" in html.lower()


def test_app_js_desktop_only_constants() -> None:
    js = _read("app.js")
    assert "DESKTOP_ONLY_VOICE_NOTE" in js
    assert DESKTOP_PHRASE in js
    assert "pttHotkeyLabel" in js
    assert "updateDesktopOnlyVoiceLabels" in js


def test_tour_js_desktop_only_step() -> None:
    tour = _read("tour.js")
    assert DESKTOP_PHRASE in tour or "desktop app only" in tour.lower()
    assert "hey_jarvis" in tour or "Wake word" in tour
    assert "Right Ctrl" in tour
    assert BOT_PY in tour
    assert "desktop-only-voice-note" in tour