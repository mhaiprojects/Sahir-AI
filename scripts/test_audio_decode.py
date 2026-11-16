#!/usr/bin/env python3
"""Tests for uploaded audio decoding."""

from __future__ import annotations

import io
import subprocess
import sys
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from audio_decode import PartialAudioError, decode_audio_bytes


def _make_wav_bytes(duration_s: float = 0.25, sample_rate: int = 16_000) -> bytes:
    import numpy as np

    samples = int(duration_s * sample_rate)
    audio = (np.sin(np.linspace(0, 12, samples)) * 0.2 * 32767).astype("int16")
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(audio.tobytes())
    return buf.getvalue()


def _make_webm_bytes(wav_bytes: bytes) -> bytes:
    proc = subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "wav",
            "-i",
            "pipe:0",
            "-f",
            "webm",
            "pipe:1",
        ],
        input=wav_bytes,
        capture_output=True,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.decode())
    return proc.stdout


def main() -> int:
    failed = 0

    wav = _make_wav_bytes()
    pcm, rate = decode_audio_bytes(wav, "audio/wav")
    if pcm.size == 0 or rate != 16_000:
        print("FAIL: wav decode", pcm.size, rate)
        failed += 1
    else:
        print("ok: wav decode", pcm.size, rate)

    webm = _make_webm_bytes(wav)
    pcm2, rate2 = decode_audio_bytes(webm, "audio/webm")
    if pcm2.size == 0:
        print("FAIL: webm decode")
        failed += 1
    else:
        print("ok: webm decode", pcm2.size, rate2)

    long_webm = _make_webm_bytes(_make_wav_bytes(duration_s=2.0))
    try:
        pcm3, _ = decode_audio_bytes(long_webm[: len(long_webm) // 2], "audio/webm")
        if pcm3.size == 0:
            raise RuntimeError("empty")
        print("ok: truncated webm salvaged", pcm3.size)
    except Exception as exc:
        print("FAIL: truncated webm salvage", exc)
        failed += 1

    for label, chunk in (
        ("header-only webm", long_webm[:300]),
        ("headerless webm tail", long_webm[len(long_webm) // 2 :]),
        ("tiny upload", long_webm[:20]),
    ):
        try:
            decode_audio_bytes(chunk, "audio/webm")
            print(f"FAIL: {label} should raise PartialAudioError")
            failed += 1
        except PartialAudioError:
            print(f"ok: {label} -> PartialAudioError")
        except Exception as exc:
            print(f"FAIL: {label} raised {type(exc).__name__}: {exc}")
            failed += 1

    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
