"""Sherpa-onnx streaming keyword spotting for custom wake phrases."""

from __future__ import annotations

import logging
import os
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import numpy as np

from config import PROJECT_ROOT, Settings

logger = logging.getLogger(__name__)

KWS_EXTRACT_DIR = "sherpa-onnx-kws-zipformer-gigaspeech-3.3M-2024-01-01"
KWS_CHUNK_SAMPLES = 1600  # 100 ms @ 16 kHz (sherpa mic example)
TAIL_PADDING_SECONDS = 0.66  # sherpa KWS Python examples
WAKE_KEYWORDS_FILENAME = "keywords_wake.generated.txt"
ENCODER = "encoder-epoch-12-avg-2-chunk-16-left-64.int8.onnx"
DECODER = "decoder-epoch-12-avg-2-chunk-16-left-64.onnx"
JOINER = "joiner-epoch-12-avg-2-chunk-16-left-64.int8.onnx"


class KwsSpotter(Protocol):
    def accept_waveform(self, pcm16_mono_16k: np.ndarray) -> str | None: ...


class KwsTokenizeError(ValueError):
    pass


def kws_model_dir() -> Path:
    return PROJECT_ROOT / "models" / "kws" / KWS_EXTRACT_DIR


def kws_model_ready() -> bool:
    d = kws_model_dir()
    return d.is_dir() and (d / "tokens.txt").is_file() and (d / "bpe.model").is_file()


def parse_wake_phrases(raw: str) -> list[str]:
    parts = [p.strip() for p in (raw or "").split(",")]
    return [p for p in parts if p]


def encode_wake_phrase(phrase: str, *, model_dir: Path | None = None) -> tuple[str, str]:
    """Return (sherpa keyword line, human label). Display side must use tokens too."""
    model_dir = model_dir or kws_model_dir()
    try:
        import sentencepiece as spm
    except ImportError as exc:
        raise KwsTokenizeError(
            "sentencepiece is required for KWS phrase encoding (pip install sentencepiece)"
        ) from exc

    tokens_path = model_dir / "tokens.txt"
    bpe_path = model_dir / "bpe.model"
    if not tokens_path.is_file() or not bpe_path.is_file():
        raise KwsTokenizeError(f"KWS model files missing under {model_dir}")

    sym: set[str] = set()
    with tokens_path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            sym.add(line.split()[0])

    normalized = " ".join(phrase.strip().split()).upper()
    if not normalized:
        raise KwsTokenizeError("Empty wake phrase")

    sp = spm.SentencePieceProcessor()
    sp.load(str(bpe_path))
    toks = sp.encode(normalized, out_type=str)
    bad = [t for t in toks if t not in sym]
    if bad:
        raise KwsTokenizeError(
            f"Phrase {phrase!r} contains out-of-vocabulary BPE pieces: {', '.join(bad)}"
        )
    display = phrase.strip().lower()
    token_line = f"{' '.join(toks)} @{' '.join(toks)}"
    return token_line, display


def encode_wake_phrases(phrases: list[str], *, model_dir: Path | None = None) -> tuple[str, list[str]]:
    rows = [encode_wake_phrase(p, model_dir=model_dir) for p in phrases]
    return "\n".join(line for line, _ in rows), [label for _, label in rows]


def keyword_tokens_line(phrase: str, *, model_dir: Path | None = None) -> str:
    """Space-separated BPE tokens for sherpa ``keywords.txt`` (no ``@`` suffix)."""
    line, _ = encode_wake_phrase(phrase, model_dir=model_dir)
    return line.split("@", 1)[0].strip()


def write_wake_keywords_file(phrases: list[str], model_dir: Path | None = None) -> Path:
    """Persist wake phrases as the model keywords file (idempotent)."""
    model_dir = model_dir or kws_model_dir()
    path = model_dir / WAKE_KEYWORDS_FILENAME
    lines = [keyword_tokens_line(p, model_dir=model_dir) for p in phrases]
    content = "\n".join(lines) + ("\n" if lines else "")
    if path.is_file() and path.read_text(encoding="utf-8") == content:
        return path
    path.write_text(content, encoding="utf-8")
    return path


def pcm_to_float32_mono(pcm: np.ndarray) -> np.ndarray:
    """Convert int16/float PCM to float32 in [-1, 1] for sherpa feature extraction."""
    x = np.asarray(pcm).reshape(-1)
    if x.dtype == np.int16:
        return (x.astype(np.float32) / 32768.0).astype(np.float32, copy=False)
    x = x.astype(np.float32, copy=False)
    if x.size == 0:
        return x
    peak = float(np.max(np.abs(x)))
    if peak > 1.0:
        if peak <= 32767.0:
            x = x / 32768.0
        else:
            x = x / peak
    return np.clip(x, -1.0, 1.0).astype(np.float32, copy=False)


def resample_float_mono(audio: np.ndarray, src_sr: int, dst_sr: int = 16_000) -> np.ndarray:
    if src_sr == dst_sr or audio.size == 0:
        return audio.astype(np.float32, copy=False)
    n = int(len(audio) * dst_sr / src_sr)
    x = np.linspace(0.0, 1.0, num=len(audio), endpoint=False)
    y = np.linspace(0.0, 1.0, num=max(n, 1), endpoint=False)
    return np.interp(y, x, audio).astype(np.float32)


def resolve_wakeword_engine(settings: Settings) -> str:
    raw = (os.getenv("WAKEWORD_ENGINE") or settings.wakeword_engine or "openwakeword").strip().lower()
    return raw if raw in ("openwakeword", "kws") else "openwakeword"


@dataclass
class KwsWakeSpotter:
    """Streaming wake phrase detector (one shared KeywordSpotter stream)."""

    phrases: list[str]
    keywords_threshold: float = 0.25
    keywords_score: float = 1.5
    num_threads: int = 2

    def __post_init__(self) -> None:
        self._lock = threading.Lock()
        self._spotter = None
        self._stream = None
        self._labels: list[str] = []

    def _ensure(self) -> None:
        if self._spotter is not None:
            return
        if not kws_model_ready():
            raise FileNotFoundError(
                f"KWS model not found under {kws_model_dir()} (run scripts/fetch_kws.py)"
            )
        import sherpa_onnx

        d = kws_model_dir()
        _, labels = encode_wake_phrases(self.phrases, model_dir=d)
        self._labels = labels
        keywords_path = write_wake_keywords_file(self.phrases, model_dir=d)

        self._spotter = sherpa_onnx.KeywordSpotter(
            tokens=str(d / "tokens.txt"),
            encoder=str(d / ENCODER),
            decoder=str(d / DECODER),
            joiner=str(d / JOINER),
            keywords_file=str(keywords_path),
            num_threads=self.num_threads,
            keywords_score=self.keywords_score,
            keywords_threshold=self.keywords_threshold,
            provider="cpu",
        )
        self._stream = self._spotter.create_stream()
        logger.info("KWS spotter ready (%d phrases)", len(self.phrases))

    def _match_label(self, hit: str) -> str | None:
        if not hit:
            return None
        for label in self._labels:
            if label.lower() in hit.lower() or hit.lower() in label.lower():
                return label
        if self._labels:
            return self._labels[0]
        return hit.lower()

    def _decode_chunk(self, audio: np.ndarray, sample_rate: int = 16_000) -> str | None:
        assert self._spotter is not None and self._stream is not None
        self._stream.accept_waveform(sample_rate, audio)
        while self._spotter.is_ready(self._stream):
            self._spotter.decode_stream(self._stream)
        hit = (self._spotter.get_result(self._stream) or "").strip()
        if not hit:
            return None
        self._spotter.reset_stream(self._stream)
        return self._match_label(hit)

    def accept_waveform(self, pcm16_mono_16k: np.ndarray) -> str | None:
        if pcm16_mono_16k.size == 0:
            return None
        audio = pcm_to_float32_mono(pcm16_mono_16k)
        with self._lock:
            self._ensure()
            return self._decode_chunk(audio)

    def detect_in_clip(self, audio: np.ndarray, *, sample_rate: int = 16_000) -> str | None:
        """Offline/clip scan: reset stream, feed audio + tail, ``input_finished`` (eval path)."""
        audio = resample_float_mono(pcm_to_float32_mono(audio), sample_rate, 16_000)
        sample_rate = 16_000
        if audio.size == 0:
            return None
        tail = np.zeros(int(TAIL_PADDING_SECONDS * sample_rate), dtype=np.float32)
        chunk = KWS_CHUNK_SAMPLES
        with self._lock:
            self._ensure()
            assert self._spotter is not None
            stream = self._spotter.create_stream()
            for sample in (audio, tail):
                for i in range(0, len(sample), chunk):
                    block = sample[i : i + chunk]
                    if block.size == 0:
                        continue
                    stream.accept_waveform(sample_rate, block)
                    while self._spotter.is_ready(stream):
                        self._spotter.decode_stream(stream)
                    hit = (self._spotter.get_result(stream) or "").strip()
                    if hit:
                        self._spotter.reset_stream(stream)
                        self._stream = self._spotter.create_stream()
                        return self._match_label(hit)
            stream.input_finished()
            while self._spotter.is_ready(stream):
                self._spotter.decode_stream(stream)
            hit = (self._spotter.get_result(stream) or "").strip()
            self._stream = self._spotter.create_stream()
            return self._match_label(hit)


def build_kws_spotter(settings: Settings) -> KwsWakeSpotter:
    phrases = parse_wake_phrases(settings.wake_phrases)
    if not phrases:
        raise ValueError("WAKE_PHRASES is empty but WAKEWORD_ENGINE=kws")
    return KwsWakeSpotter(
        phrases=phrases,
        keywords_threshold=settings.wake_kws_threshold,
        keywords_score=settings.wake_kws_score,
        num_threads=max(1, int(os.getenv("WAKE_KWS_THREADS", "2"))),
    )