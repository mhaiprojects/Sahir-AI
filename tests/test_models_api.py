"""Model manager + /api/models routes with Ollama mocked."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from agent import models as models_mod
from agent.models import ModelManager, think_kwargs, tool_calls_invalid, wants_escalation
from agent.orchestrator import Orchestrator
from config import Settings
from web.models_api import BenchRunner, create_models_router

GB = 1_000_000_000


class FakeOllama:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, Any]] = []
        self.loaded: dict[str, int] = {"llama3.1:8b": 5 * GB}
        self.tags = [
            {"name": "qwen3:0.6b", "size": 522_000_000, "details": {"parameter_size": "751M", "quantization_level": "Q4_K_M"}},
            {"name": "llama3.2:1b", "size": 1_300_000_000, "details": {}},
            {"name": "llama3.1:8b", "size": 4_900_000_000, "details": {}},
            {"name": "nomic-embed-text:latest", "size": 274_000_000, "details": {}},
        ]

    def __call__(self, method: str, url: str, payload: dict[str, Any] | None, timeout: float) -> dict[str, Any]:
        path = url.split("11434", 1)[-1]
        self.calls.append((method, path, payload))
        if path == "/api/tags":
            return {"models": self.tags}
        if path == "/api/ps":
            return {"models": [{"name": n, "size": s, "size_vram": s // 2} for n, s in self.loaded.items()]}
        if path == "/api/generate":
            if payload["keep_alive"] == 0:
                self.loaded.pop(payload["model"], None)
            else:
                self.loaded[payload["model"]] = 1 * GB
            return {"done": True, "load_duration": 1_500_000_000}
        raise AssertionError(path)


@pytest.fixture()
def fake() -> FakeOllama:
    return FakeOllama()


@pytest.fixture()
def manager(tmp_path: Path, fake: FakeOllama, monkeypatch: pytest.MonkeyPatch) -> ModelManager:
    monkeypatch.delenv("FALLBACK_MODEL", raising=False)
    settings = Settings(ollama_host="http://localhost:11434", ollama_model="llama3.1:8b")
    mgr = ModelManager(settings, state_path=tmp_path / "model_state.json", http=fake)
    yield mgr
    models_mod.set_manager(None)


@pytest.fixture()
def client(manager: ModelManager, tmp_path: Path) -> TestClient:
    app = FastAPI()
    app.include_router(create_models_router(lambda: manager, bench=BenchRunner(bench_dir=tmp_path / "bench")))
    return TestClient(app)


def test_list_models_excludes_embeddings_and_reports_split(client: TestClient) -> None:
    data = client.get("/api/models").json()
    names = [m["name"] for m in data["models"]]
    assert "nomic-embed-text:latest" not in names
    assert names[0] == "qwen3:0.6b"  # sorted by size
    big = next(m for m in data["models"] if m["name"] == "llama3.1:8b")
    assert big["active"] and big["fallback"] and big["default"]
    assert big["loaded"]["gpu_percent"] == 50
    assert big["loaded"]["size_ram"] == big["loaded"]["size_vram"]
    assert data["active_model"] == "llama3.1:8b" and data["fallback_model"] == "llama3.1:8b"


def test_select_persists_and_updates_settings(client: TestClient, manager: ModelManager, tmp_path: Path,
                                              fake: FakeOllama) -> None:
    r = client.post("/api/models/select", json={"model": "qwen3:0.6b"})
    assert r.status_code == 200
    assert manager.settings.ollama_model == "qwen3:0.6b"
    state = json.loads((tmp_path / "model_state.json").read_text())
    assert state["active_model"] == "qwen3:0.6b"
    # A fresh manager restores the persisted choice; default stays the env model.
    settings = Settings(ollama_host="http://localhost:11434", ollama_model="llama3.1:8b")
    again = ModelManager(settings, state_path=tmp_path / "model_state.json", http=fake)
    assert settings.ollama_model == "qwen3:0.6b"
    assert again.default_model == "llama3.1:8b"


def test_select_unknown_model_404(client: TestClient) -> None:
    assert client.post("/api/models/select", json={"model": "nope:1b"}).status_code == 404
    assert client.post("/api/models/select", json={"model": "nomic-embed-text:latest"}).status_code == 404


def test_load_and_eject_use_keep_alive(client: TestClient, fake: FakeOllama) -> None:
    r = client.post("/api/models/load", json={"model": "llama3.2:1b", "keep_alive": "30m"}).json()
    assert r["loaded"] and r["load_duration_ms"] == 1500
    gen = [p for m, path, p in fake.calls if path == "/api/generate"]
    assert gen[-1]["keep_alive"] == "30m" and gen[-1]["prompt"] == ""
    r = client.post("/api/models/eject", json={"model": "llama3.2:1b"}).json()
    assert r == {"model": "llama3.2:1b", "loaded": False}
    gen = [p for m, path, p in fake.calls if path == "/api/generate"]
    assert gen[-1]["keep_alive"] == 0
    assert "llama3.2:1b" not in fake.loaded


def test_options_toggle(client: TestClient, manager: ModelManager) -> None:
    r = client.post("/api/models/options", json={"auto_fallback": False, "force_fallback": True}).json()
    assert r["auto_fallback"] is False and r["force_fallback"] is True
    assert manager.initial_model("hello") == ("llama3.1:8b", "forced")


def test_ollama_error_is_502(manager: ModelManager, tmp_path: Path) -> None:
    def boom(*a: Any) -> dict[str, Any]:
        raise ConnectionError("down")
    manager._http = boom
    app = FastAPI()
    app.include_router(create_models_router(lambda: manager, bench=BenchRunner(bench_dir=tmp_path / "b")))
    assert TestClient(app).get("/api/models").status_code == 502


def test_benchmark_status_reads_latest(client: TestClient, tmp_path: Path) -> None:
    (tmp_path / "bench").mkdir()
    (tmp_path / "bench" / "latest.json").write_text(json.dumps({"results": [{"model": "x"}]}))
    s = client.get("/api/models/benchmark").json()
    assert s["running"] is False and s["latest"]["results"][0]["model"] == "x"


def test_escalation_phrases_and_think() -> None:
    assert wants_escalation("Think harder about this")
    assert wants_escalation("please use the big model")
    assert not wants_escalation("what's the weather")
    assert think_kwargs("qwen3:1.7b") == {"think": False}
    assert think_kwargs("qwen3.5:2b") == {"think": False}
    assert think_kwargs("llama3.2:1b") == {}


TOOLS = [{"type": "function", "function": {"name": "delegate", "parameters": {
    "type": "object", "properties": {"type": {"type": "string", "enum": ["research", "code"]}},
    "required": ["type"]}}}]


@pytest.mark.parametrize("msg,expected", [
    ({"content": "Hi there."}, None),
    ({"content": ""}, "empty_response"),
    ({"content": '{"name": "delegate", "arguments": {}}'}, "tool_call_in_content"),
    ({"tool_calls": [{"function": {"name": "nope", "arguments": {}}}]}, "unknown_tool:nope"),
    ({"tool_calls": [{"function": {"name": "delegate", "arguments": "{bad"}}]}, "bad_json:delegate"),
    ({"tool_calls": [{"function": {"name": "delegate", "arguments": {}}}]}, "missing_arg:delegate.type"),
    ({"tool_calls": [{"function": {"name": "delegate", "arguments": {"type": "x"}}}]}, "bad_enum:delegate.type"),
    ({"tool_calls": [{"function": {"name": "delegate", "arguments": {"type": "code"}}}]}, None),
])
def test_tool_calls_invalid(msg: dict[str, Any], expected: str | None) -> None:
    assert tool_calls_invalid(msg, TOOLS) == expected


class _Dispatcher:
    workers: dict[str, Any] = {}

    def catalog(self) -> list[dict[str, str]]:
        return []


class _Client:
    def __init__(self, replies: dict[str, dict[str, Any]]) -> None:
        self.replies = replies
        self.models: list[str] = []

    def chat(self, model: str, messages: list[dict[str, Any]], **kw: Any) -> dict[str, Any]:
        self.models.append(model)
        return {"message": self.replies[model]}


def test_orchestrator_escalates_on_invalid_json(manager: ModelManager) -> None:
    manager.settings.ollama_model = "qwen3:0.6b"
    models_mod.set_manager(manager)
    client = _Client({
        "qwen3:0.6b": {"content": '{"name": "answer", "parameters": {"reply": "hi"}}'},
        "llama3.1:8b": {"tool_calls": [{"function": {"name": "answer", "arguments": {"reply": "Hello!"}}}]},
    })
    orch = Orchestrator(manager.settings, _Dispatcher(), client=client)
    res = orch.run("hello", history=[])
    assert res.reply == "Hello!"
    assert client.models == ["qwen3:0.6b", "llama3.1:8b"]
    assert res.action_details["model"] == "llama3.1:8b"
    assert res.action_details["small_model"] == "qwen3:0.6b"
    assert res.action_details["escalated"].startswith("invalid_output:")


def test_orchestrator_user_requested_big_model(manager: ModelManager) -> None:
    manager.settings.ollama_model = "qwen3:0.6b"
    models_mod.set_manager(manager)
    client = _Client({"llama3.1:8b": {"tool_calls": [{"function": {"name": "answer", "arguments": {"reply": "Deep."}}}]}})
    res = Orchestrator(manager.settings, _Dispatcher(), client=client).run("think harder: why?", history=[])
    assert client.models == ["llama3.1:8b"]
    assert res.action_details["escalated"] == "user_request"


def test_orchestrator_valid_small_model_no_escalation(manager: ModelManager) -> None:
    manager.settings.ollama_model = "qwen3:0.6b"
    models_mod.set_manager(manager)
    client = _Client({"qwen3:0.6b": {"tool_calls": [{"function": {"name": "answer", "arguments": {"reply": "Hi."}}}]}})
    res = Orchestrator(manager.settings, _Dispatcher(), client=client).run("hello", history=[])
    assert client.models == ["qwen3:0.6b"]
    assert res.action_details["model"] == "qwen3:0.6b" and "escalated" not in res.action_details
