"""Lightweight Mermaid diagram validation (no graphviz / CLI)."""

from __future__ import annotations

import re
from dataclasses import dataclass

_MAX_CHARS = 24_000
_HEADERS = (
    "graph",
    "flowchart",
    "sequenceDiagram",
    "classDiagram",
    "stateDiagram",
    "erDiagram",
    "journey",
    "gantt",
    "pie",
    "mindmap",
    "timeline",
    "gitGraph",
    "C4Context",
    "C4Container",
)

_HEADER_RE = re.compile(
    r"(?m)^\s*(graph|flowchart|sequenceDiagram|classDiagram|stateDiagram|erDiagram|journey|"
    r"gantt|pie|mindmap|timeline|gitGraph|C4Context|C4Container)\b"
)
_LEGACY_TITLE_LINE = re.compile(r"(?m)^\s*title\s+(.+)$")
_FRONTMATTER_TITLE = re.compile(
    r"(?m)^---\s*\r?\n(?:[^\n]*\r?\n)*?title:\s*(.+?)\s*\r?\n---\s*\r?\n",
    re.I,
)


@dataclass
class MermaidValidation:
    ok: bool
    error: str = ""
    diagram_type: str = ""
    title: str = ""
    step_count: int = 0


def normalize_mermaid_source(source: str) -> str:
    """Move legacy ``title …`` body lines into YAML frontmatter (Mermaid 10+)."""
    src = (source or "").strip()
    if not src:
        return src
    bare_title: str | None = None
    kept: list[str] = []
    for line in src.splitlines():
        if _LEGACY_TITLE_LINE.match(line) and bare_title is None:
            bare_title = _LEGACY_TITLE_LINE.match(line).group(1).strip().strip('"')  # type: ignore[union-attr]
            continue
        kept.append(line)
    body = "\n".join(kept).strip()
    if bare_title:
        if _FRONTMATTER_TITLE.search(body):
            return body
        return f"---\ntitle: {bare_title}\n---\n{body}"
    return body


def _extract_title(src: str) -> str:
    fm = _FRONTMATTER_TITLE.search(src)
    if fm:
        return fm.group(1).strip().strip('"')
    legacy = _LEGACY_TITLE_LINE.search(src)
    if legacy:
        return legacy.group(1).strip().strip('"')
    return ""


def _bracket_balance(src: str) -> bool:
    stack: list[str] = []
    pairs = {"(": ")", "[": "]", "{": "}"}
    for ch in src:
        if ch in pairs:
            stack.append(pairs[ch])
        elif ch in pairs.values():
            if not stack or stack.pop() != ch:
                return False
    return not stack


def validate_mermaid(source: str) -> MermaidValidation:
    src = normalize_mermaid_source(source)
    if not src:
        return MermaidValidation(ok=False, error="empty diagram")
    if len(src) > _MAX_CHARS:
        return MermaidValidation(ok=False, error="diagram too large")
    m = _HEADER_RE.search(src)
    if not m:
        return MermaidValidation(ok=False, error="missing diagram header")
    if not _bracket_balance(src):
        return MermaidValidation(ok=False, error="unbalanced brackets")
    dtype = m.group(1)
    title = _extract_title(src)
    steps = len(re.findall(r"(?m)^\s*\w+.*(?:-->|---|->>|--)|participant\s+", src))
    if steps == 0:
        steps = max(1, len(re.findall(r"(?m)[\-\->]", src)) // 2)
    return MermaidValidation(ok=True, diagram_type=dtype, title=title, step_count=max(1, steps))