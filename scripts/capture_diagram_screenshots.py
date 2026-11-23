#!/usr/bin/env python3
"""Headless desktop + mobile screenshot of a Mermaid diagram message (item 27)."""

from __future__ import annotations

import base64
import json
import os
import re
import shutil
import socket
import struct
import subprocess
import sys
import tempfile
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent

# Legacy bare `title …` lines are invalid in Mermaid 11; store renderable source anyway.
_MERMAID = """flowchart LR
    A[User] --> B[Sahir]
    B --> C[Diagram]
"""

_DIAGRAM_DOM_JS = """(() => {
  const mount = document.querySelector(".diagram-render");
  if (!mount) return { ok: false, reason: "no .diagram-render" };
  const err = mount.textContent || "";
  if (err.startsWith("Diagram render failed")) return { ok: false, reason: err.slice(0, 200) };
  const svg = mount.querySelector("svg");
  if (!svg || svg.childElementCount < 1) return { ok: false, reason: "no svg children" };
  return { ok: true, html: mount.innerHTML.slice(0, 400) };
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


def _ws_connect(ws_url: str) -> socket.socket:
    parsed = urllib.parse.urlparse(ws_url)
    host = parsed.hostname or "127.0.0.1"
    port = parsed.port or 80
    path = parsed.path or "/"
    if parsed.query:
        path = f"{path}?{parsed.query}"
    sock = socket.create_connection((host, port), timeout=10)
    key = base64.b64encode(os.urandom(16)).decode()
    req = (
        f"GET {path} HTTP/1.1\r\n"
        f"Host: {host}:{port}\r\n"
        f"Upgrade: websocket\r\n"
        f"Connection: Upgrade\r\n"
        f"Sec-WebSocket-Key: {key}\r\n"
        f"Sec-WebSocket-Version: 13\r\n"
        f"\r\n"
    ).encode()
    sock.sendall(req)
    resp = b""
    while b"\r\n\r\n" not in resp:
        chunk = sock.recv(4096)
        if not chunk:
            raise RuntimeError("websocket handshake failed")
        resp += chunk
    return sock


def _ws_send(sock: socket.socket, message: str) -> None:
    data = message.encode()
    mask = os.urandom(4)
    if len(data) < 126:
        header = struct.pack("!BB", 0x81, 0x80 | len(data)) + mask
    elif len(data) < 65536:
        header = struct.pack("!BBH", 0x81, 0xFE, len(data)) + mask
    else:
        header = struct.pack("!BBQ", 0x81, 0xFF, len(data)) + mask
    masked = bytes(b ^ mask[i % 4] for i, b in enumerate(data))
    sock.sendall(header + masked)


def _ws_recv(sock: socket.socket) -> str:
    while True:
        hdr = sock.recv(2)
        if len(hdr) < 2:
            raise RuntimeError("websocket closed")
        b0, b1 = hdr[0], hdr[1]
        opcode = b0 & 0x0F
        masked = bool(b1 & 0x80)
        length = b1 & 0x7F
        if length == 126:
            length = struct.unpack("!H", sock.recv(2))[0]
        elif length == 127:
            length = struct.unpack("!Q", sock.recv(8))[0]
        mask = sock.recv(4) if masked else b""
        payload = b""
        while len(payload) < length:
            payload += sock.recv(length - len(payload))
        if masked:
            payload = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        if opcode == 0x1:
            return payload.decode("utf-8", errors="replace")
        if opcode == 0x8:
            raise RuntimeError("websocket closed by peer")


def _cdp_call(sock: socket.socket, msg_id: int, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    _ws_send(sock, json.dumps({"id": msg_id, "method": method, "params": params or {}}))
    while True:
        data = json.loads(_ws_recv(sock))
        if data.get("id") == msg_id:
            if "error" in data:
                raise RuntimeError(data["error"])
            return data.get("result", {})


def _cdp_eval(sock: socket.socket, msg_id: int, expression: str) -> dict[str, Any]:
    return _cdp_call(
        sock,
        msg_id,
        "Runtime.evaluate",
        {"expression": expression, "returnByValue": True, "awaitPromise": True},
    )


def _cdp_screenshot(sock: socket.socket, shot: Path, w: int, h: int, msg_id: int) -> None:
    _cdp_call(
        sock,
        msg_id,
        "Emulation.setDeviceMetricsOverride",
        {
            "width": w,
            "height": h,
            "deviceScaleFactor": 1,
            "mobile": w < 500,
        },
    )
    cap = _cdp_call(sock, msg_id + 1, "Page.captureScreenshot", {"format": "png", "captureBeyondViewport": True})
    shot.write_bytes(base64.b64decode(cap["data"]))


def _capture_diagram_session(
    chrome: str,
    user_data: Path,
    url: str,
    shots: list[tuple[Path, int, int]],
    *,
    settle_s: float = 22.0,
    timeout_s: float = 55.0,
) -> str:
    """Headless Chrome + DevTools: wait for diagram SVG, assert, write PNGs in the same session."""
    dbg_port = _pick_free_port()
    proc = subprocess.Popen(
        [
            chrome,
            "--headless=new",
            "--disable-gpu",
            f"--user-data-dir={user_data}",
            f"--remote-debugging-port={dbg_port}",
            "--no-first-run",
            "--no-default-browser-check",
            url,
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    ws_url = ""
    deadline = time.monotonic() + timeout_s
    try:
        while time.monotonic() < deadline:
            try:
                targets = json.loads(
                    urllib.request.urlopen(f"http://127.0.0.1:{dbg_port}/json/list", timeout=2).read()
                )
                page = next((t for t in targets if t.get("type") == "page"), None)
                if page:
                    ws_url = page.get("webSocketDebuggerUrl") or ""
                    if ws_url:
                        break
            except OSError:
                pass
            time.sleep(0.25)
        if not ws_url:
            raise RuntimeError("Chrome DevTools not ready")
        sock = _ws_connect(ws_url)
        try:
            _cdp_call(sock, 1, "Page.enable")
            _cdp_call(sock, 2, "Runtime.enable")
            time.sleep(settle_s)
            result = _cdp_eval(sock, 3, _DIAGRAM_DOM_JS)
            value = (result.get("result") or {}).get("value")
            if not value or not value.get("ok"):
                reason = (value or {}).get("reason") if isinstance(value, dict) else value
                raise RuntimeError(f"diagram not rendered: {reason}")
            dom_dump = _cdp_eval(
                sock,
                4,
                'document.querySelector(".diagram-render")?.outerHTML || ""',
            )
            html = (dom_dump.get("result") or {}).get("value") or ""
            if "<svg" not in html:
                raise RuntimeError("`.diagram-render svg` missing in live DOM")
            msg_id = 10
            for path, w, h in shots:
                _cdp_screenshot(sock, path, w, h, msg_id)
                msg_id += 2
            return html
        finally:
            sock.close()
    finally:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=8)
            except subprocess.TimeoutExpired:
                proc.kill()


def _assert_diagram_svg_in_dom(dom: str) -> None:
    if "Diagram render failed" in dom:
        raise RuntimeError(f"diagram render error in DOM: {dom[:200]}")
    svg_m = re.search(r"<svg\b[^>]*>(.+)</svg>", dom, re.S | re.I)
    if not svg_m or not svg_m.group(1).strip():
        raise RuntimeError("`.diagram-render svg` missing or empty in DOM")


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

    sys.path.insert(0, str(ROOT))
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
    user_data = Path(tempfile.mkdtemp(prefix="sahir-chrome-ud-"))
    try:
        _wait_health(base)
        chrome = _chrome()
        page_url = f"{base}/?conv={cid}&view=chat"
        shot_jobs = [
            (out_dir / f"diagram-reply-{label}.png", w, h)
            for label, w, h in (("desktop", 1440, 900), ("mobile", 390, 844))
        ]
        dom = _capture_diagram_session(chrome, user_data, page_url, shot_jobs)
        _assert_diagram_svg_in_dom(dom)
        shots = [p for p, _, _ in shot_jobs]
        for shot in shots:
            if not shot.is_file() or shot.stat().st_size < 8000:
                raise RuntimeError(f"screenshot too small or missing: {shot}")
        if shots[0].stat().st_size < 12000:
            raise RuntimeError("desktop screenshot likely missing rendered SVG (too small)")
        print(json.dumps({"screenshots": [str(p) for p in shots]}))
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