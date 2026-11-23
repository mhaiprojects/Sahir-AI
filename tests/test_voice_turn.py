"""Unit tests for mid-turn voice classification and fold limits."""

from __future__ import annotations

import pytest

from agent.voice_turn import (
    MidTurnKind,
    can_fold_more,
    classify_mid_turn,
    fold_turn_text,
    is_stop_utterance,
    is_text_composer_continuation,
)


@pytest.mark.parametrize(
    "text,kind",
    [
        ("stop", MidTurnKind.STOP),
        ("cancel", MidTurnKind.STOP),
        ("never mind", MidTurnKind.STOP),
        ("shut up", MidTurnKind.STOP),
        ("also mention the garden", MidTurnKind.CONTEXT_ADD),
        ("and use metric units", MidTurnKind.CONTEXT_ADD),
        ("actually make it shorter", MidTurnKind.CONTEXT_ADD),
        ("switch to supervisor", MidTurnKind.NEW_COMMAND),
        ("do action 2", MidTurnKind.NEW_COMMAND),
    ],
)
def test_classify_mid_turn(text, kind):
    assert classify_mid_turn(text) == kind


@pytest.mark.parametrize("text", ["stop talking", "quiet please"])
def test_stop_phrases(text):
    assert is_stop_utterance(text) or classify_mid_turn(text) == MidTurnKind.STOP


@pytest.mark.parametrize(
    "text,expected",
    [
        ("also add this", True),
        ("And one more thing", True),
        ("+ fix the typo", True),
        ("hello there", False),
    ],
)
def test_text_composer_continuation(text, expected):
    assert is_text_composer_continuation(text) is expected


def test_fold_text_format():
    assert "(added:" in fold_turn_text("What is Pi?", "to 10 digits")


def test_fold_caps():
    assert can_fold_more(fold_count=3, char_count=10, addition="x") is False
    assert can_fold_more(fold_count=0, char_count=990, addition="x" * 20) is False
    assert can_fold_more(fold_count=1, char_count=100, addition="also") is True