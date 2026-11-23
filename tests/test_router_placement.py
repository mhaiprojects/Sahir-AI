"""Router GPU placement checks (mocked Ollama /api/ps)."""

from __future__ import annotations

import threading
import time
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from agent import gpu_config as gpu_config_mod
from agent.gpu_config import GpuRuntimeConfig
from agent.models import ModelManager
from agent.router_model import (
    RouterModelService,
    router_classify_guard,
    start_router_service,
    stop_router_service,
)
from config import Settings
from web.models_api import BenchRunner, create_models_router

GB = 1_000_000_000
ROUTER = "qwen3:0.6b"
ROUTER_SIZE = int(0.52 * GB)


class PlacementHttp:
    def __init__(self, ps_rows: list[dict[str, Any]] | None = None) -> None:
        self.ps_rows = list(ps_rows or [])
        self.calls: list[tuple[str, str, dict[str, Any] | None]] = []

    def set_ps(self, rows: list[dict[str, Any]]) -> None:
        self.ps_rows = list(rows)

    def __call__(self, method: str, url: str, payload: dict[str, Any] | None, timeout: float) -> dict[str, Any]:
        self.calls.append((method, url, payload))
        if url.endswith("/api/ps"):
            return {"models": self.ps_rows}
        if url.endswith("/api/generate"):
            model = (payload or {}).get("model", "")
            if payload and payload.get("keep_alive") in (0, "0"):
                for row in self.ps_rows:
                    if row.get("name") == model:
                        row["size_vram"] = 0
            elif payload and payload.get("keep_alive") == -1:
                for row in self.ps_rows:
                    if row.get("name") == model:
                        row["size_vram"] = row.get("size", ROUTER_SIZE)
            return {"done": True, "load_duration": 5_000_000}
        return {}


@pytest.fixture()
def router_cfg(monkeypatch: pytest.MonkeyPatch) -> GpuRuntimeConfig:
    monkeypatch.setenv("ROUTER_PIN_ENABLED", "true")
    monkeypatch.setenv("ROUTER_MODEL", ROUTER)
    monkeypatch.setenv("ROUTER_MIN_GPU_FRACTION", "0.9")
    monkeypatch.setenv("ROUTER_PLACEMENT_RELOAD_COOLDOWN_SEC", "300")
    monkeypatch.setenv("ROUTER_CLASSIFY_GRACE_SEC", "3")
    monkeypatch.setenv("VRAM_TOTAL_GB", "4")
    monkeypatch.setenv("VRAM_DESKTOP_GB", "1.1")
    gpu_config_mod.reset_gpu_config()
    return GpuRuntimeConfig.from_env()


def svc(http: PlacementHttp, cfg: GpuRuntimeConfig) -> RouterModelService:
    return RouterModelService(Settings(ollama_host="http://127.0.0.1:11434"), cfg, http=http)


@pytest.fixture(autouse=True)
def _stop_router() -> None:
    import agent.router_model as rm

    stop_router_service()
    rm._last_classify_at = 0.0
    rm._classify_inflight = 0
    yield
    stop_router_service()


def test_no_reload_when_already_on_gpu(router_cfg: GpuRuntimeConfig) -> None:
    http = PlacementHttp([{"name": ROUTER, "size": ROUTER_SIZE, "size_vram": ROUTER_SIZE}])
    s = svc(http, router_cfg)
    before = len(http.calls)
    s.placement_check()
    assert len(http.calls) == before + 1  # ps only
    assert s.placement_status()["state"] == "gpu_ok"


def test_wait_when_vram_blocked(router_cfg: GpuRuntimeConfig) -> None:
    big = int(3.2 * GB)
    http = PlacementHttp([
        {"name": ROUTER, "size": ROUTER_SIZE, "size_vram": int(ROUTER_SIZE * 0.46)},
        {"name": "llama3.1:8b", "size": big, "size_vram": big},
    ])
    s = svc(http, router_cfg)
    s.placement_check()
    unload = [c for c in http.calls if (c[2] or {}).get("keep_alive") in (0, "0")]
    assert not unload
    assert s.placement_status()["state"] == "cpu_partial"


def test_reload_when_room(router_cfg: GpuRuntimeConfig) -> None:
    http = PlacementHttp([{"name": ROUTER, "size": ROUTER_SIZE, "size_vram": int(ROUTER_SIZE * 0.5)}])
    s = svc(http, router_cfg)
    s.placement_check()
    models_unloaded = [c[2]["model"] for c in http.calls if (c[2] or {}).get("keep_alive") in (0, "0")]
    assert models_unloaded == [ROUTER]
    pin = [c for c in http.calls if (c[2] or {}).get("keep_alive") == -1]
    assert pin
    assert s._last_gpu_fraction is not None and s._last_gpu_fraction >= 0.99


def test_backoff_skips_rapid_reload(router_cfg: GpuRuntimeConfig) -> None:
    http = PlacementHttp([{"name": ROUTER, "size": ROUTER_SIZE, "size_vram": int(ROUTER_SIZE * 0.4)}])
    s = svc(http, router_cfg)
    s._last_reload_mono = time.monotonic()
    before = len(http.calls)
    s.placement_check()
    unload = [c for c in http.calls if (c[2] or {}).get("keep_alive") in (0, "0")]
    assert not unload
    assert len(http.calls) == before + 1


def test_skip_reload_while_classify_in_flight(router_cfg: GpuRuntimeConfig) -> None:
    http = PlacementHttp([{"name": ROUTER, "size": ROUTER_SIZE, "size_vram": int(ROUTER_SIZE * 0.4)}])
    s = svc(http, router_cfg)
    with router_classify_guard():
        time.sleep(0.05)
        s.placement_check()
    unload = [c for c in http.calls if (c[2] or {}).get("keep_alive") in (0, "0")]
    assert not unload


def test_other_models_not_unloaded(router_cfg: GpuRuntimeConfig) -> None:
    http = PlacementHttp([
        {"name": ROUTER, "size": ROUTER_SIZE, "size_vram": int(ROUTER_SIZE * 0.4)},
        {"name": "llama3.2:1b", "size": int(1.3 * GB), "size_vram": int(0.6 * GB)},
    ])
    s = svc(http, router_cfg)
    s.placement_check()
    unloads = [c[2]["model"] for c in http.calls if (c[2] or {}).get("keep_alive") in (0, "0")]
    assert ROUTER in unloads
    assert "llama3.2:1b" not in unloads


def test_router_reload_api(tmp_path, router_cfg: GpuRuntimeConfig) -> None:
    http = PlacementHttp([{"name": ROUTER, "size": ROUTER_SIZE, "size_vram": int(ROUTER_SIZE * 0.4)}])
    settings = Settings(ollama_host="http://127.0.0.1:11434")
    stop_router_service()
    svc_inst = RouterModelService(settings, router_cfg, http=http)
    from agent import router_model as rm

    rm._service = svc_inst
    mgr = ModelManager(settings, state_path=tmp_path / "ms.json", http=http)
    app = FastAPI()
    app.include_router(create_models_router(lambda: mgr, bench=BenchRunner(bench_dir=tmp_path / "b")))
    client = TestClient(app)
    r = client.post("/api/models/router/reload")
    assert r.status_code == 200
    assert r.json().get("reloaded") is True
    unloads = [c[2]["model"] for c in http.calls if (c[2] or {}).get("keep_alive") in (0, "0")]
    assert ROUTER in unloads