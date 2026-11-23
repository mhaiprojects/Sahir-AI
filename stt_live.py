"""Live (partial) STT backends: Moonshine via sherpa-onnx or faster-whisper tiny."""

from __future__ import annotations

import logging
import os
import threading
from pathlib import Path
from typing import Protocol

import numpy as np

from config import PROJECT_ROOT, Settings

logger = logging.getLogger(__name__)

MOONSHINE_EXTRACT_DIR = "sherpa-onnx-moonshine-tiny-en-int8"
MOONSHINE_REQUIRED = (
    "preprocess.onnx",
    "encode.int8.onnx",
    "uncached_decode.int8.onnx",
    "cached_decode.int8.onnx",
    "tokens.txt",
)


def moonshine_model_dir() -> Path:
    return PROJECT_ROOT / "models" / "moonshine" / MOONSHINE_EXTRACT_DIR


def moonshine_model_ready() -> bool:
    d = moonshine_model_dir()
    return d.is_dir() and all((d / name).is_file() for name in MOONSHINE_REQUIRED)


class LiveSttBackend(Protocol):
    name: str

    def transcribe_partial(self, pcm16_mono_16k: np.ndarray) -> str:
        """pcm: float32 mono @ 16 kHz."""


def _auto_backend_from_benchmark() -> str | None:
    path = PROJECT_ROOT / "data" / "bench" / "live_stt.json"
    if not path.is_file():
        return None
    try:
        import json

        data = json.loads(path.read_text(encoding="utf-8"))
        moon = (data.get("backends") or {}).get("moonshine")
        wh = (data.get("backends") or {}).get("whisper")
        if not moon or not wh:
            return None
        if (moon.get("wer_mean") or 1) > (wh.get("wer_mean") or 0) + 0.05:
            return "whisper"
        if (moon.get("latency_ms_p95") or 1e9) > (wh.get("latency_ms_p95") or 0) * 1.25:
            return "whisper"
        return "moonshine"
    except (OSError, ValueError, TypeError):
        return None


def resolve_live_stt_backend_name(settings: Settings) -> str:
    env = (os.getenv("WEB_LIVE_STT_BACKEND") or settings.web_live_stt_backend or "").strip().lower()
    if env in ("moonshine", "whisper"):
        return env
    if moonshine_model_ready():
        pick = _auto_backend_from_benchmark()
        if pick:
            return pick
        return "moonshine"
    logger.info("Moonshine model not found; live STT using whisper (%s)", settings.whisper_live_model)
    return "whisper"


def live_stt_status(settings: Settings) -> dict:
    chosen = resolve_live_stt_backend_name(settings)
    moon_ready = moonshine_model_ready()
    return {
        "backend": chosen,
        "moonshine_ready": moon_ready,
        "whisper_live_model": settings.whisper_live_model,
        "whisper_final_model": settings.whisper_final_model,
        "moonshine_dir": str(moonshine_model_dir()) if moon_ready else "",
    }


class WhisperLiveBackend:
    name = "whisper"

    def __init__(self, pipeline: object) -> None:
        self._pipeline = pipeline

    def transcribe_partial(self, pcm16_mono_16k: np.ndarray) -> str:
        return self._pipeline.transcribe(audio=pcm16_mono_16k, tier="live")  # type: ignore[attr-defined]


class MoonshineLiveBackend:
    name = "moonshine"

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._lock = threading.Lock()
        self._recognizer = None

    def _get_recognizer(self):
        if self._recognizer is not None:
            return self._recognizer
        import sherpa_onnx

        d = moonshine_model_dir()
        if not moonshine_model_ready():
            raise FileNotFoundError(f"Moonshine model not found under {d}")
        self._recognizer = sherpa_onnx.OfflineRecognizer.from_moonshine(
            preprocessor=str(d / "preprocess.onnx"),
            encoder=str(d / "encode.int8.onnx"),
            uncached_decoder=str(d / "uncached_decode.int8.onnx"),
            cached_decoder=str(d / "cached_decode.int8.onnx"),
            tokens=str(d / "tokens.txt"),
            num_threads=max(1, int(os.getenv("MOONSHINE_THREADS", "2"))),
            provider="cpu",
        )
        logger.info("Loaded Moonshine live STT from %s", d)
        return self._recognizer

    def transcribe_partial(self, pcm16_mono_16k: np.ndarray) -> str:
        if pcm16_mono_16k.size == 0:
            return ""
        audio = np.asarray(pcm16_mono_16k, dtype=np.float32).reshape(-1)
        clip = float(np.max(np.abs(audio)))
        if clip > 1.0:
            audio = audio / clip
        with self._lock:
            recognizer = self._get_recognizer()
            stream = recognizer.create_stream()
            stream.accept_waveform(16000, audio)
            recognizer.decode_stream(stream)
            text = (stream.result.text or "").strip()
        return text


def build_live_stt_backend(settings: Settings, *, pipeline: object | None = None) -> LiveSttBackend:
    name = resolve_live_stt_backend_name(settings)
    if name == "moonshine":
        if not moonshine_model_ready():
            logger.warning("WEB_LIVE_STT_BACKEND=moonshine but model missing; falling back to whisper")
            name = "whisper"
        else:
            return MoonshineLiveBackend(settings)
    if pipeline is None:
        raise ValueError("WhisperLiveBackend requires speech pipeline")
    return WhisperLiveBackend(pipeline)