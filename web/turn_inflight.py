"""Registry of in-flight voice turns (for text composer fold-in while generating)."""

from __future__ import annotations

import weakref
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Callable

if TYPE_CHECKING:
    from web.voice_ws import VoiceSession

_sessions: dict[int, weakref.ref[Any]] = {}


@dataclass
class FoldRequest:
    text: str
    done: bool = False
    error: str | None = None


def register_inflight(conversation_id: int | None, session: "VoiceSession") -> None:
    if conversation_id is None:
        return
    _sessions[int(conversation_id)] = weakref.ref(session)


def unregister_inflight(conversation_id: int | None, session: "VoiceSession") -> None:
    if conversation_id is None:
        return
    ref = _sessions.get(int(conversation_id))
    if ref is not None and ref() is session:
        _sessions.pop(int(conversation_id), None)


def get_inflight_session(conversation_id: int | None) -> "VoiceSession | None":
    if conversation_id is None:
        return None
    ref = _sessions.get(int(conversation_id))
    if ref is None:
        return None
    sess = ref()
    if sess is None:
        _sessions.pop(int(conversation_id), None)
    return sess


def try_request_text_fold(conversation_id: int | None, text: str) -> bool:
    sess = get_inflight_session(conversation_id)
    if sess is None:
        return False
    return sess.request_text_fold(text)