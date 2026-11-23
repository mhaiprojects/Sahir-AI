"""PCM helpers for voice E2E checks (browser AudioWorklet-compatible chunking)."""

from __future__ import annotations

import io
import wave
from typing import Iterator

import numpy as np

# Matches web/static/pcm-capture-worklet.js (1024 samples @ 16 kHz ≈ 64 ms).
WORKLET_CHUNK_SAMPLES = 1024
TARGET_SAMPLE_RATE = 16_000


def pcm16_stream_chunks(
    pcm: bytes,
    chunk_samples: int = WORKLET_CHUNK_SAMPLES,
) -> Iterator[bytes]:
    """Split int16 PCM into fixed-size chunks like the browser worklet sends."""
    if chunk_samples <= 0:
        raise ValueError("chunk_samples must be positive")
    width = 2
    chunk_bytes = chunk_samples * width
    if len(pcm) % width:
        raise ValueError("pcm length must be a multiple of 2 (int16)")
    for i in range(0, len(pcm), chunk_bytes):
        yield pcm[i : i + chunk_bytes]


def pcm16_to_wav(pcm: bytes, sample_rate: int = TARGET_SAMPLE_RATE) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm)
    return buf.getvalue()


def wav_bytes_to_pcm16_mono(
    wav: bytes,
    target_sr: int = TARGET_SAMPLE_RATE,
) -> tuple[bytes, int]:
    """Decode mono/stereo WAV to int16 PCM at ``target_sr``."""
    from web_server import _resample

    with wave.open(io.BytesIO(wav)) as wf:
        sr = wf.getframerate()
        nch = wf.getnchannels()
        sampwidth = wf.getsampwidth()
        if sampwidth != 2:
            raise ValueError(f"unsupported sample width {sampwidth}")
        frames = wf.readframes(wf.getnframes())
    samples = np.frombuffer(frames, dtype=np.int16)
    if nch > 1:
        samples = samples.reshape(-1, nch)[:, 0]
    audio = samples.astype(np.float32) / 32768.0
    if sr != target_sr:
        audio = _resample(audio, sr, target_sr)
    pcm = (np.clip(audio, -1.0, 1.0) * 32767).astype(np.int16).tobytes()
    return pcm, target_sr


def pad_utterance_pcm(
    speech_pcm: bytes,
    *,
    lead_silence_s: float = 0.5,
    trail_silence_s: float = 1.2,
    sample_rate: int = TARGET_SAMPLE_RATE,
) -> bytes:
    """Lead + speech + trail silence so server VAD can end the utterance."""
    lead = np.zeros(int(sample_rate * lead_silence_s), dtype=np.int16).tobytes()
    trail = np.zeros(int(sample_rate * trail_silence_s), dtype=np.int16).tobytes()
    return lead + speech_pcm + trail