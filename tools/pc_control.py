"""Whitelisted local PC control actions."""

from __future__ import annotations

import logging
import shutil
import subprocess
from dataclasses import dataclass

from config import Settings

logger = logging.getLogger(__name__)


@dataclass
class ActionResult:
    message: str
    success: bool = True


def open_app(app_name: str, settings: Settings) -> ActionResult:
    name = app_name.strip().lower()
    if name not in {a.lower() for a in settings.allowed_apps}:
        return ActionResult(
            message=f"Application {app_name!r} is not on the allow list.",
            success=False,
        )
    binary = shutil.which(name) or shutil.which(app_name)
    if not binary:
        return ActionResult(message=f"Could not find application {app_name!r}.", success=False)
    try:
        subprocess.Popen([binary], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError as exc:
        logger.exception("Failed to open app %s", app_name)
        return ActionResult(message=f"Failed to open {app_name}: {exc}", success=False)
    logger.info("Opened app: %s", name)
    return ActionResult(message=f"Opened {app_name}.")


def run_command(command: str, args: list[str], settings: Settings) -> ActionResult:
    if command not in settings.allowed_commands:
        return ActionResult(
            message=f"Command {command!r} is not allowed.",
            success=False,
        )
    binary = shutil.which(command)
    if not binary:
        return ActionResult(message=f"Command {command!r} was not found.", success=False)
    try:
        completed = subprocess.run(
            [binary, *args],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return ActionResult(message=f"Command {command} timed out.", success=False)
    output = (completed.stdout or completed.stderr or "").strip()
    if not output:
        output = f"{command} exited with code {completed.returncode}."
    logger.info("Ran command: %s %s", command, args)
    return ActionResult(message=output[:400], success=completed.returncode == 0)


def set_volume(percent: int) -> ActionResult:
    percent = max(0, min(100, percent))
    binary = shutil.which("pactl")
    if not binary:
        return ActionResult(message="pactl is not available for volume control.", success=False)
    try:
        subprocess.run(
            [binary, "set-sink-volume", "@DEFAULT_SINK@", f"{percent}%"],
            capture_output=True,
            text=True,
            timeout=5,
            check=True,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        return ActionResult(message=f"Could not set volume: {exc}", success=False)
    return ActionResult(message=f"Volume set to {percent} percent.")
