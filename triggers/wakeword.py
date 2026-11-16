"""Background wake-word detection using openWakeWord."""

from __future__ import annotations

import logging
import threading
from typing import Callable

import numpy as np
import sounddevice as sd
from openwakeword.model import Model

logger = logging.getLogger(__name__)


class WakeWordListener:
    """Listen continuously for a wake word and invoke a callback."""

    def __init__(
        self,
        *,
        model_name: str,
        sample_rate: int = 16_000,
        threshold: float = 0.5,
        on_detected: Callable[[], None] | None = None,
    ) -> None:
        self.model_name = model_name
        self.sample_rate = sample_rate
        self.threshold = threshold
        self.on_detected = on_detected
        self._model: Model | None = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._enabled = True

    def _load_model(self) -> Model:
        if self._model is None:
            logger.info("Loading openWakeWord models (target=%s)", self.model_name)
            self._model = Model()
        return self._model

    def _run(self) -> None:
        model = self._load_model()
        chunk_size = 1280  # 80 ms at 16 kHz

        def audio_callback(indata, _frames, _time, status) -> None:
            if status or not self._enabled:
                return
            audio = (indata.copy().reshape(-1) * 32767).astype(np.int16)
            prediction = model.predict(audio)
            score = float(prediction.get(self.model_name, 0.0))
            if score >= self.threshold:
                logger.info("Wake word detected (score=%.2f)", score)
                self._enabled = False
                if self.on_detected:
                    on_detected = self.on_detected
                    threading.Thread(target=on_detected, daemon=True).start()
                threading.Timer(2.0, self._reenable).start()

        with sd.InputStream(
            samplerate=self.sample_rate,
            channels=1,
            dtype="float32",
            blocksize=chunk_size,
            callback=audio_callback,
        ):
            while not self._stop.is_set():
                self._stop.wait(0.2)

    def _reenable(self) -> None:
        self._enabled = True

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="wakeword", daemon=True)
        self._thread.start()
        logger.info("Wake word listener started")

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2)
