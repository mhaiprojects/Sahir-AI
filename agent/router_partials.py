"""STT partial stabilisation for speculative router (§6.2 tactic 1)."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from agent.chat_router import RouteDecision


def _norm_prefix(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").lower()).strip()


def prefix_meaning_changed(before: str, after: str) -> bool:
    """True when the final transcript likely changed routing intent vs partial."""
    a, b = _norm_prefix(before), _norm_prefix(after)
    if not a:
        return bool(b)
    if b.startswith(a) or a.startswith(b):
        return False
    wa, wb = a.split(), b.split()
    overlap = 0
    for i, w in enumerate(wa):
        if i < len(wb) and wb[i] == w:
            overlap += 1
        else:
            break
    if overlap >= min(5, len(wa), len(wb)):
        return False
    return a[:24] != b[:24]


@dataclass
class PartialStabilizer:
    min_words: int = 5
    max_words: int = 8

    _last: str = ""
    _stable: str = ""
    _repeat: int = 0
    cached_decision: RouteDecision | None = None

    def observe(self, partial: str) -> str | None:
        p = (partial or "").strip()
        if not p:
            return None
        words = p.split()
        if len(words) < self.min_words:
            self._last = p
            return None
        clip = " ".join(words[: self.max_words])
        if clip == self._last:
            self._repeat += 1
        else:
            self._last = clip
            self._repeat = 1
        if self._repeat >= 2:
            self._stable = clip
            return self._stable
        return None

    def reset(self) -> None:
        self._last = ""
        self._stable = ""
        self._repeat = 0
        self.cached_decision = None


@dataclass
class VoiceRouteState:
    """Per WebSocket session routing state (undo + partial cache)."""

    stabilizer: PartialStabilizer = field(default_factory=PartialStabilizer)
    last_utterance: str = ""
    last_decision: RouteDecision | None = None
    last_conversation_id: int | None = None
    speculative_generation: int = 0
    speech_end_mono: float | None = None
    route_final_mono: float | None = None
    first_token_mono: float | None = None
    speculative_draft: Any | None = None
    speculative_partial: str = ""