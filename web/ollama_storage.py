"""Read-only discovery of where Ollama stores pulled models and free disk space."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any, Callable

SYSTEM_OLLAMA_MODELS = Path("/usr/share/ollama/.ollama/models")
USER_OLLAMA_MODELS = Path.home() / ".ollama" / "models"
_GB = 1_000_000_000

_SYSTEMCTL_SHOW: Callable[..., subprocess.CompletedProcess[str]] | None = None


def _systemctl_show(*args: str) -> subprocess.CompletedProcess[str]:
    fn = _SYSTEMCTL_SHOW or subprocess.run
    return fn(
        ["systemctl", "show", "ollama", *args, "--no-pager"],
        capture_output=True,
        text=True,
        timeout=5,
        check=False,
    )


def parse_systemd_environment(text: str) -> dict[str, str]:
    """Parse ``systemctl show -p Environment`` value into key/value pairs."""
    env: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("Environment="):
            continue
        blob = line[len("Environment=") :].strip().strip('"').strip("'")
        for token in blob.split():
            if "=" not in token:
                continue
            key, val = token.split("=", 1)
            env[key] = val.strip('"').strip("'")
    return env


def ollama_unit_active() -> bool:
    proc = _systemctl_show("-p", "LoadState")
    if proc.returncode != 0:
        return False
    for line in proc.stdout.splitlines():
        if line.startswith("LoadState="):
            return line.split("=", 1)[1].strip() not in ("", "not-found", "masked")
    return False


def resolve_models_path(*, override_dir: Path | None = None) -> tuple[Path, str]:
    """Return (models directory, resolution source tag)."""
    if override_dir is not None:
        return override_dir.expanduser().resolve(), "env"
    proc = _systemctl_show("-p", "Environment")
    if proc.returncode == 0 and proc.stdout.strip():
        env = parse_systemd_environment(proc.stdout)
        raw = env.get("OLLAMA_MODELS", "").strip()
        if raw:
            return Path(raw).expanduser().resolve(), "systemd"
    if ollama_unit_active():
        return SYSTEM_OLLAMA_MODELS.resolve(), "system_default"
    return USER_OLLAMA_MODELS.expanduser().resolve(), "user_default"


def disk_usage_anchor(path: Path) -> Path:
    """Nearest existing parent (or root) for ``shutil.disk_usage``."""
    p = path.expanduser()
    try:
        p = p.resolve()
    except OSError:
        p = path.expanduser()
    while not p.exists() and p != p.parent:
        p = p.parent
    return p


def on_root_filesystem(path: Path) -> bool:
    anchor = disk_usage_anchor(path)
    try:
        return os.stat(anchor).st_dev == os.stat("/").st_dev
    except OSError:
        return False


def inspect_model_storage(
    *,
    override_dir: Path | None = None,
    warn_free_gb: float = 10.0,
) -> dict[str, Any]:
    """Summary for Models UI / ``GET /api/models``."""
    path, source = resolve_models_path(override_dir=override_dir)
    anchor = disk_usage_anchor(path)
    threshold = max(0.0, float(warn_free_gb)) * _GB
    warnings: list[str] = []
    disk: dict[str, int | None] = {"total": None, "used": None, "free": None}
    try:
        usage = shutil.disk_usage(anchor)
        disk = {"total": usage.total, "used": usage.used, "free": usage.free}
    except OSError:
        warnings.append(f"Could not read disk space for {anchor}")

    on_root = on_root_filesystem(path)
    low_space = disk["free"] is not None and disk["free"] < threshold

    if on_root:
        warnings.append(
            "Ollama model storage is on the root filesystem. "
            "Consider moving OLLAMA_MODELS to a larger disk (see docs/models.md)."
        )
    if low_space and disk["free"] is not None:
        free_gb = disk["free"] / _GB
        warnings.append(
            f"Low free space ({free_gb:.1f} GB free, threshold {warn_free_gb:g} GB). "
            "Avoid large ollama pull until you free space or move the store."
        )

    return {
        "path": str(path),
        "path_exists": path.is_dir(),
        "resolved_from": source,
        "disk_anchor": str(anchor),
        "disk_total_bytes": disk["total"],
        "disk_free_bytes": disk["free"],
        "disk_used_bytes": disk["used"],
        "on_root_filesystem": on_root,
        "warn_low_space": low_space,
        "warn_on_root": on_root,
        "free_threshold_gb": warn_free_gb,
        "warnings": warnings,
    }