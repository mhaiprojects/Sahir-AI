"""Unit tests for voice PCM chunking and WAV helpers."""

from __future__ import annotations

import wave
from io import BytesIO

import numpy as np
import pytest

from scripts.voice_pcm_utils import (
    WORKLET_CHUNK_SAMPLES,
    pad_utterance_pcm,
    pcm16_stream_chunks,
    pcm16_to_wav,
    wav_bytes_to_pcm16_mono,
)


def test_pcm16_stream_chunks_sizes() -> None:
    pcm = np.arange(3000, dtype=np.int16).tobytes()
    chunks = list(pcm16_stream_chunks(pcm))
    assert len(chunks) == 3
    assert len(chunks[0]) == WORKLET_CHUNK_SAMPLES * 2
    assert len(chunks[1]) == WORKLET_CHUNK_SAMPLES * 2
    assert len(chunks[2]) == (3000 - 2 * WORKLET_CHUNK_SAMPLES) * 2


def test_pcm16_stream_chunks_rejects_odd_length() -> None:
    with pytest.raises(ValueError, match="multiple of 2"):
        list(pcm16_stream_chunks(b"\x00\x00\x00"))


def test_pcm16_to_wav_roundtrip_at_16k() -> None:
    pcm = np.array([0, 1000, -1000, 0], dtype=np.int16).tobytes()
    wav = pcm16_to_wav(pcm, sample_rate=16_000)
    out, sr = wav_bytes_to_pcm16_mono(wav)
    assert sr == 16_000
    assert len(out) == len(pcm)
    a = np.frombuffer(out, dtype=np.int16)
    b = np.frombuffer(pcm, dtype=np.int16)
    assert np.max(np.abs(a - b)) <= 1


def test_pad_utterance_pcm_length() -> None:
    speech = np.zeros(1600, dtype=np.int16).tobytes()  # 0.1 s
    padded = pad_utterance_pcm(speech, lead_silence_s=0.5, trail_silence_s=1.2)
    assert len(padded) == (8000 + 1600 + 19200) * 2


def test_wav_bytes_resamples_non_16k() -> None:
    sr = 22_050
    samples = np.zeros(sr // 10, dtype=np.int16)
    buf = BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes(samples.tobytes())
    pcm, out_sr = wav_bytes_to_pcm16_mono(buf.getvalue())
    assert out_sr == 16_000
    assert len(pcm) == 2 * int(sr // 10 * 16_000 / sr)