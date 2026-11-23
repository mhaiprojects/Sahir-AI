#!/usr/bin/env python3
"""Headless check: #chat-empty-state visibility when switching conversations (item 31)."""

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
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts import capture_web_helpers as cap

_READY_PROBE_JS = """(() => {
  const el = document.getElementById("chat-empty-state");
  if (!el) return { visible: false, reason: "missing #chat-empty-state" };
  const cs = getComputedStyle(el);
  const visible =
    cs.display !== "none" &&
    cs.visibility !== "hidden" &&
    parseFloat(cs.opacity || "1") > 0.01 &&
    !el.classList.contains("hidden");
  const log = document.getElementById("chat-log");
  const userAsst = log ? log.querySelectorAll(".msg.user, .msg.assistant").length : -1;
  return { visible, display: cs.display, user_asst: userAsst };
})()"""


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


def _stop_server(proc: subprocess.Popen[bytes]) -> None:
    if proc.poll() is not None:
        return
    proc.terminate()
    try:
        proc.wait(timeout=8)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=3)


def _wait_dom_ready(sock: Any, msg_id: int, *, timeout_s: float = 25.0) -> int:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        probe = cap.cdp_eval(
            sock,
            msg_id,
            '(() => !!(window.sahirOpenConversation && document.getElementById("chat-empty-state")))()',
        )
        msg_id += 1
        if (probe.get("result") or {}).get("value"):
            return msg_id
        time.sleep(0.2)
    raise RuntimeError("page did not expose sahirOpenConversation / empty state")


def _assert_visible(sock: Any, msg_id: int, step: str) -> tuple[int, dict[str, Any]]:
    result = cap.cdp_eval(sock, msg_id, _READY_PROBE_JS)
    msg_id += 1
    value = (result.get("result") or {}).get("value") or {}
    if not value.get("visible"):
        raise RuntimeError(f"{step}: expected Ready visible, got {value}")
    return msg_id, value


def _assert_hidden(sock: Any, msg_id: int, step: str) -> tuple[int, dict[str, Any]]:
    result = cap.cdp_eval(sock, msg_id, _READY_PROBE_JS)
    msg_id += 1
    value = (result.get("result") or {}).get("value") or {}
    if value.get("visible"):
        raise RuntimeError(f"{step}: expected Ready hidden, got {value}")
    return msg_id, value


def run_check(chrome: str, base: str, conv_with_msgs: int, conv_empty: int) -> None:
    user_data = Path(tempfile.mkdtemp(prefix="sahir-ready-chrome-ud-"))
    dbg_port = cap._pick_free_port()
    url = cap.build_automation_url(base, conv=conv_empty, view="chat")
    proc: subprocess.Popen[bytes] | None = None
    try:
        proc = subprocess.Popen(
            [
                chrome,
                "--headless=new",
                "--disable-gpu",
                "--disable-breakpad",
                "--window-size=1280,900",
                f"--user-data-dir={user_data}",
                f"--remote-debugging-port={dbg_port}",
                "--no-first-run",
                "--no-default-browser-check",
                url,
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        ws_url = ""
        deadline = time.monotonic() + 50.0
        while time.monotonic() < deadline:
            try:
                targets = json.loads(
                    urllib.request.urlopen(f"http://127.0.0.1:{dbg_port}/json/list", timeout=2).read()
                )
                page = next((t for t in targets if t.get("type") == "page"), None)
                if page and page.get("webSocketDebuggerUrl"):
                    ws_url = page["webSocketDebuggerUrl"]
                    break
            except OSError:
                pass
            time.sleep(0.25)
        if not ws_url:
            raise RuntimeError("Chrome DevTools not ready")

        sock = cap._ws_connect(ws_url)
        try:
            cap._cdp_call(sock, 1, "Page.enable")
            cap._cdp_call(sock, 2, "Runtime.enable")
            cap.cdp_eval(sock, 3, cap._AUTOMATION_BOOT_JS)
            msg_id = _wait_dom_ready(sock, 4)
            time.sleep(2.0)

            msg_id, _ = _assert_visible(sock, msg_id, "initial empty conversation")

            open_js = f"""(async () => {{
              await window.sahirOpenConversation({conv_with_msgs});
              await new Promise((r) => setTimeout(r, 400));
              return true;
            }})()"""
            cap.cdp_eval(sock, msg_id, open_js)
            msg_id += 1
            msg_id, _ = _assert_hidden(sock, msg_id, "conversation with messages")

            open_empty_js = f"""(async () => {{
              await window.sahirOpenConversation({conv_empty});
              await new Promise((r) => setTimeout(r, 400));
              return true;
            }})()"""
            cap.cdp_eval(sock, msg_id, open_empty_js)
            msg_id += 1
            msg_id, _ = _assert_visible(sock, msg_id, "switch back to empty conversation")

            new_chat_js = """(async () => {
              const res = await fetch("/api/conversations", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({}),
              });
              if (!res.ok) throw new Error("create conversation failed: " + res.status);
              const conv = await res.json();
              await window.sahirOpenConversation(conv.id);
              await new Promise((r) => setTimeout(r, 400));
              return conv.id;
            })()"""
            cap.cdp_eval(sock, msg_id, new_chat_js)
            msg_id += 1
            msg_id, _ = _assert_visible(sock, msg_id, "new chat")
        finally:
            sock.close()
    finally:
        if proc is not None:
            cap.stop_chrome_process_group(proc)
        cap.remove_chrome_user_data_dir(user_data)


def main() -> int:
    temp_dir = Path(tempfile.mkdtemp(prefix="sahir-ready-check-"))
    port = _pick_free_port()
    env = os.environ.copy()
    env["WEB_HOST"] = "127.0.0.1"
    env["WEB_PORT"] = str(port)
    env["AGENT_DB_PATH"] = str(temp_dir / "agent.db")
    env["WEB_DB_PATH"] = str(temp_dir / "web.db")
    env["WEB_SSL_CERT"] = ""
    env["WEB_SSL_KEY"] = ""
    env["WEB_AUTH_ENABLED"] = "false"

    from agent.store import AgentStore

    store = AgentStore(temp_dir / "agent.db")
    conv_a = store.create_conversation("With messages")
    conv_a_id = int(conv_a["id"])
    store.add_message(conv_a_id, "user", "Hello from check_ready_state")
    store.add_message(conv_a_id, "assistant", "Hi there.")
    conv_b = store.create_conversation("Empty latest")
    conv_b_id = int(conv_b["id"])

    proc = subprocess.Popen(
        [str(_venv_python()), str(ROOT / "bot.py"), "--web"],
        cwd=str(ROOT),
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
    base = f"http://127.0.0.1:{port}"
    try:
        _wait_health(base)
        chrome = _chrome()
        run_check(chrome, base, conv_a_id, conv_b_id)
        print("check_ready_state: OK")
        return 0
    except Exception as exc:
        err = ""
        if proc.stderr:
            err = proc.stderr.read().decode(errors="replace")[:800]
        print(f"check_ready_state: FAIL — {exc}", file=sys.stderr)
        if err:
            print(err, file=sys.stderr)
        return 1
    finally:
        _stop_server(proc)
        shutil.rmtree(temp_dir, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())