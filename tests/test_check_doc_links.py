"""Doc link checker smoke test."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_doc_links_strict() -> None:
    script = ROOT / "scripts" / "check_doc_links.py"
    proc = subprocess.run(
        [sys.executable, str(script), "--strict"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr