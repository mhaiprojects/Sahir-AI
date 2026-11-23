"""Regression guards for GitHub #32 (fixed in #40 UI load time, #41 CLI benchmark visibility)."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_models_js_keeps_load_status_after_refresh() -> None:
    js = (ROOT / "web" / "static" / "models.js").read_text(encoding="utf-8")
    assert "load_duration_ms" in js
    assert "after the refresh" in js or "#40" in js


def test_models_js_cli_benchmark_polling() -> None:
    js = (ROOT / "web" / "static" / "models.js").read_text(encoding="utf-8")
    assert "started outside this page" in js
    assert "benchErrors" in js
    assert "retrying" in js.lower()


def test_models_api_declares_external_benchmark_stale_window() -> None:
    src = (ROOT / "web" / "models_api.py").read_text(encoding="utf-8")
    assert "EXTERNAL_RUN_STALE_S" in src
    assert "external_progress" in src


def test_model_manager_maps_ollama_load_duration() -> None:
    src = (ROOT / "agent" / "models.py").read_text(encoding="utf-8")
    assert 'resp.get("load_duration")' in src