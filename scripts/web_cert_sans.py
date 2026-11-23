#!/usr/bin/env python3
"""CLI for web.ssl_cert_check (used by scripts/generate_web_cert.sh)."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from web.ssl_cert_check import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())