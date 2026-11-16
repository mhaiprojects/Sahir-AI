"""Speech-to-text, text-to-speech, and microphone recording."""

from __future__ import annotations

import io
import logging
import threading
import time
import wave
from typing import Callable

import numpy as np
import sounddevice as sd
from faster_whisper import WhisperModel
from piper import PiperVoice

from config import Settings
from markdown_speech import strip_markdown_for_speech

logger = logging.getLogger(__name__)


class SpeechPipeline:
    """Local STT/TTS using faster-whisper and Piper."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._whisper_models: dict[str, WhisperModel] = {}
        self._piper: PiperVoice | None = None
        self._kokoro = None
        self._kokoro_failed = False
        self._speak_lock = threading.Lock()

    def _get_whisper(self, model_name: str | None = None) -> WhisperModel:
        model_name = model_name or self.settings.whisper_model
        if model_name not in self._whisper_models:
            logger.info(
                "Loading Whisper model %s on %s",
                model_name,
                self.settings.whisper_device,
            )
            self._whisper_models[model_name] = WhisperModel(
                model_name,
                device=self.settings.whisper_device,
                compute_type=self.settings.whisper_compute_type,
            )
        return self._whisper_models[model_name]

    def transcribe(
        self,
        audio: np.ndarray,
        *,
        tier: str = "default",
    ) -> str:
        if audio.size == 0:
            return ""

        if tier == "live":
            model_name = self.settings.whisper_live_model
            vad_filter = False
            beam_size = 1
            best_of = 1
            temperature = 0.0
        elif tier == "final":
            model_name = self.settings.whisper_final_model
            vad_filter = True
            beam_size = 5
            best_of = 5
            temperature = 0.0
        else:
            model_name = self.settings.whisper_model
            vad_filter = True
            beam_size = 5
            best_of = 5
            temperature = 0.0

        model = self._get_whisper(model_name)
        segments, _info = model.transcribe(
            audio,
            language="en",
            vad_filter=vad_filter,
            beam_size=beam_size,
            best_of=best_of,
            temperature=temperature,
        )
        text = " ".join(segment.text.strip() for segment in segments).strip()
        logger.info("Transcribed (%s/%s): %r", tier, model_name, text)
        return text

    def transcribe_live(self, audio: np.ndarray) -> str:
        """Fast partial transcription while the user is still speaking."""
        return self.transcribe(audio, tier="live")

    def transcribe_final(self, audio: np.ndarray) -> str:
        """Higher-quality transcription after a conversation ends."""
        return self.transcribe(audio, tier="final")

    def _get_piper(self) -> PiperVoice:
        if self._piper is None:
            model_path = self.settings.piper_model_path
            if not model_path.exists():
                raise FileNotFoundError(f"Piper model not found: {model_path}")
            logger.info("Loading Piper voice from %s", model_path)
            self._piper = PiperVoice.load(str(model_path))
        return self._piper

    def _get_kokoro(self):
        if self._kokoro is None and not self._kokoro_failed:
            try:
                from tts_backends import KokoroTTS

                self._kokoro = KokoroTTS(
                    self.settings.kokoro_model_dir,
                    speaker_id=self.settings.kokoro_speaker_id,
                    speed=self.settings.kokoro_speed,
                )
            except Exception as exc:
                self._kokoro_failed = True
                logger.warning("Kokoro TTS unavailable, falling back to Piper: %s", exc)
        return self._kokoro

    @property
    def tts_backend(self) -> str:
        if self.settings.tts_backend == "kokoro" and self._get_kokoro() is not None:
            return "kokoro"
        return "piper"

    def synthesize_float(self, text: str) -> tuple[np.ndarray, int]:
        """Synthesize already-cleaned text with the configured backend (Piper fallback)."""
        if self.tts_backend == "kokoro":
            return self._kokoro.synthesize(text)
        voice = self._get_piper()
        chunks = [
            np.array(c.audio_float_array, dtype=np.float32) for c in voice.synthesize(text)
        ]
        audio = np.concatenate(chunks) if chunks else np.array([], dtype=np.float32)
        return audio, voice.config.sample_rate

    def speak(self, text: str) -> None:
        text = strip_markdown_for_speech(text)
        if not text.strip():
            return
        with self._speak_lock:
            audio, sample_rate = self.synthesize_float(text)
            if audio.size == 0:
                return
            sd.play(audio, sample_rate)
            sd.wait()

    def synthesize_wav(self, text: str) -> tuple[bytes, int]:
        """Return WAV bytes and sample rate for browser playback."""
        text = strip_markdown_for_speech(text)
        if not text.strip():
            return b"", 0
        audio, sample_rate = self.synthesize_float(text)
        if audio.size == 0:
            return b"", sample_rate
        from tts_backends import float_to_wav_bytes

        return float_to_wav_bytes(audio, sample_rate), sample_rate

    def record_until_silence(
        self,
        *,
        stop_event: threading.Event | None = None,
        on_chunk: Callable[[], None] | None = None,
    ) -> np.ndarray:
        """Record from mic until silence or stop_event (wake-word mode)."""
        settings = self.settings
        frames: list[np.ndarray] = []
        silence_started: float | None = None
        recording_started = time.monotonic()

        def callback(indata, _frames, _time, status) -> None:
            nonlocal silence_started
            if status:
                logger.warning("Audio status: %s", status)
            chunk = indata.copy().reshape(-1)
            frames.append(chunk)
            if on_chunk:
                on_chunk()
            rms = float(np.sqrt(np.mean(np.square(chunk))))
            if rms < 0.008:
                if silence_started is None:
                    silence_started = time.monotonic()
            else:
                silence_started = None

        with sd.InputStream(
            samplerate=settings.sample_rate,
            channels=settings.channels,
            dtype="float32",
            callback=callback,
            blocksize=int(settings.sample_rate * 0.1),
        ):
            while True:
                if stop_event and stop_event.is_set():
                    break
                if silence_started and (
                    time.monotonic() - silence_started >= settings.vad_silence_seconds
                ):
                    break
                if time.monotonic() - recording_started >= settings.max_record_seconds:
                    break
                time.sleep(0.05)

        if not frames:
            return np.array([], dtype=np.float32)
        return np.concatenate(frames)

    def record_while(self, is_active: Callable[[], bool]) -> np.ndarray:
        """Record while is_active() returns True (push-to-talk mode)."""
        settings = self.settings
        frames: list[np.ndarray] = []
        started = time.monotonic()

        def callback(indata, _frames, _time, status) -> None:
            if status:
                logger.warning("Audio status: %s", status)
            if is_active():
                frames.append(indata.copy().reshape(-1))

        with sd.InputStream(
            samplerate=settings.sample_rate,
            channels=settings.channels,
            dtype="float32",
            callback=callback,
            blocksize=int(settings.sample_rate * 0.05),
        ):
            while is_active():
                if time.monotonic() - started >= settings.max_record_seconds:
                    break
                time.sleep(0.02)

        if not frames:
            return np.array([], dtype=np.float32)
        return np.concatenate(frames)
