"""SAHIR_VOICE_E2E chat wrapper (throwaway voice e2e only)."""

from __future__ import annotations

import time

from agent.llm import VoiceE2EChat, maybe_voice_e2e_chat
from tests.conftest import FakeLLM


def test_voice_e2e_chat_delay_only_passthrough():
    inner = FakeLLM(reply="Unmodified reply.")
    wrapped = VoiceE2EChat(inner, delay_s=0.05)
    t0 = time.monotonic()
    out = wrapped.chat([{"role": "user", "content": "hello"}])
    assert time.monotonic() - t0 >= 0.04
    assert out["content"] == "Unmodified reply."
    assert len(inner.calls) == 1


def test_maybe_voice_e2e_chat_noop_without_env(monkeypatch):
    monkeypatch.delenv("SAHIR_VOICE_E2E", raising=False)
    llm = FakeLLM()
    assert maybe_voice_e2e_chat(llm) is llm


def test_maybe_voice_e2e_chat_wraps_ollama(monkeypatch):
    monkeypatch.setenv("SAHIR_VOICE_E2E", "1")
    from agent.llm import OllamaChat

    llm = OllamaChat("http://127.0.0.1:11434", "test")
    wrapped = maybe_voice_e2e_chat(llm)
    assert isinstance(wrapped, VoiceE2EChat)