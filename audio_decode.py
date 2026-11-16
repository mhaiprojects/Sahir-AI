"""Decode browser-uploaded audio into mono float32 PCM."""

from __future__ import annotations

import io
import logging
import shutil
import subprocess
import wave
from typing import Callable

import numpy as np

logger = logging.getLogger(__name__)

MIN_AUDIO_BYTES = 64
WAV_HEADER_BYTES = 44

# ffmpeg/PyAV messages that mean the upload is a fragment (truncated or
# headerless MediaRecorder chunk) rather than a broken pipeline.
_PARTIAL_MARKERS = (
    "ended prematurely",
    "invalid data found",
    "input/output error",
    "end of file",
    "duplicate element",
    "eof",
    "no audio stream",
    "empty",
)


class PartialAudioError(ValueError):
    """Upload is truncated / incomplete audio; callers should skip it quietly."""


def _first_line(text: str) -> str:
    for line in str(text).splitlines():
        line = line.strip()
        if line:
            return line[:200]
    return ""


def _looks_partial(message: str) -> bool:
    lowered = message.lower()
    return any(marker in lowered for marker in _PARTIAL_MARKERS)


def _wav_bytes_to_float32(data: bytes) -> tuple[np.ndarray, int]:
    with wave.open(io.BytesIO(data), "rb") as wf:
        channels = wf.getnchannels()
        sample_rate = wf.getframerate()
        sample_width = wf.getsampwidth()
        n_frames = wf.getnframes()
        raw = wf.readframes(n_frames)

    if sample_width == 2:
        audio = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    elif sample_width == 4:
        audio = np.frombuffer(raw, dtype=np.int32).astype(np.float32) / 2147483648.0
    else:
        raise ValueError(f"Unsupported WAV sample width: {sample_width}")

    if channels > 1:
        audio = audio.reshape(-1, channels).mean(axis=1)

    return audio, sample_rate


def _detect_container(data: bytes) -> str:
    if len(data) >= 4 and data[:4] == b"RIFF":
        return "wav"
    if len(data) >= 4 and data[:4] == b"OggS":
        return "ogg"
    if len(data) >= 4 and data[:4] == b"\x1aE\xdf\xa3":
        return "webm"
    return "unknown"


def _decode_with_ffmpeg(data: bytes, container: str) -> bytes:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("ffmpeg is not installed")

    cmd = [
        ffmpeg,
        "-hide_banner",
        "-loglevel",
        "error",
        "-nostdin",
        "-fflags",
        "+discardcorrupt",
    ]
    if container in {"webm", "ogg", "wav"}:
        cmd.extend(["-f", container])
    cmd.extend(
        [
            "-i",
            "pipe:0",
            "-f",
            "wav",
            "-acodec",
            "pcm_s16le",
            "-ac",
            "1",
            "pipe:1",
        ]
    )

    logger.debug(
        "Running ffmpeg decode for container=%s bytes=%d",
        container,
        len(data),
    )
    proc = subprocess.run(
        cmd,
        input=data,
        capture_output=True,
        check=False,
    )
    if proc.returncode != 0:
        stderr = proc.stderr.decode(errors="replace").strip()
        # Truncated WebM often still yields decodable frames before the cut.
        if len(proc.stdout) > WAV_HEADER_BYTES and proc.stdout[:4] == b"RIFF":
            logger.debug("ffmpeg exited %s but salvaged %d bytes", proc.returncode, len(proc.stdout))
            return proc.stdout
        raise RuntimeError(stderr or "ffmpeg failed")
    if not proc.stdout:
        raise RuntimeError("ffmpeg returned empty output")
    return proc.stdout


def _decode_with_pyav(data: bytes) -> bytes:
    try:
        import av
    except ImportError as exc:
        raise RuntimeError("PyAV is not installed") from exc

    logger.debug("Running PyAV decode bytes=%d", len(data))
    out = io.BytesIO()
    with av.open(io.BytesIO(data)) as container:
        audio_stream = next((s for s in container.streams if s.type == "audio"), None)
        if audio_stream is None:
            raise RuntimeError("No audio stream found")

        sample_rate = audio_stream.codec_context.sample_rate or 16_000
        resampler = av.audio.resampler.AudioResampler(format="s16", layout="mono", rate=sample_rate)

        with wave.open(out, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(sample_rate)
            for frame in container.decode(audio=0):
                for resampled in resampler.resample(frame):
                    wf.writeframes(resampled.to_ndarray().tobytes())

    wav_bytes = out.getvalue()
    if not wav_bytes:
        raise RuntimeError("PyAV produced empty WAV output")
    return wav_bytes


def decode_audio_bytes(data: bytes, content_type: str | None = None) -> tuple[np.ndarray, int]:
    """Decode uploaded audio (WAV/WebM/OGG) into mono float32 samples."""
    if not data:
        raise ValueError("No audio data received")
    if len(data) < MIN_AUDIO_BYTES:
        raise PartialAudioError(
            f"Could not decode audio. Audio too short to decode ({len(data)} bytes)"
        )

    container = _detect_container(data)
    content_type = (content_type or "").lower()
    logger.info(
        "Decoding audio upload: bytes=%d container=%s content_type=%s magic=%r",
        len(data),
        container,
        content_type or "unknown",
        data[:4],
    )

    if container == "wav" or content_type.endswith("wav"):
        try:
            audio, sample_rate = _wav_bytes_to_float32(data)
            logger.info("Decoded WAV audio: samples=%d rate=%d", audio.size, sample_rate)
            return audio, sample_rate
        except Exception as exc:
            logger.warning("Direct WAV decode failed: %s", exc)

    decoders: list[tuple[str, Callable[[], bytes]]] = [
        ("ffmpeg", lambda: _decode_with_ffmpeg(data, container)),
        ("pyav", lambda: _decode_with_pyav(data)),
    ]
    errors: list[str] = []
    for name, decoder in decoders:
        try:
            wav_bytes = decoder()
            audio, sample_rate = _wav_bytes_to_float32(wav_bytes)
            if audio.size == 0:
                raise RuntimeError("decoded audio is empty")
            logger.info(
                "Decoded audio via %s: samples=%d rate=%d",
                name,
                audio.size,
                sample_rate,
            )
            return audio, sample_rate
        except Exception as exc:
            message = f"{name}: {_first_line(exc) or type(exc).__name__}"
            logger.debug("Audio decode attempt failed (%s): %s", name, exc)
            errors.append(message)

    summary = "Could not decode audio. " + (
        "; ".join(errors) if errors else "No decoders available"
    )
    if container == "unknown" or any(_looks_partial(err) for err in errors):
        logger.warning(
            "Skipping partial/truncated audio (bytes=%d container=%s): %s",
            len(data),
            container,
            "; ".join(errors),
        )
        raise PartialAudioError(summary)
    logger.warning("Audio decode failed (bytes=%d container=%s): %s", len(data), container, summary)
    raise ValueError(summary)
