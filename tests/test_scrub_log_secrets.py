"""scrub_log_secrets.py in-place redaction."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_scrub_log_secrets_reports_zero_after(tmp_path: Path) -> None:
    log = tmp_path / "startup.log"
    log.write_text('line1\nGET /login?token=abcdef0123456789 HTTP/1.1\n', encoding="utf-8")
    proc = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "scrub_log_secrets.py"), str(tmp_path)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert proc.returncode == 0, proc.stderr
    assert "abcdef" not in log.read_text(encoding="utf-8")
    assert "token=***" in log.read_text(encoding="utf-8")
    assert "unredacted_after=0" in proc.stdout