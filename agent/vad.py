"""Silero VAD (MIT) streaming wrapper + utterance endpointer for the realtime voice loop.

Uses the Silero v5 ONNX model that ships with pipecat (no extra download). Falls back to an
RMS energy detector if onnxruntime or the model file is unavailable.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

logger = logging.getLogger(__name__)

SAMPLE_RATE = 16_000
FRAME = 512  # 32 ms @ 16 kHz (required window for Silero v5)
_CONTEXT = 64


def _find_model() -> Path | None:
    try:
        import pipecat  # type: ignore

        p = Path(pipecat.__file__).parent / "audio" / "vad" / "data" / "silero_vad.onnx"
        if p.exists():
            return p
    except Exception:
        pass
    try:
        import openwakeword  # type: ignore

        p = Path(openwakeword.__file__).parent / "resources" / "models" / "silero_vad.onnx"
        if p.exists():
            return p
    except Exception:
        pass
    return None


class SileroVAD:
    """Frame-level speech probability for 512-sample float32 frames at 16 kHz."""

    def __init__(self, model_path: Path | None = None) -> None:
        self.backend = "energy"
        self._session = None
        path = model_path or _find_model()
        if path is not None:
            try:
                import onnxruntime as ort

                opts = ort.SessionOptions()
                opts.inter_op_num_threads = 1
                opts.intra_op_num_threads = 1
                self._session = ort.InferenceSession(str(path), sess_options=opts,
                                                     providers=["CPUExecutionProvider"])
                self.backend = "silero"
            except Exception as exc:  # pragma: no cover
                logger.warning("Silero VAD unavailable, using energy VAD: %s", exc)
        self.reset()

    def reset(self) -> None:
        self._state = np.zeros((2, 1, 128), dtype=np.float32)
        self._context = np.zeros((1, _CONTEXT), dtype=np.float32)

    def prob(self, frame: np.ndarray) -> float:
        frame = np.asarray(frame, dtype=np.float32).reshape(1, -1)
        if frame.shape[1] != FRAME:
            raise ValueError(f"VAD frame must be {FRAME} samples")
        if self._session is None:
            rms = float(np.sqrt(np.mean(frame**2)))
            return float(min(1.0, rms / 0.02))
        x = np.concatenate([self._context, frame], axis=1)
        out, self._state = self._session.run(
            None, {"input": x, "state": self._state, "sr": np.array(SAMPLE_RATE, dtype=np.int64)}
        )
        self._context = x[:, -_CONTEXT:]
        return float(out[0][0])


def speech_seconds(audio: np.ndarray, threshold: float = 0.5, vad: SileroVAD | None = None) -> float:
    """Total seconds of speech in a 16 kHz mono float32 clip (used as a gate before Whisper)."""
    vad = vad or SileroVAD()
    vad.reset()
    n = len(audio) // FRAME
    voiced = sum(1 for i in range(n) if vad.prob(audio[i * FRAME:(i + 1) * FRAME]) >= threshold)
    return voiced * FRAME / SAMPLE_RATE


@dataclass
class Endpointer:
    """Turns a stream of frames into utterances: emits speech_start / utterance events."""

    threshold: float = 0.5
    min_speech_ms: int = 200
    end_silence_ms: int = 700
    max_utterance_s: float = 30.0
    pre_roll_ms: int = 300
    vad: SileroVAD = field(default_factory=SileroVAD)

    def __post_init__(self) -> None:
        self._buf = np.zeros(0, dtype=np.float32)
        self.reset()

    def reset(self) -> None:
        self.in_speech = False
        self._speech_frames: list[np.ndarray] = []
        self._pre: list[np.ndarray] = []
        self._voiced_ms = 0
        self._silence_ms = 0
        self._started_emitted = False

    def feed(self, pcm: np.ndarray) -> list[tuple[str, np.ndarray | None]]:
        """Feed float32 16 kHz samples; returns events [("speech_start", None) | ("utterance", audio)]."""
        events: list[tuple[str, np.ndarray | None]] = []
        self._buf = np.concatenate([self._buf, np.asarray(pcm, dtype=np.float32)])
        frame_ms = FRAME * 1000 // SAMPLE_RATE
        while len(self._buf) >= FRAME:
            frame, self._buf = self._buf[:FRAME], self._buf[FRAME:]
            p = self.vad.prob(frame)
            voiced = p >= self.threshold
            if not self.in_speech:
                self._pre.append(frame)
                max_pre = max(1, self.pre_roll_ms // frame_ms)
                self._pre = self._pre[-max_pre:]
                if voiced:
                    self._voiced_ms += frame_ms
                    if self._voiced_ms >= self.min_speech_ms:
                        self.in_speech = True
                        self._speech_frames = list(self._pre)
                        self._silence_ms = 0
                        events.append(("speech_start", None))
                else:
                    self._voiced_ms = 0
                continue
            self._speech_frames.append(frame)
            self._silence_ms = 0 if voiced else self._silence_ms + frame_ms
            dur_s = len(self._speech_frames) * FRAME / SAMPLE_RATE
            if self._silence_ms >= self.end_silence_ms or dur_s >= self.max_utterance_s:
                audio = np.concatenate(self._speech_frames)
                self.reset()
                events.append(("utterance", audio))
        return events

    def flush(self) -> np.ndarray | None:
        if self.in_speech and self._speech_frames:
            audio = np.concatenate(self._speech_frames)
            self.reset()
            return audio
        self.reset()
        return None
