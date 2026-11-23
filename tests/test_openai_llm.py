"""OpenAI-compatible LLM backend: HTTP mock, config, router/orchestrator wiring."""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
import pytest

from agent.llm_factory import build_chat_llm, chat_completion, resolve_llm_backend
from agent.openai_compat import (
    OpenAIAuthError,
    OpenAIChat,
    load_openai_api_key,
    normalize_openai_base_url,
    warn_openai_base_url,
)
from config import Settings


class _Handler(BaseHTTPRequestHandler):
    server_version = "MockOpenAI/1.0"

    def log_message(self, *_args: Any) -> None:
        return

    def _read_json(self) -> dict[str, Any]:
        n = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(n) if n else b"{}"
        return json.loads(raw.decode() or "{}")

    def do_GET(self) -> None:
        if self.path.rstrip("/").endswith("/models"):
            body = json.dumps({"data": [{"id": "mock-7b"}, {"id": "mock-small"}]}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        self.send_error(404)

    def do_POST(self) -> None:
        if not self.path.rstrip("/").endswith("/chat/completions"):
            self.send_error(404)
            return
        payload = self._read_json()
        if payload.get("stream"):
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            chunks = ["Hello", " world"]
            for piece in chunks:
                data = {
                    "choices": [{"delta": {"content": piece}, "index": 0}],
                }
                line = f"data: {json.dumps(data)}\n\n".encode()
                self.wfile.write(line)
                self.wfile.flush()
            self.wfile.write(b"data: [DONE]\n\n")
            return
        tools = payload.get("tools") or []
        msg: dict[str, Any] = {"role": "assistant", "content": "mock reply"}
        if tools:
            msg["tool_calls"] = [
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {"name": "answer", "arguments": json.dumps({"reply": "42"})},
                }
            ]
        body = json.dumps({"choices": [{"message": msg}]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


@pytest.fixture()
def mock_openai_server() -> str:
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    port = srv.server_address[1]
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{port}/v1"
    yield base
    srv.shutdown()


def test_normalize_base_url() -> None:
    assert normalize_openai_base_url("http://127.0.0.1:8080") == "http://127.0.0.1:8080/v1"
    with pytest.raises(ValueError):
        normalize_openai_base_url("ftp://x")


def test_warn_non_loopback() -> None:
    assert warn_openai_base_url("http://127.0.0.1:8080/v1") is None
    assert "not loopback" in (warn_openai_base_url("https://api.example.com/v1") or "")


def test_key_file_mode_600(tmp_path: Path) -> None:
    path = tmp_path / "key.txt"
    path.write_text("secret\n", encoding="utf-8")
    path.chmod(0o644)
    _token, _src, err = load_openai_api_key(file_path=str(path))
    assert err and "600" in err


def test_openai_chat_non_stream(mock_openai_server: str) -> None:
    chat = OpenAIChat(mock_openai_server, "mock-small", api_key="k")
    out = chat.chat([{"role": "user", "content": "hi"}])
    assert out["content"] == "mock reply"


def test_openai_chat_stream(mock_openai_server: str) -> None:
    chat = OpenAIChat(mock_openai_server, "mock-small", api_key="k")
    text = "".join(chat.chat_stream([{"role": "user", "content": "hi"}]))
    assert "Hello" in text and "world" in text


def test_openai_tools_shape(mock_openai_server: str) -> None:
    chat = OpenAIChat(mock_openai_server, "mock-small", api_key="k")
    tools = [{"type": "function", "function": {"name": "answer", "parameters": {"type": "object"}}}]
    resp = chat.chat_completion([{"role": "user", "content": "x"}], tools=tools)
    assert resp["message"]["tool_calls"][0]["function"]["name"] == "answer"


def test_connection_error_mapping() -> None:
    from agent.openai_compat import OpenAIConnectionError

    chat = OpenAIChat("http://127.0.0.1:1/v1", "x", api_key="bad", timeout_s=0.5)
    with pytest.raises(OpenAIConnectionError):
        chat.chat([{"role": "user", "content": "hi"}])


def test_http_401_mapping(mock_openai_server: str) -> None:
    class DenyHandler(_Handler):
        def do_POST(self) -> None:
            if self.path.rstrip("/").endswith("/chat/completions"):
                self.send_response(401)
                self.send_header("Content-Type", "application/json")
                body = b'{"error":"bad key"}'
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            super().do_POST()

    srv = ThreadingHTTPServer(("127.0.0.1", 0), DenyHandler)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        chat = OpenAIChat(f"http://127.0.0.1:{port}/v1", "m", api_key="bad")
        with pytest.raises(OpenAIAuthError):
            chat.chat([{"role": "user", "content": "hi"}])
    finally:
        srv.shutdown()


def test_build_chat_llm_backend_selection(monkeypatch: pytest.MonkeyPatch, mock_openai_server: str) -> None:
    monkeypatch.setenv("LLM_BACKEND", "openai")
    monkeypatch.setenv("OPENAI_BASE_URL", mock_openai_server)
    monkeypatch.setenv("OPENAI_MODEL", "mock-small")
    monkeypatch.setenv("OPENAI_API_KEY", "local")
    s = Settings.from_env()
    assert resolve_llm_backend(s) == "openai"
    llm = build_chat_llm(s)
    assert llm.chat([{"role": "user", "content": "ping"}])["content"] == "mock reply"


def test_chat_completion_orchestrator_shape(monkeypatch: pytest.MonkeyPatch, mock_openai_server: str) -> None:
    monkeypatch.setenv("LLM_BACKEND", "openai")
    monkeypatch.setenv("OPENAI_BASE_URL", mock_openai_server)
    monkeypatch.setenv("OPENAI_MODEL", "mock-small")
    s = Settings.from_env()
    resp = chat_completion(s, "mock-small", [{"role": "user", "content": "x"}])
    assert "message" in resp
    assert resp["message"]["content"]


def test_chat_text_only_uses_openai(monkeypatch: pytest.MonkeyPatch, mock_openai_server: str) -> None:
    from tools import router

    monkeypatch.setenv("LLM_BACKEND", "openai")
    monkeypatch.setenv("OPENAI_BASE_URL", mock_openai_server)
    monkeypatch.setenv("OPENAI_MODEL", "mock-small")
    s = Settings.from_env()
    s.openai_api_key = "local"
    r = router.chat_text_only_detailed("hello", s)
    assert r.reply == "mock reply"
    assert r.action == "direct_chat"


def test_router_t2_stays_ollama(monkeypatch: pytest.MonkeyPatch) -> None:
    """Tier 2 classify uses Ollama Client, not the OpenAI chat backend."""
    monkeypatch.setenv("LLM_BACKEND", "openai")
    hosts: list[str] = []

    class FakeClient:
        def __init__(self, host: str) -> None:
            hosts.append(host)

        def chat(self, **_kwargs: Any) -> dict[str, Any]:
            return {"message": {"content": "C"}}

    monkeypatch.setattr("ollama.Client", FakeClient)
    from agent.router_t2 import _default_chat

    _default_chat(model="qwen3:0.6b", messages=[{"role": "user", "content": "hi"}])
    assert hosts and "11434" in hosts[0]


def test_llm_backend_defaults_ollama(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LLM_BACKEND", raising=False)
    assert resolve_llm_backend(Settings()) == "ollama"