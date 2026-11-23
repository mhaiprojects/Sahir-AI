"""Render systemd user unit templates (no stale paths, expected directives)."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SYSTEMD = ROOT / "systemd"
INSTALL = ROOT / "scripts" / "install_systemd.sh"


def _render(unit: str, root: Path) -> str:
    raw = (SYSTEMD / unit).read_text(encoding="utf-8")
    return raw.replace("@ROOT@", str(root))


def test_templates_have_no_stale_paths() -> None:
    for path in SYSTEMD.glob("*.service"):
        text = path.read_text(encoding="utf-8")
        assert "_to-del" not in text
        assert "@ROOT@" in text
        assert "bot.py" in text


def test_rendered_web_unit_paths_and_exec() -> None:
    fake_root = Path("/home/example/Sahir-AI")
    text = _render("voice-assistant-web.service", fake_root)
    assert "_to-del" not in text
    assert f"WorkingDirectory={fake_root}" in text
    assert f"ExecStart={fake_root}/venv/bin/python bot.py --web" in text
    assert f"EnvironmentFile=-{fake_root}/.env" in text
    assert "After=network-online.target" in text
    assert f"ExecStartPre=-{fake_root}/scripts/wait_ollama.sh" in text
    assert "Restart=on-failure" in text
    assert "WEB_AUTH_TOKEN_FILE" in text


def test_install_script_documents_enable_and_status() -> None:
    body = INSTALL.read_text(encoding="utf-8")
    assert "--enable" in body and "--disable" in body and "--status" in body
    assert "voice-assistant.sh stop" in body
    assert "daemon-reload" in body