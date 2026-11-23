"""Live STT backend selection and interface."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import numpy as np
import pytest

from config import Settings
from stt_live import (
    MoonshineLiveBackend,
    WhisperLiveBackend,
    build_live_stt_backend,
    moonshine_model_ready,
    resolve_live_stt_backend_name,
)


def test_resolve_prefers_env(monkeypatch: pytest.MonkeyPatch) -> None:
    s = Settings()
    monkeypatch.setenv("WEB_LIVE_STT_BACKEND", "whisper")
    assert resolve_live_stt_backend_name(s) == "whisper"


def test_resolve_auto_moonshine_when_present(monkeypatch: pytest.MonkeyPatch) -> None:
    s = Settings()
    monkeypatch.delenv("WEB_LIVE_STT_BACKEND", raising=False)
    if not moonshine_model_ready():
        pytest.skip("Moonshine model not on disk")
    assert resolve_live_stt_backend_name(s) == "moonshine"


def test_build_fallback_when_moonshine_missing(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("WEB_LIVE_STT_BACKEND", "moonshine")
    monkeypatch.setattr("stt_live.moonshine_model_ready", lambda: False)
    pipeline = MagicMock()
    pipeline.transcribe.return_value = "ok"
    s = Settings()
    backend = build_live_stt_backend(s, pipeline=pipeline)
    assert isinstance(backend, WhisperLiveBackend)
    assert backend.transcribe_partial(np.zeros(100, dtype=np.float32)) == "ok"
    pipeline.transcribe.assert_called_once()


def test_whisper_live_backend_delegates() -> None:
    pipeline = MagicMock()
    pipeline.transcribe.return_value = "partial"
    b = WhisperLiveBackend(pipeline)
    out = b.transcribe_partial(np.ones(320, dtype=np.float32))
    assert out == "partial"
    pipeline.transcribe.assert_called_once()
    assert pipeline.transcribe.call_args.kwargs["tier"] == "live"


@pytest.mark.slow
@pytest.mark.flaky_tts_audio
@pytest.mark.xfail(
    strict=False,
    reason="#8: Moonshine on Piper TTS audio is intermittently below threshold",
)
def test_moonshine_on_piper_clip() -> None:
    if not moonshine_model_ready():
        pytest.skip("Moonshine model not on disk")
    from audio_pipeline import SpeechPipeline

    settings = Settings.from_env()
    pipeline = SpeechPipeline(settings)
    audio, sr = pipeline.synthesize_float("Hello world.")
    assert audio.size > 0
    if sr != 16000:
        dst_len = int(len(audio) * 16000 / sr)
        src_x = np.linspace(0, 1, num=len(audio), endpoint=False)
        dst_x = np.linspace(0, 1, num=dst_len, endpoint=False)
        audio = np.interp(dst_x, src_x, audio).astype(np.float32)
    backend = MoonshineLiveBackend(settings)
    text = backend.transcribe_partial(audio)
    assert text
    assert "hello" in text.lower() or "world" in text.lower()