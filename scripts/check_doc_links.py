#!/usr/bin/env python3
"""Check relative Markdown links under docs/ and README.md."""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LINK_RE = re.compile(r"\]\(([^)#]+)(?:#[^)]+)?\)")


def collect_markdown_files() -> list[Path]:
    files = [ROOT / "README.md", ROOT / "DOCKER.md", ROOT / "mkdocs.yml"]
    files.extend(ROOT.glob("docs/**/*.md"))
    return [p for p in files if p.is_file()]


def resolve_link(source: Path, target: str) -> Path | None:
    t = target.strip()
    if not t or t.startswith(("http://", "https://", "mailto:", "ssh://", "git@")):
        return None
    if t.startswith("/"):
        return ROOT / t.lstrip("/")
    return (source.parent / t).resolve()


def check_file(path: Path) -> list[str]:
    errors: list[str] = []
    text = path.read_text(encoding="utf-8", errors="replace")
    for m in LINK_RE.finditer(text):
        raw = m.group(1).strip()
        if raw.startswith("#"):
            continue
        resolved = resolve_link(path, raw)
        if resolved is None:
            continue
        try:
            resolved.relative_to(ROOT)
        except ValueError:
            continue
        if not resolved.is_file():
            errors.append(f"{path.relative_to(ROOT)}: missing link target {raw} -> {resolved.relative_to(ROOT)}")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--strict", action="store_true", help="Exit 1 on any error")
    args = parser.parse_args()
    all_errors: list[str] = []
    for md in collect_markdown_files():
        all_errors.extend(check_file(md))
    if all_errors:
        print("Broken relative links:")
        for line in sorted(set(all_errors)):
            print(f"  {line}")
        return 1 if args.strict else 0
    print(f"OK: checked {len(collect_markdown_files())} markdown sources")
    return 0


if __name__ == "__main__":
    sys.exit(main())