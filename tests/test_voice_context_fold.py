"""Voice session context-fold state (no network)."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from agent.voice_turn import classify_mid_turn, fold_turn_text
from web.voice_ws import ActiveTurn, VoiceSession


class FakePipeline:
    tts_backend = "fake"

    def transcribe_final(self, audio):
        return "also mention testing"


def test_fold_increments_and_emits_context_added():
    ws = AsyncMock()
    session = VoiceSession(
        ws,
        get_settings=MagicMock(),
        get_pipeline=lambda: FakePipeline(),
        get_agent=MagicMock(),
        get_command_store=None,
        request_lock=__import__("threading").Lock(),
    )
    session.send = AsyncMock()
    session.conversation_id = 1
    session.busy = True
    session._active_turn = ActiveTurn(combined_text="Long question about X", char_count=24)
    session._phase = "thinking"
    session.get_command_store = lambda: None
    store = MagicMock()
    store.last_user_message_id.return_value = 42
    session.get_agent = lambda: MagicMock(store=store)

    started = []

    def fake_start(**kwargs):
        started.append(kwargs)

    session.start_turn = fake_start  # type: ignore[method-assign]

    import numpy as np

    asyncio.run(session._handle_mid_turn_utterance(np.zeros(16000, dtype=np.float32)))

    from agent.voice_turn import MidTurnKind

    assert classify_mid_turn("also mention testing") == MidTurnKind.CONTEXT_ADD
    session.send.assert_any_await({"type": "context_added", "text": "also mention testing"})
    assert started and started[0]["text"] == fold_turn_text("Long question about X", "also mention testing")
    assert started[0]["replace_user_message_id"] == 42