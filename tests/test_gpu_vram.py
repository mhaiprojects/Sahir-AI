"""GPU/VRAM config, budget warnings, router pin scheduler (mocked Ollama HTTP)."""

from __future__ import annotations

import json
import threading
import time
from typing import Any

import pytest

from agent import gpu_config as gpu_config_mod
from agent.gpu_config import GpuRuntimeConfig
from agent.ollama_runtime import chat_options, embed_options, ollama_chat_kwargs
from agent.router_model import RouterModelService, start_router_service, stop_router_service
from agent.vram_budget import budget_summary, chat_vram_budget_bytes, vram_warning_for_model
from config import Settings

GB = 1_000_000_000


class FakeOllamaHttp:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict[str, Any] | None]] = []

    def __call__(self, method: str, url: str, payload: dict[str, Any] | None, timeout: float) -> dict[str, Any]:
        self.calls.append((method, url, payload))
        return {"done": True, "load_duration": 0}


def test_gpu_config_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EMBED_ON_CPU", "false")
    monkeypatch.setenv("OLLAMA_NUM_CTX", "2048")
    monkeypatch.setenv("ROUTER_MODEL", "tiny:1b")
    monkeypatch.setenv("VRAM_TOTAL_GB", "8")
    monkeypatch.setenv("VRAM_DESKTOP_GB", "0")
    gpu_config_mod.reset_gpu_config()
    cfg = GpuRuntimeConfig.from_env()
    assert not cfg.embed_on_cpu
    assert cfg.ollama_num_ctx == 2048
    assert cfg.router_model == "tiny:1b"
    assert cfg.vram_total_gb == 8.0
    gpu_config_mod.reset_gpu_config()


def test_vram_budget_math() -> None:
    cfg = GpuRuntimeConfig(
        vram_total_gb=4.0, vram_router_gb=0.7, vram_embed_gb=0.3, vram_desktop_gb=1.1,
    )
    assert chat_vram_budget_bytes(cfg) == int(1.9 * GB)
    assert vram_warning_for_model(int(2.0 * GB), cfg) is not None
    assert vram_warning_for_model(int(1.0 * GB), cfg) is None
    summary = budget_summary(cfg)
    assert summary["chat_budget_gb"] == 1.9


def test_embed_and_chat_options(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EMBED_ON_CPU", "true")
    monkeypatch.setenv("OLLAMA_NUM_CTX", "1024")
    gpu_config_mod.reset_gpu_config()
    assert embed_options() == {"num_gpu": 0}
    assert chat_options() == {"num_ctx": 1024}
    kw = ollama_chat_kwargs("qwen3:0.6b")
    assert kw["think"] is False
    assert kw["options"]["num_ctx"] == 1024
    gpu_config_mod.reset_gpu_config()


@pytest.fixture()
def fake_http() -> FakeOllamaHttp:
    return FakeOllamaHttp()


@pytest.fixture()
def router_cfg(monkeypatch: pytest.MonkeyPatch) -> GpuRuntimeConfig:
    monkeypatch.setenv("ROUTER_PIN_ENABLED", "true")
    monkeypatch.setenv("ROUTER_KEEP_WARM_SEC", "0.05")
    monkeypatch.setenv("ROUTER_MODEL", "qwen3:0.6b")
    gpu_config_mod.reset_gpu_config()
    return GpuRuntimeConfig.from_env()


def test_router_pin_payload(fake_http: FakeOllamaHttp, router_cfg: GpuRuntimeConfig) -> None:
    settings = Settings(ollama_host="http://127.0.0.1:11434")
    svc = RouterModelService(settings, router_cfg, http=fake_http)
    svc.pin()
    assert len(fake_http.calls) == 1
    payload = fake_http.calls[0][2]
    assert payload["model"] == "qwen3:0.6b"
    assert payload["keep_alive"] == -1
    assert payload["options"]["num_ctx"] == 1024


def test_router_keep_warm_scheduler(fake_http: FakeOllamaHttp, router_cfg: GpuRuntimeConfig) -> None:
    settings = Settings(ollama_host="http://127.0.0.1:11434")
    svc = RouterModelService(settings, router_cfg, http=fake_http)
    before = len(fake_http.calls)
    svc.start()
    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline and len(fake_http.calls) < before + 2:
        time.sleep(0.02)
    svc.stop()
    assert len(fake_http.calls) >= before + 1  # pin + at least one warm ping


def test_start_router_service_singleton(fake_http: FakeOllamaHttp, router_cfg: GpuRuntimeConfig) -> None:
    stop_router_service()
    settings = Settings(ollama_host="http://127.0.0.1:11434")
    svc = start_router_service(settings, http=fake_http)
    assert svc is not None
    time.sleep(0.25)
    stop_router_service()
    assert any((c[2] or {}).get("keep_alive") == -1 for c in fake_http.calls)