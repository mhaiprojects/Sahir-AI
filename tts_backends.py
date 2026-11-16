"""Optional TTS backends. Piper stays the default in audio_pipeline.SpeechPipeline.

Kokoro-82M (Apache-2.0 weights) runs through sherpa-onnx (Apache-2.0), which ships Python 3.14
wheels (the standalone ``kokoro-onnx`` package requires Python < 3.14).
Model: ``bash scripts/setup_kokoro.sh`` → models/kokoro/kokoro-int8-multi-lang-v1_1.
"""

from __future__ import annotations

import io
import logging
import os
import re
import wave
from pathlib import Path

import numpy as np

logger = logging.getLogger(__name__)


class KokoroTTS:
    def __init__(self, model_dir: Path, *, speaker_id: int = 0, speed: float = 1.0, num_threads: int | None = None):
        import sherpa_onnx  # optional dependency

        model_dir = Path(model_dir)
        model = next((model_dir / n for n in ("model.int8.onnx", "model.onnx") if (model_dir / n).exists()), None)
        if model is None:
            raise FileNotFoundError(f"Kokoro model not found in {model_dir} (run scripts/setup_kokoro.sh)")
        lang = os.getenv("KOKORO_LEXICON", "us-en")
        preferred = model_dir / f"lexicon-{lang}.txt"
        lexicons = [str(preferred)] if preferred.exists() else sorted(str(p) for p in model_dir.glob("lexicon*.txt"))
        kokoro = sherpa_onnx.OfflineTtsKokoroModelConfig(
            model=str(model),
            voices=str(model_dir / "voices.bin"),
            tokens=str(model_dir / "tokens.txt"),
            lexicon=",".join(lexicons) if lexicons else "",
        )
        cfg = sherpa_onnx.OfflineTtsConfig(
            model=sherpa_onnx.OfflineTtsModelConfig(
                kokoro=kokoro, num_threads=num_threads or max(1, (os.cpu_count() or 4) // 2), provider="cpu"
            ),
            max_num_sentences=1,
        )
        if not cfg.validate():
            raise ValueError(f"Invalid Kokoro config for {model_dir}")
        self._tts = sherpa_onnx.OfflineTts(cfg)
        self.speaker_id = speaker_id
        self.speed = speed
        self.sample_rate = int(self._tts.sample_rate)
        logger.info("Kokoro TTS loaded from %s (%d speakers)", model_dir, self._tts.num_speakers)

    def synthesize(self, text: str) -> tuple[np.ndarray, int]:
        audio = self._tts.generate(text, sid=self.speaker_id, speed=self.speed)
        return np.asarray(audio.samples, dtype=np.float32), int(audio.sample_rate)


def float_to_wav_bytes(audio: np.ndarray, sample_rate: int) -> bytes:
    pcm16 = (np.clip(audio, -1.0, 1.0) * 32767).astype(np.int16)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm16.tobytes())
    return buf.getvalue()


_SENT_SPLIT = re.compile(r"(?<=[.!?;:])\s+|\n+")


def split_sentences(text: str, max_len: int = 240) -> list[str]:
    """Split reply text into speakable sentences for incremental (streamed) TTS."""
    out: list[str] = []
    for part in _SENT_SPLIT.split(text):
        part = part.strip()
        while len(part) > max_len:
            cut = part.rfind(",", 0, max_len)
            cut = cut if cut > 40 else max_len
            out.append(part[: cut + 1].strip())
            part = part[cut + 1 :].strip()
        if part:
            out.append(part)
    return out
