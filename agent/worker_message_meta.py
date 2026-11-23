"""Merge worker output into assistant message metadata."""

from __future__ import annotations

from typing import Any


def worker_message_meta_from_tasks(tasks: list[dict[str, Any]] | None) -> dict[str, Any]:
    for ent in tasks or []:
        result = ent.get("result") if isinstance(ent, dict) else None
        if not isinstance(result, dict):
            continue
        out = result.get("output")
        if not isinstance(out, dict):
            continue
        meta = out.get("message_meta")
        if isinstance(meta, dict) and meta:
            return dict(meta)
    return {}