"""Auto default model selection: VRAM budget, benchmark scores, fallback exclusion."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent.gpu_config import reset_gpu_config
from agent.model_selection import choose_auto_default, load_bench_scores

GB = 1_000_000_000


def _m(name: str, size: int, param: str = "") -> dict:
    d = {"name": name, "size": size, "details": {}}
    if param:
        d["details"]["parameter_size"] = param
    return d


@pytest.fixture(autouse=True)
def _reset_gpu() -> None:
    reset_gpu_config()
    yield
    reset_gpu_config()


def test_never_auto_selects_fallback() -> None:
    installed = [
        _m("llama3.1:8b", int(4.9 * GB), "8B"),
        _m("qwen3:0.6b", 522_000_000, "0.6B"),
    ]
    name, meta = choose_auto_default(installed, fallback_model="llama3.1:8b", router_model="qwen3:0.6b")
    assert name == "qwen3:0.6b"
    assert meta["fits_vram"]
    assert meta["reason"] in {"vram_fit", "bench_ranked"}


def test_prefers_vram_fitting_over_oversized() -> None:
    installed = [
        _m("llama3.1:8b", int(4.9 * GB), "8B"),
        _m("qwen3.5:2b", int(2.4 * GB), "2B"),
        _m("qwen3:0.6b", 522_000_000, "0.6B"),
    ]
    name, meta = choose_auto_default(installed, fallback_model="llama3.1:8b", router_model="qwen3:0.6b")
    assert name != "llama3.1:8b"
    assert meta["fits_vram"]


def test_bench_score_beats_size(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    bench = {
        "results": [
            {"model": "qwen3:0.6b", "effective_correct_pct": 70},
            {"model": "llama3.2:1b", "effective_correct_pct": 92},
        ]
    }
    path = tmp_path / "latest.json"
    path.write_text(json.dumps(bench))
    monkeypatch.setenv("MODEL_BENCH_PATH", str(path))
    installed = [
        _m("qwen3:0.6b", 522_000_000, "0.6B"),
        _m("llama3.2:1b", 1_300_000_000, "1B"),
        _m("llama3.1:8b", int(4.9 * GB), "8B"),
    ]
    name, meta = choose_auto_default(
        installed, fallback_model="llama3.1:8b", router_model="qwen3:0.6b", bench_path=path,
    )
    assert name == "llama3.2:1b"
    assert meta["bench_effective_correct_pct"] == 92
    assert meta["reason"] == "bench_ranked"
    assert meta["picked_better_than_router"]


def test_router_stays_when_it_wins(tmp_path: Path) -> None:
    bench = {
        "results": [
            {"model": "qwen3:0.6b", "effective_correct_pct": 95},
            {"model": "llama3.2:1b", "effective_correct_pct": 80},
        ]
    }
    path = tmp_path / "latest.json"
    path.write_text(json.dumps(bench))
    installed = [_m("qwen3:0.6b", 522_000_000), _m("llama3.2:1b", 1_300_000_000)]
    name, _ = choose_auto_default(
        installed, fallback_model="llama3.1:8b", router_model="qwen3:0.6b", bench_path=path,
    )
    assert name == "qwen3:0.6b"


def test_only_fallback_installed_returns_none() -> None:
    installed = [_m("llama3.1:8b", int(4.9 * GB), "8B")]
    name, meta = choose_auto_default(installed, fallback_model="llama3.1:8b", router_model="qwen3:0.6b")
    assert name is None
    assert meta["reason"] == "no_eligible_chat_models"


def test_load_bench_scores_missing(tmp_path: Path) -> None:
    assert load_bench_scores(tmp_path / "missing.json") == {}