"""Chat view layout: flex column, tour automation, capture helpers."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "web" / "static"


def test_chat_view_flex_layout_css():
    css = (STATIC / "unified.css").read_text(encoding="utf-8")
    assert ".view-chat.active" in css
    assert ".view-chat .main-content" in css
    assert ".view-chat .chat-log" in css
    assert "@media (min-width: 981px)" in css
    assert "min-height: 180px" in css
    assert "min-height: 450px" not in css
    assert ".diagram-render" in css and "60vh" in css


def test_tour_automation_suppression():
    tour = (STATIC / "tour.js").read_text(encoding="utf-8")
    assert 'qs.has("notour")' in tour
    assert "voice-assistant-tour-done" in tour


def test_capture_builds_automation_url():
    from scripts.capture_web_helpers import build_automation_url

    url = build_automation_url("http://127.0.0.1:9", conv=3, view="chat")
    assert "notour=1" in url
    assert "wip=collapsed" in url
    assert "conv=3" in url
    assert "view=chat" in url


def test_capture_layout_probe_defined():
    from scripts import capture_web_helpers as cap

    assert "conv-actions" in cap._LAYOUT_PROBE_JS
    assert "composer_bottom" in cap._LAYOUT_PROBE_JS


def test_index_chat_log_before_composer():
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    assert html.find('id="chat-log"') < html.find('id="chat-form"')
    assert 'conv-actions-menu" id="conv-actions-menu" open' in html


def test_mobile_chat_bar_compact_controls():
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    css = (STATIC / "unified.css").read_text(encoding="utf-8")
    assert 'id="chat-bar-sheet-toggle"' in html
    assert 'id="chat-bar-mobile-summary"' in html
    assert "min-height: 56px" in css and "#mic-btn" in css
    assert "min-height: 180px" in css


def test_conv_actions_menu_sync():
    js = (STATIC / "chatbar.js").read_text(encoding="utf-8")
    assert "syncConvActionsMenu" in js


def test_wip_collapsed_query():
    js = (STATIC / "wip.js").read_text(encoding="utf-8")
    assert 'wipQs.get("wip") === "collapsed"' in js


def test_mobile_main_shell_grid():
    css = (STATIC / "panels.css").read_text(encoding="utf-8")
    assert "minmax(0, 1fr) auto" in css