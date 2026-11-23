"""Server-side wake gate for /ws/voice hands-free mode."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Protocol

import numpy as np


class SpotterFn(Protocol):
    def accept_waveform(self, pcm16_mono_16k: np.ndarray) -> str | None: ...


class WakeGateState(str, Enum):
    WAIT_WAKE = "wait_wake"
    WAIT_UTTERANCE = "wait_utterance"


@dataclass
class WakeGate:
    """Idle → wake detected → VAD utterance → timeout → idle."""

    enabled: bool = False
    utterance_timeout_s: float = 8.0
    spotter: SpotterFn | None = None
    clock: Callable[[], float] = time.monotonic
    state: WakeGateState = WakeGateState.WAIT_WAKE
    armed_at: float | None = None
    last_phrase: str = ""
    kws_calls: int = field(default=0, init=False)

    def set_enabled(self, on: bool) -> None:
        self.enabled = on
        self.reset()

    def reset(self) -> None:
        self.state = WakeGateState.WAIT_WAKE
        self.armed_at = None
        self.last_phrase = ""

    def listen_phrases(self) -> list[str]:
        if self.spotter and hasattr(self.spotter, "phrases"):
            return list(getattr(self.spotter, "phrases"))
        return []

    def on_turn_complete(self) -> None:
        if self.enabled:
            self.reset()

    def process_pcm(self, pcm: np.ndarray) -> tuple[str | None, str | None]:
        """Return (event, phrase): event is wake_detected | utterance_timeout | None."""
        if not self.enabled:
            return None, None
        if self.state == WakeGateState.WAIT_WAKE:
            if self.spotter is None:
                return None, None
            self.kws_calls += 1
            hit = self.spotter.accept_waveform(pcm)
            if hit:
                self.state = WakeGateState.WAIT_UTTERANCE
                self.armed_at = self.clock()
                self.last_phrase = hit
                return "wake_detected", hit
            return None, None
        now = self.clock()
        if self.armed_at is not None and (now - self.armed_at) > self.utterance_timeout_s:
            self.reset()
            return "utterance_timeout", None
        return None, None

    def vad_may_run(self) -> bool:
        if not self.enabled:
            return True
        return self.state == WakeGateState.WAIT_UTTERANCE