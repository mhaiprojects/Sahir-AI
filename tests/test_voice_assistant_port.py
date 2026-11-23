"""voice-assistant.sh refuses to start when WEB_PORT is already bound."""

from __future__ import annotations

import socket
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "voice-assistant.sh"


@pytest.mark.skipif(not (ROOT / "venv" / "bin" / "python").is_file(), reason="venv required")
def test_start_exits_when_port_busy(tmp_path: Path) -> None:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("127.0.0.1", 0))
    sock.listen(1)
    port = sock.getsockname()[1]
    try:
        env = {
            "WEB_PORT": str(port),
            "WEB_HOST": "127.0.0.1",
            "PATH": "/usr/bin:/bin",
            "VOICE_ASSISTANT_FORCE_SCRIPT": "1",
            "VOICE_ASSISTANT_PID_FILE": str(tmp_path / "no.pid"),
        }
        proc = subprocess.run(
            ["bash", str(SCRIPT), "start"],
            cwd=str(ROOT),
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
        )
        combined = (proc.stdout + proc.stderr).lower()
        assert proc.returncode != 0
        assert "in use" in combined
        assert str(port) in combined
    finally:
        sock.close()