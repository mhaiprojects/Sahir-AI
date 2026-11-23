"""Shared headless Chrome helpers for Sahir web capture scripts (tour off, CDP, layout metrics)."""

from __future__ import annotations

import base64
import json
import os
import socket
import struct
import subprocess
import tempfile
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

TOUR_STORAGE_KEY = "voice-assistant-tour-done"


_LAYOUT_PROBE_JS = """(() => {
  const narrow = window.innerWidth < 981;
  const form = document.getElementById("chat-form");
  const log = document.getElementById("chat-log");
  const actions = document.getElementById("conv-actions");
  const more = document.querySelector(".conv-actions-more");
  const rename = document.getElementById("conv-rename");
  let actionsOk = false;
  if (actions && !actions.hidden) {
    if (narrow) {
      actionsOk = !!more && more.getBoundingClientRect().width > 0;
    } else {
      actionsOk = !!rename && rename.getBoundingClientRect().width > 0;
    }
  }
  const rect = form?.getBoundingClientRect();
  const composerBottom = rect ? rect.bottom : 0;
  const composerOk = !!form && composerBottom > 0 && composerBottom <= window.innerHeight + 8;
  return {
    ok: actionsOk && composerOk,
    actions_ok: actionsOk,
    composer_ok: composerOk,
    composer_bottom: Math.round(composerBottom),
    inner_height: window.innerHeight,
    chat_log_client_height: log ? log.clientHeight : 0,
    narrow,
  };
})()"""

_AUTOMATION_BOOT_JS = """(() => {
  try {
    localStorage.setItem("voice-assistant-tour-done", "1");
    localStorage.setItem("sahir-wip-collapsed", "1");
  } catch (e) { /* ignore */ }
  const wp = document.getElementById("wip-panel");
  if (wp) {
    wp.classList.add("collapsed");
    document.body.classList.add("wip-collapsed");
  }
  const menu = document.getElementById("conv-actions-menu");
  if (menu) {
    if (window.innerWidth < 981) menu.removeAttribute("open");
    else menu.setAttribute("open", "");
  }
  return true;
})()"""


def automation_query_params() -> dict[str, str]:
    return {"notour": "1", "wip": "collapsed"}


def build_automation_url(base: str, **extra: int | str) -> str:
    params = {**automation_query_params(), **{k: str(v) for k, v in extra.items()}}
    q = urllib.parse.urlencode(params)
    return f"{base.rstrip('/')}/?{q}"


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


def cdp_eval(sock: socket.socket, msg_id: int, expression: str) -> dict[str, Any]:
    return _cdp_call(
        sock,
        msg_id,
        "Runtime.evaluate",
        {"expression": expression, "returnByValue": True, "awaitPromise": True},
    )


def _pick_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def capture_viewport(
    chrome: str,
    url: str,
    shot: Path | None,
    width: int,
    height: int,
    *,
    settle_s: float = 20.0,
    ready_js: str | None = None,
    user_data: Path | None = None,
    require_layout: bool = True,
) -> dict[str, Any]:
    """One Chrome session at fixed window size (set before navigation); CDP screenshot, no post-load resize."""
    own_ud = user_data is None
    user_data = user_data or Path(tempfile.mkdtemp(prefix="sahir-chrome-ud-"))
    dbg_port = _pick_free_port()
    proc = subprocess.Popen(
        [
            chrome,
            "--headless=new",
            "--disable-gpu",
            f"--window-size={width},{height}",
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
    deadline = time.monotonic() + 50.0
    metrics: dict[str, Any] = {
        "width": width,
        "height": height,
        "chat_log_client_height": 0,
        "layout": {},
    }
    try:
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
        sock = _ws_connect(ws_url)
        try:
            _cdp_call(sock, 1, "Page.enable")
            _cdp_call(sock, 2, "Runtime.enable")
            cdp_eval(sock, 3, _AUTOMATION_BOOT_JS)
            time.sleep(settle_s)
            msg_id = 4
            if ready_js:
                result = cdp_eval(sock, msg_id, ready_js)
                msg_id += 1
                value = (result.get("result") or {}).get("value")
                if not value or (isinstance(value, dict) and not value.get("ok")):
                    reason = value.get("reason") if isinstance(value, dict) else value
                    raise RuntimeError(f"page not ready for capture: {reason}")
            layout_result = cdp_eval(sock, msg_id, _LAYOUT_PROBE_JS)
            msg_id += 1
            layout = (layout_result.get("result") or {}).get("value") or {}
            metrics["layout"] = layout
            metrics["chat_log_client_height"] = int(layout.get("chat_log_client_height") or 0)
            metrics["composer_bottom"] = int(layout.get("composer_bottom") or 0)
            metrics["inner_height"] = int(layout.get("inner_height") or 0)
            if require_layout and not layout.get("ok"):
                raise RuntimeError(
                    f"layout check failed at {width}x{height}: "
                    f"actions_ok={layout.get('actions_ok')} composer_ok={layout.get('composer_ok')} "
                    f"composer_bottom={layout.get('composer_bottom')} inner_height={layout.get('inner_height')}"
                )
            tour = cdp_eval(
                sock,
                msg_id,
                '(() => { const o = document.getElementById("tour-overlay"); '
                "if (!o) return true; return o.classList.contains('hidden'); })()",
            )
            msg_id += 1
            metrics["tour_hidden"] = bool((tour.get("result") or {}).get("value"))
            if shot is not None:
                cap = _cdp_call(
                    sock, msg_id, "Page.captureScreenshot", {"format": "png", "captureBeyondViewport": True}
                )
                shot.write_bytes(base64.b64decode(cap["data"]))
            return metrics
        finally:
            sock.close()
    finally:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=8)
            except subprocess.TimeoutExpired:
                proc.kill()
        if own_ud:
            import shutil

            shutil.rmtree(user_data, ignore_errors=True)