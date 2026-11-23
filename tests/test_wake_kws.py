"""KWS wake phrases, engine selection, and web wake gate."""

from __future__ import annotations

import time
from pathlib import Path
from unittest.mock import MagicMock

import numpy as np
import pytest

from config import Settings
from wake_gate import WakeGate, WakeGateState
from wake_kws import (
    KwsTokenizeError,
    KwsWakeSpotter,
    encode_wake_phrase,
    kws_model_dir,
    kws_model_ready,
    parse_wake_phrases,
    pcm_to_float32_mono,
    resolve_wakeword_engine,
    write_wake_keywords_file,
)


def test_parse_wake_phrases() -> None:
    assert parse_wake_phrases("hey sahir, okay sahir") == ["hey sahir", "okay sahir"]


def test_encode_wake_phrase_valid() -> None:
    if not kws_model_ready():
        pytest.skip("KWS model not on disk")
    line, label = encode_wake_phrase("hey sahir")
    assert "@" in line and label == "hey sahir"


def test_encode_wake_phrase_invalid() -> None:
    if not kws_model_ready():
        pytest.skip("KWS model not on disk")
    with pytest.raises(KwsTokenizeError):
        encode_wake_phrase("!!!")


def test_pcm_to_float32_int16_scaling() -> None:
    pcm = np.array([0, 16384, -32768], dtype=np.int16)
    f = pcm_to_float32_mono(pcm)
    assert f.dtype == np.float32
    np.testing.assert_allclose(f, [0.0, 0.5, -1.0], atol=1e-4)


def test_wake_keywords_file_custom_only() -> None:
    if not kws_model_ready():
        pytest.skip("KWS model not on disk")
    path = write_wake_keywords_file(["hey sahir"], model_dir=kws_model_dir())
    body = path.read_text(encoding="utf-8")
    assert "▁HE Y ▁SA H IR" in body
    assert "▁S I RI" not in body


def test_resolve_wakeword_engine_default() -> None:
    assert resolve_wakeword_engine(Settings()) == "openwakeword"


def test_resolve_wakeword_engine_kws(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WAKEWORD_ENGINE", "kws")
    assert resolve_wakeword_engine(Settings.from_env()) == "kws"


class StubSpotter:
    def __init__(self) -> None:
        self.calls = 0
        self._hits: list[bool] = []

    def queue_hit(self, hit: bool) -> None:
        self._hits.append(hit)

    def accept_waveform(self, pcm: np.ndarray) -> str | None:
        self.calls += 1
        if self._hits and self._hits.pop(0):
            return "hey sahir"
        return None


def test_wake_gate_state_machine() -> None:
    stub = StubSpotter()
    gate = WakeGate(enabled=True, utterance_timeout_s=0.05, spotter=stub, clock=time.monotonic)
    pcm = np.zeros(320, dtype=np.float32)
    stub.queue_hit(True)
    ev, phrase = gate.process_pcm(pcm)
    assert ev == "wake_detected" and phrase == "hey sahir"
    assert gate.state == WakeGateState.WAIT_UTTERANCE
    assert gate.vad_may_run()
    time.sleep(0.08)
    ev2, _ = gate.process_pcm(pcm)
    assert ev2 == "utterance_timeout"
    assert gate.state == WakeGateState.WAIT_WAKE


def test_wake_gate_off_skips_kws() -> None:
    stub = StubSpotter()
    stub.queue_hit(True)
    gate = WakeGate(enabled=False, spotter=stub)
    gate.process_pcm(np.zeros(100, dtype=np.float32))
    assert stub.calls == 0
    assert gate.vad_may_run()


def test_voice_session_wake_gate_disabled_no_kws(monkeypatch: pytest.MonkeyPatch) -> None:
    from web.voice_ws import VoiceSession

    built = MagicMock()
    monkeypatch.setattr("wake_kws.build_kws_spotter", built)
    ws = MagicMock()
    session = VoiceSession(
        ws,
        get_settings=Settings,
        get_pipeline=lambda: None,
        get_agent=lambda: None,
        get_command_store=None,
        request_lock=__import__("threading").Lock(),
    )
    session.wake_gate_enabled = False
    session._wake_gate.set_enabled(False)
    pcm = np.zeros(1600, dtype=np.float32)
    session._wake_gate.process_pcm(pcm)
    built.assert_not_called()


@pytest.mark.slow
def test_kws_on_piper_clip() -> None:
    if not kws_model_ready():
        pytest.skip("KWS model not on disk")
    from audio_pipeline import SpeechPipeline
    settings = Settings.from_env()
    pipeline = SpeechPipeline(settings)
    audio, sr = pipeline.synthesize_float("hey siri")
    spotter = KwsWakeSpotter(
        ["hey siri"],
        keywords_threshold=0.1,
        keywords_score=2.5,
        num_threads=1,
    )
    hit = spotter.detect_in_clip(audio, sample_rate=sr)
    assert hit is not None