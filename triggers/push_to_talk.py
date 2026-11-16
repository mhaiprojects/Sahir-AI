"""Global push-to-talk hotkey listener."""

from __future__ import annotations

import logging
import threading
from typing import Callable

from pynput import keyboard

logger = logging.getLogger(__name__)

HOTKEY_MAP = {
    "ctrl_r": keyboard.Key.ctrl_r,
    "ctrl_l": keyboard.Key.ctrl_l,
    "f8": keyboard.Key.f8,
    "f9": keyboard.Key.f9,
}


class PushToTalk:
    """Hold a hotkey to record; release to finish the utterance."""

    def __init__(self, hotkey_name: str = "ctrl_r") -> None:
        self.hotkey = HOTKEY_MAP.get(hotkey_name.lower(), keyboard.Key.ctrl_r)
        self.active = False
        self.on_start: Callable[[], None] | None = None
        self.on_stop: Callable[[], None] | None = None
        self._lock = threading.Lock()
        self._listener: keyboard.Listener | None = None

    def _on_press(self, key) -> None:
        if key != self.hotkey:
            return
        with self._lock:
            if self.active:
                return
            self.active = True
        logger.debug("PTT key down")
        if self.on_start:
            self.on_start()

    def _on_release(self, key) -> None:
        if key != self.hotkey:
            return
        with self._lock:
            if not self.active:
                return
            self.active = False
        logger.debug("PTT key up")
        if self.on_stop:
            self.on_stop()

    def is_active(self) -> bool:
        with self._lock:
            return self.active

    def start(self) -> None:
        self._listener = keyboard.Listener(
            on_press=self._on_press,
            on_release=self._on_release,
        )
        self._listener.daemon = True
        self._listener.start()
        logger.info("Push-to-talk armed on %s", self.hotkey)

    def stop(self) -> None:
        if self._listener:
            self._listener.stop()
            self._listener = None
