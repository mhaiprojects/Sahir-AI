"""Mermaid source normalization (legacy title lines → frontmatter)."""

from __future__ import annotations

from agent.mermaid_validate import normalize_mermaid_source, validate_mermaid

_LEGACY = """flowchart LR
    title E2E diagram
    A[User] --> B[Sahir]
"""


def test_normalize_legacy_title_to_frontmatter():
    out = normalize_mermaid_source(_LEGACY)
    assert out.startswith("---\ntitle: E2E diagram\n---\n")
    assert "title E2E" not in out
    assert "flowchart LR" in out


def test_validate_accepts_legacy_after_normalize():
    v = validate_mermaid(_LEGACY)
    assert v.ok
    assert v.title == "E2E diagram"