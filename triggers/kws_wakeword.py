"""Background KWS wake-phrase detection (sherpa-onnx)."""

from __future__ import annotations

import logging
import threading
from typing import Callable

import numpy as np
import sounddevice as sd

from config import Settings
from wake_kws import KwsWakeSpotter, build_kws_spotter

logger = logging.getLogger(__name__)


class KwsWakeListener:
    """Listen continuously for configured wake phrases via sherpa-onnx KWS."""

    def __init__(
        self,
        settings: Settings,
        *,
        sample_rate: int = 16_000,
        on_detected: Callable[[str], None] | None = None,
    ) -> None:
        self.sample_rate = sample_rate
        self.on_detected = on_detected
        self._spotter = build_kws_spotter(settings)
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._enabled = True

    def _run(self) -> None:
        from wake_kws import KWS_CHUNK_SAMPLES

        chunk_size = KWS_CHUNK_SAMPLES

        def audio_callback(indata, _frames, _time, status) -> None:
            if status or not self._enabled:
                return
            audio = indata.reshape(-1).astype(np.float32)
            phrase = self._spotter.accept_waveform(audio)
            if phrase:
                logger.info("KWS wake phrase detected: %s", phrase)
                self._enabled = False
                if self.on_detected:
                    threading.Thread(target=self.on_detected, args=(phrase,), daemon=True).start()
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
        self._thread = threading.Thread(target=self._run, name="kws-wakeword", daemon=True)
        self._thread.start()
        logger.info("KWS wake listener started")

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2)