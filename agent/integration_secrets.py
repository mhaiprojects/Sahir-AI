"""Load integration passwords from env or mode-600 files (never log values)."""

from __future__ import annotations

import os
from pathlib import Path


def load_secret(
    *,
    env_var: str,
    file_path: Path | None,
    label: str,
) -> tuple[str, str, str | None]:
    """Return (secret, source, error). source is env|file|none."""
    env = os.getenv(env_var, "").strip()
    if env:
        return env, "env", None
    if not file_path or not file_path.is_file():
        return "", "none", None
    try:
        mode = file_path.stat().st_mode
        if mode & 0o077:
            return (
                "",
                "file",
                f"{label} file {file_path} must be mode 600 (not group/world readable)",
            )
        secret = file_path.read_text(encoding="utf-8").strip()
        if not secret:
            return "", "file", f"{label} file {file_path} is empty"
        return secret, "file", None
    except OSError as exc:
        return "", "file", f"Could not read {label} file {file_path}: {exc}"


def redact(text: str, secret: str) -> str:
    if not secret or len(secret) < 4:
        return text
    return text.replace(secret, "[REDACTED]")