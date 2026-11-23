"""Ollama model store path discovery and disk space warnings."""

from __future__ import annotations

import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

import web.ollama_storage as storage


def test_parse_systemd_environment_extracts_ollama_models() -> None:
    text = (
        "Environment=PATH=/usr/bin:/bin "
        "OLLAMA_MODELS=/mnt/ssd6/ollama/models\n"
    )
    env = storage.parse_systemd_environment(text)
    assert env["OLLAMA_MODELS"] == "/mnt/ssd6/ollama/models"
    assert "PATH" in env


def test_resolve_models_path_env_override(tmp_path: Path) -> None:
    path, source = storage.resolve_models_path(override_dir=tmp_path / "models")
    assert source == "env"
    assert path == (tmp_path / "models").resolve()


def test_resolve_models_path_from_systemd() -> None:
    def fake_show(*args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            args=(),
            returncode=0,
            stdout="Environment=OLLAMA_MODELS=/mnt/ssd6/ollama/models\n",
            stderr="",
        )

    with patch.object(storage, "_systemctl_show", side_effect=fake_show):
        with patch.object(storage, "ollama_unit_active", return_value=True):
            path, source = storage.resolve_models_path(override_dir=None)
    assert source == "systemd"
    assert path == Path("/mnt/ssd6/ollama/models")


def test_resolve_models_path_system_default_when_unit_active() -> None:
    def fake_show(*args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            args=(), returncode=0, stdout="Environment=PATH=/usr/bin\n", stderr=""
        )

    with patch.object(storage, "_systemctl_show", side_effect=fake_show):
        with patch.object(storage, "ollama_unit_active", return_value=True):
            path, source = storage.resolve_models_path(override_dir=None)
    assert source == "system_default"
    assert path == storage.SYSTEM_OLLAMA_MODELS


def test_inspect_model_storage_warnings_on_root_and_low_space(tmp_path: Path) -> None:
    models = tmp_path / "ollama" / "models"
    anchor = tmp_path

    with patch.object(storage, "resolve_models_path", return_value=(models, "systemd")):
        with patch.object(storage, "disk_usage_anchor", return_value=anchor):
            with patch.object(storage, "on_root_filesystem", return_value=True):
                with patch.object(storage.shutil, "disk_usage", return_value=SimpleNamespace(
                    total=100 * storage._GB, used=95 * storage._GB, free=3 * storage._GB
                )):
                    info = storage.inspect_model_storage(warn_free_gb=10.0)
    assert info["warn_on_root"] is True
    assert info["warn_low_space"] is True
    assert len(info["warnings"]) >= 2
    assert info["disk_free_bytes"] == 3 * storage._GB


def test_inspect_model_storage_healthy_off_root(tmp_path: Path) -> None:
    models = tmp_path / "models"
    with patch.object(
        storage, "resolve_models_path", return_value=(models, "systemd")
    ):
        with patch.object(storage, "disk_usage_anchor", return_value=tmp_path):
            with patch.object(storage, "on_root_filesystem", return_value=False):
                with patch.object(
                    storage.shutil,
                    "disk_usage",
                    return_value=SimpleNamespace(
                        total=500 * storage._GB,
                        used=100 * storage._GB,
                        free=400 * storage._GB,
                    ),
                ):
                    info = storage.inspect_model_storage(warn_free_gb=10.0)
    assert info["warnings"] == []
    assert not info["warn_low_space"]
    assert not info["warn_on_root"]