"""Live caption partials on /ws/voice (_partial_router_loop)."""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import numpy as np
import pytest

from agent.router_partials import PartialStabilizer


def test_partial_loop_emits_live_caption_before_stable(monkeypatch: pytest.MonkeyPatch) -> None:
    from web import voice_ws

    sent: list[dict] = []

    class FakeSession(voice_ws.VoiceSession):
        def __init__(self) -> None:
            self.route_state = type("RS", (), {"stabilizer": PartialStabilizer(min_words=5)})()
            self._in_speech = True
            self.settings = MagicMock()
            self.get_command_store = None
            self.endpointer = MagicMock()
            self.endpointer.speech_snapshot.return_value = np.zeros(8000, dtype=np.float32)

        async def send(self, payload: dict) -> None:
            sent.append(payload)

    pipeline = MagicMock()
    calls = iter(["alpha beta", "alpha beta gamma"])
    pipeline.transcribe_live.side_effect = lambda _a: next(calls, "")

    sess = FakeSession()

    sleep_calls = [0]

    async def _sleep_once(_s: float) -> None:
        sleep_calls[0] += 1
        if sleep_calls[0] >= 2:
            sess._in_speech = False

    monkeypatch.setattr(voice_ws.asyncio, "sleep", _sleep_once)
    monkeypatch.setattr("agent.dispatcher.plan_route", lambda *a, **k: (None, None))
    sess.get_pipeline = MagicMock(return_value=pipeline)

    async def _run() -> None:
        await sess._partial_router_loop()

    asyncio.run(_run())

    partials = [p for p in sent if p.get("type") == "transcript_partial"]
    assert len(partials) >= 1
    assert partials[0]["text"] == "alpha beta"