#!/usr/bin/env python3
"""Headless desktop + mobile screenshot of a Mermaid diagram message (item 27)."""

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
sys.path.insert(0, str(ROOT))
from scripts import capture_web_helpers as cap

_MERMAID = """flowchart LR
    A[User] --> B[Sahir]
    B --> C[Diagram]
"""

_DIAGRAM_READY_JS = """(() => {
  const mount = document.querySelector(".diagram-render");
  if (!mount) return { ok: false, reason: "no .diagram-render" };
  const err = mount.textContent || "";
  if (err.startsWith("Diagram render failed")) return { ok: false, reason: err.slice(0, 200) };
  const svg = mount.querySelector("svg");
  if (!svg || svg.childElementCount < 1) return { ok: false, reason: "no svg children" };
  return { ok: true };
})()"""

_CAPTURES = (
    ("desktop", 1440, 900, "diagram-reply-desktop.png"),
    ("desktop1366", 1366, 768, "diagram-reply-desktop1366.png"),
    ("mobile", 390, 844, "diagram-reply-mobile.png"),
    ("mobile500", 500, 757, None),
)


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


def main() -> int:
    out_dir = Path(os.environ.get("DIAGRAM_SCREENSHOT_DIR", ROOT / "data" / "test-artifacts"))
    out_dir.mkdir(parents=True, exist_ok=True)
    temp_dir = Path(tempfile.mkdtemp(prefix="sahir-diagram-shot-"))
    port = _pick_free_port()
    env = os.environ.copy()
    env["WEB_HOST"] = "127.0.0.1"
    env["WEB_PORT"] = str(port)
    env["AGENT_DB_PATH"] = str(temp_dir / "agent.db")
    env["WEB_DB_PATH"] = str(temp_dir / "web.db")
    env["WEB_SSL_CERT"] = ""
    env["WEB_SSL_KEY"] = ""
    env["WEB_AUTH_ENABLED"] = "false"

    from agent.mermaid_validate import normalize_mermaid_source
    from agent.store import AgentStore

    store = AgentStore(temp_dir / "agent.db")
    conv = store.create_conversation("Diagram demo")
    cid = int(conv["id"])
    mermaid_src = normalize_mermaid_source(_MERMAID)
    store.add_message(
        cid,
        "assistant",
        "Sample diagram",
        {
            "kind": "diagram",
            "mermaid": mermaid_src,
            "diagram_title": "E2E diagram",
            "diagram_steps": 3,
            "tts_text": "Here's the diagram: E2E diagram, with 3 steps.",
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
    try:
        _wait_health(base)
        chrome = _chrome()
        page_url = cap.build_automation_url(base, conv=cid, view="chat")
        report: dict[str, object] = {
            "screenshots": [],
            "chat_log_client_heights": {},
            "composer_positions": {},
        }
        for label, w, h, fname in _CAPTURES:
            shot = out_dir / fname if fname else None
            metrics = cap.capture_viewport(
                chrome,
                page_url,
                shot,
                w,
                h,
                settle_s=22.0,
                ready_js=_DIAGRAM_READY_JS,
            )
            if not metrics.get("tour_hidden"):
                raise RuntimeError("guided tour overlay visible during capture")
            report["chat_log_client_heights"][label] = metrics["chat_log_client_height"]
            report["composer_positions"][label] = {
                "bottom": metrics.get("composer_bottom"),
                "inner_height": metrics.get("inner_height"),
                "layout": metrics.get("layout"),
            }
            if shot is not None:
                if not shot.is_file() or shot.stat().st_size < 8000:
                    raise RuntimeError(f"screenshot too small or missing: {shot}")
                report["screenshots"].append(str(shot))
        desktop_shot = out_dir / "diagram-reply-desktop.png"
        if desktop_shot.stat().st_size < 12000:
            raise RuntimeError("desktop screenshot likely missing rendered SVG (too small)")
        print(json.dumps(report))
        return 0
    finally:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=20)
            except subprocess.TimeoutExpired:
                proc.kill()
        shutil.rmtree(temp_dir, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())