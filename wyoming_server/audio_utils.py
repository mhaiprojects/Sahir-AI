"""PCM helpers for Wyoming audio events."""

from __future__ import annotations

import numpy as np

from wake_kws import pcm_to_float32_mono, resample_float_mono

TARGET_RATE = 16_000
TARGET_WIDTH = 2
TARGET_CHANNELS = 1


def audio_chunks_to_mono16k(chunks: list) -> tuple[np.ndarray, float]:
    """Decode Wyoming AudioChunk list to float32 mono @ 16 kHz. Returns (audio, seconds)."""
    if not chunks:
        return np.array([], dtype=np.float32), 0.0
    rate = int(chunks[0].rate)
    parts: list[np.ndarray] = []
    seconds = 0.0
    for chunk in chunks:
        seconds += float(chunk.seconds)
        raw = chunk.audio
        if chunk.width == 2:
            pcm = np.frombuffer(raw, dtype=np.int16)
        elif chunk.width == 4:
            pcm = np.frombuffer(raw, dtype=np.int32)
            pcm = (pcm.astype(np.float32) / 2147483648.0 * 32767.0).astype(np.int16)
        else:
            raise ValueError(f"Unsupported audio width: {chunk.width}")
        if chunk.channels > 1:
            pcm = pcm.reshape(-1, chunk.channels).mean(axis=1).astype(np.int16)
        parts.append(pcm_to_float32_mono(pcm))
    audio = np.concatenate(parts) if parts else np.array([], dtype=np.float32)
    if rate != TARGET_RATE:
        audio = resample_float_mono(audio, rate, TARGET_RATE)
    return audio, seconds


def float_to_pcm16_chunks(audio: np.ndarray, sample_rate: int, *, samples_per_chunk: int = 1024) -> list[bytes]:
    pcm = (np.clip(audio, -1.0, 1.0) * 32767.0).astype(np.int16).tobytes()
    out: list[bytes] = []
    width = TARGET_WIDTH * TARGET_CHANNELS
    step = samples_per_chunk * width
    for i in range(0, len(pcm), step):
        out.append(pcm[i : i + step])
    return out or [b""]