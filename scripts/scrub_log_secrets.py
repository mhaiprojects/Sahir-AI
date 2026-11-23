#!/usr/bin/env python3
"""Scrub sensitive query-string values from log files (in place). Reports counts only."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from log_redaction import count_unredacted_token_params, redact_query_secrets  # noqa: E402


def scrub_file(path: Path, *, dry_run: bool = False) -> tuple[int, int]:
    text = path.read_text(encoding="utf-8", errors="replace")
    before = count_unredacted_token_params(text)
    if before == 0:
        return 0, 0
    cleaned = redact_query_secrets(text)
    after = count_unredacted_token_params(cleaned)
    if not dry_run and cleaned != text:
        path.write_text(cleaned, encoding="utf-8")
        try:
            import os

            os.chmod(path, 0o600)
        except OSError:
            pass
    return before, after


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "log_dir",
        nargs="?",
        type=Path,
        default=ROOT / "data" / "logs",
        help="Directory containing *.log files (default: data/logs)",
    )
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    log_dir: Path = args.log_dir
    if not log_dir.is_dir():
        print(f"log_dir missing: {log_dir}")
        return 0
    total_before = 0
    total_after = 0
    files = 0
    for path in sorted(log_dir.glob("*.log*")):
        if not path.is_file():
            continue
        b, a = scrub_file(path, dry_run=args.dry_run)
        if b:
            files += 1
            total_before += b
            total_after += a
            print(f"{path.name}: redacted {b} value(s), remaining {a}")
    print(f"files_touched={files} unredacted_before={total_before} unredacted_after={total_after}")
    return 0 if total_after == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())