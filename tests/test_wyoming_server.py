"""Wyoming protocol server: bind rules, describe, handlers, handle guardrails."""

from __future__ import annotations

import asyncio
import os
from types import SimpleNamespace
from unittest.mock import AsyncMock

import numpy as np
import pytest

from wyoming.asr import Transcribe, Transcript
from wyoming.audio import AudioChunk, AudioStart, AudioStop
from wyoming.handle import Handled, NotHandled
from wyoming.info import Describe
from wyoming.tts import Synthesize

from config import Settings
from wyoming_server.config import WyomingSettings, host_requires_lan_consent, validate_bind
from wyoming_server.handler import SahirWyomingHandler
from wyoming_server.services import WyomingServices
from agent.loop import AgentReply


def test_bind_safety_loopback_ok() -> None:
    wy = WyomingSettings(uri="tcp://127.0.0.1:10300", allow_lan=False)
    host, port = validate_bind(wy)
    assert host == "127.0.0.1" and port == 10300


def test_bind_safety_lan_refused() -> None:
    wy = WyomingSettings(uri="tcp://0.0.0.0:10300", allow_lan=False)
    with pytest.raises(SystemExit):
        validate_bind(wy)


def test_bind_safety_lan_allowed() -> None:
    wy = WyomingSettings(uri="tcp://0.0.0.0:10300", allow_lan=True)
    host, port = validate_bind(wy)
    assert host == "0.0.0.0"


def test_host_requires_lan_consent() -> None:
    assert host_requires_lan_consent("127.0.0.1") is False
    assert host_requires_lan_consent("0.0.0.0") is True


def test_describe_payload() -> None:
    wy = WyomingSettings(handle_enabled=True)
    svc = WyomingServices(app_settings=Settings(), wyoming_settings=wy)
    info = svc.build_info()
    assert len(info.asr) == 1 and info.asr[0].name == "sahir-asr"
    assert info.asr[0].models[0].languages == ["en"]
    assert len(info.tts) == 1 and info.tts[0].voices
    assert len(info.handle) == 1


def test_describe_handle_off_by_default() -> None:
    svc = WyomingServices(app_settings=Settings(), wyoming_settings=WyomingSettings())
    assert svc.build_info().handle == []


def test_handle_disabled_returns_not_handled() -> None:
    async def _run() -> None:
        writer = AsyncMock()
        svc = WyomingServices(app_settings=Settings(), wyoming_settings=WyomingSettings(handle_enabled=False))
        h = SahirWyomingHandler(svc, writer)

        async def write(ev):
            await writer(ev)

        h._write = write
        await h.handle_event(Transcript(text="hello", language="en").event())
        ev = writer.await_args_list[-1].args[0]
        assert NotHandled.is_type(ev.type)

    asyncio.run(_run())


def test_transcribe_stub_and_tts_stub() -> None:
    async def _run() -> None:
        writer = AsyncMock()
        svc = WyomingServices(
            app_settings=Settings(),
            wyoming_settings=WyomingSettings(max_audio_seconds=30),
            transcribe_fn=lambda _a: "hello world",
            synthesize_fn=lambda t: (np.zeros(1600, dtype=np.float32), 16000),
        )
        h = SahirWyomingHandler(svc, writer)
        h._write = writer

        await h.handle_event(Transcribe().event())
        await h.handle_event(AudioStart(rate=16000, width=2, channels=1).event())
        pcm = np.zeros(800, dtype=np.int16).tobytes()
        await h.handle_event(AudioChunk(rate=16000, width=2, channels=1, audio=pcm).event())
        await h.handle_event(AudioStop().event())
        last = writer.await_args_list[-1].args[0]
        assert Transcript.is_type(last.type)
        assert Transcript.from_event(last).text == "hello world"

        await h.handle_event(Synthesize(text="hi").event())
        assert any(AudioStop.is_type(c.args[0].type) for c in writer.await_args_list)

    asyncio.run(_run())


def test_audio_length_cap() -> None:
    async def _run() -> None:
        writer = AsyncMock()
        svc = WyomingServices(
            app_settings=Settings(),
            wyoming_settings=WyomingSettings(max_audio_seconds=0.05),
            transcribe_fn=lambda _a: "x",
        )
        h = SahirWyomingHandler(svc, writer)
        h._write = writer
        await h.handle_event(Transcribe().event())
        blob = np.zeros(8000, dtype=np.int16).tobytes()
        await h.handle_event(AudioChunk(rate=16000, width=2, channels=1, audio=blob).event())
        from wyoming.error import Error

        err_events = [c.args[0] for c in writer.await_args_list if Error.is_type(c.args[0].type)]
        assert err_events

    asyncio.run(_run())


def test_side_effect_refusal() -> None:
    from agent.numbered_replies import action_requires_confirm
    from wyoming_server.agent_handle import WYOMING_CONFIRM_SPOKEN, wyoming_side_effect_refusal

    reply = AgentReply(
        reply="ok",
        action="confirm_pending",
        action_details={},
        routed=True,
        conversation_id=1,
        user_message_id=1,
        assistant_message_id=2,
        sources=[],
        numbered_items=[],
        tts_text="ok",
        show_numbered_list=False,
    )
    assert wyoming_side_effect_refusal(reply, SimpleNamespace()) == WYOMING_CONFIRM_SPOKEN
    send = {"kind": "email", "action": "send_draft"}
    assert action_requires_confirm(send)
    reply2 = AgentReply(
        reply="send it",
        action="numbered_action",
        action_details=send,
        routed=True,
        conversation_id=1,
        user_message_id=1,
        assistant_message_id=2,
        sources=[],
        numbered_items=[],
        tts_text="",
        show_numbered_list=False,
    )
    assert wyoming_side_effect_refusal(reply2, SimpleNamespace()) == WYOMING_CONFIRM_SPOKEN


def test_handle_enabled_stub() -> None:
    async def _run() -> None:
        writer = AsyncMock()
        svc = WyomingServices(
            app_settings=Settings(),
            wyoming_settings=WyomingSettings(handle_enabled=True),
            handle_fn=lambda t: (True, f"echo {t}"),
        )
        h = SahirWyomingHandler(svc, writer)
        h._write = writer
        await h.handle_event(Transcript(text="what time is it", language="en").event())
        ev = writer.await_args_list[-1].args[0]
        assert Handled.is_type(ev.type)
        assert "echo" in Handled.from_event(ev).text

    asyncio.run(_run())