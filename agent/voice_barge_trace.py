"""Structured barge-in timeline (server logs + optional WS events)."""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


def trace_ws_enabled() -> bool:
    return os.getenv("SAHIR_VOICE_TRACE", "").lower() in ("1", "true", "yes")


@dataclass
class BargeTrace:
    t0: float = field(default_factory=time.monotonic)
    events: list[dict] = field(default_factory=list)

    def mark(self, name: str, **data: object) -> dict:
        rel_ms = int((time.monotonic() - self.t0) * 1000)
        entry = {"event": name, "ms": rel_ms, **data}
        self.events.append(entry)
        logger.info("voice_barge_trace %s ms=%s %s", name, rel_ms, data)
        return entry