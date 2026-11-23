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
import time
import wave
from pathlib import Path

import numpy as np

from kokoro_speech import (
    KokoroLexicon,
    default_user_lexicon_path,
    kokoro_oov_fallback_total,
    prepare_kokoro_text,
)

logger = logging.getLogger(__name__)

_DEFAULT_KOKORO_THREADS = 4


def kokoro_tts_health() -> dict[str, int | float | str]:
    return {
        "kokoro_oov_fallbacks": kokoro_oov_fallback_total(),
        "default_num_threads": _default_kokoro_num_threads(),
    }


def _default_kokoro_num_threads() -> int:
    raw = os.getenv("KOKORO_NUM_THREADS", "").strip()
    if raw.isdigit():
        return max(1, int(raw))
    return _DEFAULT_KOKORO_THREADS


def _phoneme_lines_from_user_lexicon(user_path: Path) -> list[str]:
    from kokoro_speech import _IPA_MARK

    lines: list[str] = []
    with user_path.open(encoding="utf-8", errors="replace") as fh:
        for raw in fh:
            line = raw.strip()
            if not line or line.startswith("#") or "->" in line:
                continue
            parts = line.split()
            if len(parts) < 2:
                continue
            tail = " ".join(parts[1:])
            if _IPA_MARK.search(tail):
                lines.append(line)
    return lines


def _materialize_user_phoneme_lexicon(user_path: Path, cache_path: Path) -> Path | None:
    rows = _phoneme_lines_from_user_lexicon(user_path)
    if not rows:
        return None
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text("\n".join(rows) + "\n", encoding="utf-8")
    return cache_path


def _kokoro_lexicon_paths(model_dir: Path, lang: str) -> list[str]:
    preferred = model_dir / f"lexicon-{lang}.txt"
    paths: list[Path] = []
    if preferred.exists():
        paths.append(preferred)
    else:
        paths.extend(sorted(model_dir.glob("lexicon*.txt")))
    for user_path in (default_user_lexicon_path(), model_dir.parent / "user_lexicon.txt", model_dir / "user_lexicon.txt"):
        if not user_path.is_file():
            continue
        cache = user_path.parent / "user_phoneme_lexicon.auto.txt"
        materialized = _materialize_user_phoneme_lexicon(user_path, cache)
        if materialized is not None and materialized not in paths:
            paths.append(materialized)
        break
    return [str(p) for p in paths]


class KokoroTTS:
    def __init__(
        self,
        model_dir: Path,
        *,
        speaker_id: int = 0,
        speed: float = 1.0,
        num_threads: int | None = None,
        lexicon: KokoroLexicon | None = None,
    ):
        import sherpa_onnx  # optional dependency

        model_dir = Path(model_dir)
        model = next((model_dir / n for n in ("model.int8.onnx", "model.onnx") if (model_dir / n).exists()), None)
        if model is None:
            raise FileNotFoundError(f"Kokoro model not found in {model_dir} (run scripts/setup_kokoro.sh)")
        lang = os.getenv("KOKORO_LEXICON", "us-en")
        lexicon_paths = _kokoro_lexicon_paths(model_dir, lang)
        user_lex = default_user_lexicon_path()
        if lexicon is None:
            base_paths = [p for p in lexicon_paths if Path(p) != user_lex]
            self._lexicon = KokoroLexicon.load(*base_paths, user_lexicon=user_lex)
        else:
            self._lexicon = lexicon
        kokoro = sherpa_onnx.OfflineTtsKokoroModelConfig(
            model=str(model),
            voices=str(model_dir / "voices.bin"),
            tokens=str(model_dir / "tokens.txt"),
            lexicon=",".join(lexicon_paths) if lexicon_paths else "",
        )
        threads = num_threads if num_threads is not None else _default_kokoro_num_threads()
        cfg = sherpa_onnx.OfflineTtsConfig(
            model=sherpa_onnx.OfflineTtsModelConfig(
                kokoro=kokoro, num_threads=threads, provider="cpu"
            ),
            max_num_sentences=1,
        )
        if not cfg.validate():
            raise ValueError(f"Invalid Kokoro config for {model_dir}")
        self._tts = sherpa_onnx.OfflineTts(cfg)
        self.speaker_id = speaker_id
        self.speed = speed
        self.sample_rate = int(self._tts.sample_rate)
        self.num_threads = threads
        logger.info(
            "Kokoro TTS loaded from %s (%d speakers, %d threads)",
            model_dir,
            self._tts.num_speakers,
            threads,
        )

    @property
    def lexicon(self) -> KokoroLexicon:
        return self._lexicon

    def prepare_text(self, text: str) -> str:
        return prepare_kokoro_text(text, self._lexicon)

    def synthesize(self, text: str, *, prepare: bool = True) -> tuple[np.ndarray, int]:
        speakable = self.prepare_text(text) if prepare else text
        audio = self._tts.generate(speakable, sid=self.speaker_id, speed=self.speed)
        return np.asarray(audio.samples, dtype=np.float32), int(audio.sample_rate)

    def synthesize_stream_sentences(self, text: str) -> list[tuple[np.ndarray, int, str]]:
        """Synthesize one sentence at a time for lower time-to-first-audio."""
        chunks: list[tuple[np.ndarray, int, str]] = []
        for sentence in split_sentences(self.prepare_text(text)):
            if not sentence.strip():
                continue
            audio, sr = self.synthesize(sentence)
            chunks.append((audio, sr, sentence))
        return chunks


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


def measure_kokoro_rtf(
    model_dir: Path,
    text: str,
    *,
    thread_counts: tuple[int, ...] = (1, 2, 4),
) -> dict[str, object]:
    """Benchmark Kokoro RTF for several thread counts (requires model on disk)."""
    results: dict[str, object] = {"text_chars": len(text), "runs": []}
    best_threads = _DEFAULT_KOKORO_THREADS
    best_rtf = float("inf")
    ttfb: float | None = None
    for n in thread_counts:
        t0 = time.perf_counter()
        tts = KokoroTTS(model_dir, num_threads=n)
        load_s = time.perf_counter() - t0
        s0 = time.perf_counter()
        sentences = split_sentences(tts.prepare_text(text))
        first: np.ndarray | None = None
        sr = tts.sample_rate
        if sentences:
            audio, sr = tts.synthesize(sentences[0])
            first = audio
            ttfb = time.perf_counter() - s0
        t1 = time.perf_counter()
        audio, sr = tts.synthesize(text)
        synth_s = time.perf_counter() - t1
        dur = float(len(audio) / sr) if sr and len(audio) else 0.0
        rtf = synth_s / dur if dur > 0 else None
        row = {
            "num_threads": n,
            "load_s": round(load_s, 3),
            "synth_s": round(synth_s, 3),
            "audio_s": round(dur, 3),
            "rtf": round(rtf, 3) if rtf is not None else None,
            "time_to_first_audio_s": round(ttfb, 3) if ttfb is not None and n == thread_counts[0] else None,
        }
        results["runs"].append(row)
        if rtf is not None and rtf < best_rtf:
            best_rtf = rtf
            best_threads = n
    results["recommended_num_threads"] = best_threads
    results["best_rtf"] = round(best_rtf, 3) if best_rtf != float("inf") else None
    return results