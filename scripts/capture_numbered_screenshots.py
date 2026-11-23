#!/usr/bin/env python3
"""Headless desktop + mobile screenshots of a numbered assistant reply (item 23)."""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _pick_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _venv_python() -> Path:
    v = ROOT / "venv" / "bin" / "python"
    return v if v.is_file() else Path(sys.executable)


def _wait_health(base: str, timeout: float = 45.0) -> None:
    deadline = time.monotonic() + timeout
    url = f"{base}/api/health"
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2) as resp:
                if resp.status == 200:
                    return
        except OSError:
            time.sleep(0.25)
    raise RuntimeError(f"server not healthy at {base}")


def _chrome() -> str:
    for name in ("google-chrome-stable", "google-chrome", "chromium", "chromium-browser"):
        path = shutil.which(name)
        if path:
            return path
    raise RuntimeError("No Chrome/Chromium binary found")


def _wait_conv_api(base: str, cid: int, timeout: float = 30.0) -> None:
    url = f"{base}/api/conversations/{cid}"
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2) as resp:
                data = json.loads(resp.read().decode())
                msgs = [m for m in data.get("messages") or [] if m.get("role") == "assistant"]
                if msgs and (msgs[-1].get("meta") or {}).get("numbered_items"):
                    return
        except OSError:
            pass
        time.sleep(0.2)
    raise RuntimeError("conversation not ready for screenshot")


def _screenshot(chrome: str, user_data: Path, url: str, shot: Path, w: int, h: int) -> None:
    cmd = [
        chrome,
        "--headless=new",
        "--disable-gpu",
        "--hide-scrollbars",
        f"--window-size={w},{h}",
        f"--screenshot={shot}",
        f"--user-data-dir={user_data}",
        "--timeout=12000",
        url,
    ]
    subprocess.run(cmd, check=True, timeout=55)


def main() -> int:
    default_out = ROOT / "data" / "test-artifacts"
    out_dir = Path(os.environ.get("NUMBERED_SCREENSHOT_DIR", default_out))
    out_dir.mkdir(parents=True, exist_ok=True)
    temp_dir = Path(tempfile.mkdtemp(prefix="sahir-numbered-shot-"))
    port = _pick_free_port()
    env = os.environ.copy()
    env["WEB_HOST"] = "127.0.0.1"
    env["WEB_PORT"] = str(port)
    env["AGENT_DB_PATH"] = str(temp_dir / "agent.db")
    env["WEB_DB_PATH"] = str(temp_dir / "web.db")
    env["WEB_SSL_CERT"] = ""
    env["WEB_SSL_KEY"] = ""
    env["WEB_AUTH_ENABLED"] = "false"

    sys.path.insert(0, str(ROOT))
    from agent.numbered_replies import format_tts

    from agent.store import AgentStore

    store = AgentStore(temp_dir / "agent.db")
    conv = store.create_conversation("Numbered demo")
    cid = int(conv["id"])
    items = [
        {"n": 1, "text": "Check system load", "action": None},
        {"n": 2, "text": "Run uptime", "action": {"kind": "run_command", "command": "uptime", "args": []}},
    ]
    store.add_message(
        cid,
        "assistant",
        "1. Check system load\n2. Run uptime",
        {
            "numbered_items": items,
            "show_numbered_list": True,
            "tts_text": format_tts(items),
        },
    )

    proc = subprocess.Popen(
        [str(_venv_python()), str(ROOT / "bot.py"), "--web"],
        cwd=str(ROOT),
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    base = f"http://127.0.0.1:{port}"
    user_data = Path(tempfile.mkdtemp(prefix="sahir-chrome-ud-"))
    try:
        _wait_health(base)
        _wait_conv_api(base, cid)
        chrome = _chrome()
        page_url = f"{base}/?conv={cid}"
        # Let chatbar async openChat finish (breadcrumb, list render, scroll).
        time.sleep(3.0)
        for label, w, h in (("desktop", 1440, 900), ("mobile", 390, 844)):
            shot = out_dir / f"numbered-reply-{label}.png"
            _screenshot(chrome, user_data, page_url, shot, w, h)
            time.sleep(0.5)
            if not shot.is_file() or shot.stat().st_size < 5000:
                raise RuntimeError(f"screenshot too small or missing: {shot}")
        print(f"Screenshots written to {out_dir}")
        return 0
    finally:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=20)
            except subprocess.TimeoutExpired:
                proc.kill()
        shutil.rmtree(user_data, ignore_errors=True)
        shutil.rmtree(temp_dir, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())