"""Application-wide logging configuration."""

from __future__ import annotations

import logging
import os
from logging.handlers import RotatingFileHandler
from pathlib import Path

from log_redaction import install_log_redaction

_LOG_DIR_MODE = 0o700
_LOG_FILE_MODE = 0o600


def _harden_log_path(path: Path) -> None:
    try:
        if path.is_dir():
            os.chmod(path, _LOG_DIR_MODE)
        elif path.is_file():
            os.chmod(path, _LOG_FILE_MODE)
    except OSError:
        pass


def configure_logging(log_dir: Path, *, level: int = logging.INFO) -> Path:
    """Configure console + rotating file logging for the app."""
    old_umask = os.umask(_LOG_DIR_MODE ^ 0o777)
    try:
        log_dir.mkdir(parents=True, exist_ok=True, mode=_LOG_DIR_MODE)
    finally:
        os.umask(old_umask)
    _harden_log_path(log_dir)
    log_path = log_dir / "voice-assistant.log"

    root = logging.getLogger()
    root.setLevel(level)

    formatter = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    )

    if not any(isinstance(h, RotatingFileHandler) for h in root.handlers):
        file_handler = RotatingFileHandler(
            log_path,
            maxBytes=2_000_000,
            backupCount=5,
            encoding="utf-8",
        )
        file_handler.setFormatter(formatter)
        root.addHandler(file_handler)

    if not any(isinstance(h, logging.StreamHandler) and not isinstance(h, RotatingFileHandler) for h in root.handlers):
        stream_handler = logging.StreamHandler()
        stream_handler.setFormatter(formatter)
        root.addHandler(stream_handler)

    install_log_redaction()
    _harden_log_path(log_path)
    for sibling in log_dir.glob("*.log*"):
        _harden_log_path(sibling)

    logging.getLogger("uvicorn.access").setLevel(logging.INFO)
    return log_path
