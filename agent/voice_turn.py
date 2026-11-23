"""Classify mid-turn voice/text utterances: stop vs context add vs new command."""

from __future__ import annotations

import re
from enum import Enum
from typing import TYPE_CHECKING, Any

from agent.chat_router import normalize_voice_control_stt, try_tier0_voice_control, typed_should_route_tier0
from agent.numbered_replies import parse_do_action_index

if TYPE_CHECKING:
    pass

MAX_TURN_FOLDS = 3
MAX_TURN_FOLD_CHARS = 1000

_CONTINUATION_RE = re.compile(
    r"(?is)^(?:also\b|and\b|oh and\b|actually\b|plus\b|wait[,:]?\s|i mean\b|make it\b)",
)
_TEXT_CONTINUATION_RE = re.compile(
    r"(?is)^(?:\+|also\b|and\b|actually\b)",
)


class MidTurnKind(str, Enum):
    STOP = "stop"
    CONTEXT_ADD = "context_add"
    NEW_COMMAND = "new_command"


_STOP_EXTRA = re.compile(r"(?is)^(?:shut up|be quiet|quiet)\b")

def is_stop_utterance(text: str) -> bool:
    t = normalize_voice_control_stt((text or "").strip())
    if not t:
        return False
    if _STOP_EXTRA.search(t):
        return True
    ctl = try_tier0_voice_control(t)
    if not ctl:
        return False
    return ctl.command in {"stop_speech", "cancel_request", "pause_listening"}


def is_continuation_marker(text: str) -> bool:
    t = (text or "").strip()
    if not t:
        return False
    return bool(_CONTINUATION_RE.search(t))


def is_text_composer_continuation(text: str) -> bool:
    """Text chat fold-in only when message starts with an explicit marker."""
    t = (text or "").strip()
    if not t:
        return False
    return bool(_TEXT_CONTINUATION_RE.match(t))


def looks_like_new_command(text: str, *, command_store: Any | None = None) -> bool:
    t = normalize_voice_control_stt((text or "").strip())
    if not t:
        return False
    if parse_do_action_index(t) is not None:
        return True
    if typed_should_route_tier0(t):
        return True
    if command_store is not None and command_store.match(t):
        return True
    ctl = try_tier0_voice_control(t)
    if ctl is not None and not is_stop_utterance(t):
        return True
    if re.match(r"(?is)^(?:please\s+)?switch\s+to\b", t):
        return True
    if re.match(r"(?is)^supervisor\b", t):
        return True
    return False


def classify_mid_turn(text: str, *, command_store: Any | None = None) -> MidTurnKind:
    t = (text or "").strip()
    if not t:
        return MidTurnKind.NEW_COMMAND
    if is_stop_utterance(t):
        return MidTurnKind.STOP
    if looks_like_new_command(t, command_store=command_store):
        return MidTurnKind.NEW_COMMAND
    if is_continuation_marker(t):
        return MidTurnKind.CONTEXT_ADD
    return MidTurnKind.CONTEXT_ADD


def fold_turn_text(base: str, addition: str) -> str:
    b = (base or "").strip()
    a = (addition or "").strip()
    if not a:
        return b
    if not b:
        return a
    return f"{b}\n\n(added: {a})"


def can_fold_more(*, fold_count: int, char_count: int, addition: str) -> bool:
    if fold_count >= MAX_TURN_FOLDS:
        return False
    extra = len((addition or "").strip())
    return (char_count + extra) <= MAX_TURN_FOLD_CHARS


def combined_char_count(base: str, addition: str) -> int:
    return len(fold_turn_text(base, addition))