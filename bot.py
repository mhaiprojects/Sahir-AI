#!/usr/bin/env python3
"""Sahir: custom local voice assistant with push-to-talk, wake word, and Cursor CLI."""

from __future__ import annotations

import argparse
import logging
import shutil
import sys
import threading
import time

from config import Settings
from logging_setup import configure_logging
from tools.router import chat_text_only, route_utterance

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("voice-assistant")


def _init_logging(settings: Settings) -> None:
    log_path = configure_logging(settings.log_dir)
    logger.info("Logging to %s", log_path)


class VoiceAssistant:
    def __init__(self, settings: Settings) -> None:
        from audio_pipeline import SpeechPipeline
        from triggers.push_to_talk import PushToTalk
        from triggers.wakeword import WakeWordListener

        self.settings = settings
        self.pipeline = SpeechPipeline(settings)
        self.ptt = PushToTalk(settings.ptt_hotkey)
        self._busy = threading.Lock()
        self._ptt_thread: threading.Thread | None = None
        self._wake_listener: WakeWordListener | None = None

    def _handle_transcript(self, transcript: str, source: str) -> None:
        transcript = transcript.strip()
        if not transcript:
            self.pipeline.speak("I didn't catch that.")
            return
        logger.info("[%s] User said: %s", source, transcript)
        reply = route_utterance(
            transcript,
            self.settings,
            on_long_task=lambda: self.pipeline.speak("Working on that now."),
        )
        logger.info("Reply: %s", reply[:200])
        self.pipeline.speak(reply)

    def _process_audio(self, audio, source: str) -> None:
        if not self._busy.acquire(blocking=False):
            logger.warning("Busy; ignoring %s request", source)
            return
        try:
            transcript = self.pipeline.transcribe(audio)
            self._handle_transcript(transcript, source)
        except Exception:
            logger.exception("Failed to process %s audio", source)
            self.pipeline.speak("Something went wrong processing your request.")
        finally:
            self._busy.release()

    def _on_ptt_start(self) -> None:
        if self._busy.locked():
            return

        def record_and_process() -> None:
            audio = self.pipeline.record_while(self.ptt.is_active)
            self._process_audio(audio, "ptt")

        self._ptt_thread = threading.Thread(target=record_and_process, daemon=True)
        self._ptt_thread.start()

    def _on_wake_word(self) -> None:
        if self._busy.locked():
            return

        def record_and_process() -> None:
            try:
                self.pipeline.speak("Yes?")
            except Exception:
                logger.debug("Could not play wake ack", exc_info=True)
            audio = self.pipeline.record_until_silence()
            self._process_audio(audio, "wakeword")

        threading.Thread(target=record_and_process, daemon=True).start()

    def run(self) -> None:
        self._preflight()
        self.ptt.on_start = self._on_ptt_start
        self.ptt.start()

        if self.settings.wakeword_enabled:
            self._wake_listener = WakeWordListener(
                model_name=self.settings.wakeword_model,
                sample_rate=self.settings.sample_rate,
                on_detected=self._on_wake_word,
            )
            self._wake_listener.start()

        hotkey = self.settings.ptt_hotkey
        wake = self.settings.wakeword_model if self.settings.wakeword_enabled else "disabled"
        self.pipeline.speak(
            f"Sahir ready. Hold {hotkey} to talk, or say {wake}."
        )
        logger.info("Running. PTT=%s wake_word=%s", hotkey, wake)

        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            logger.info("Shutting down")
        finally:
            self.ptt.stop()
            if self._wake_listener:
                self._wake_listener.stop()

    def _preflight(self) -> None:
        if not shutil.which(self.settings.cursor_agent_bin):
            logger.warning("%s not found on PATH", self.settings.cursor_agent_bin)
        if not self.settings.piper_model_path.exists():
            raise FileNotFoundError(
                f"Piper model missing: {self.settings.piper_model_path}. "
                "Run scripts/setup_models.sh"
            )


def run_text_mode(
    settings: Settings,
    utterance: str | None,
    *,
    use_router: bool = False,
) -> int:
    """Text-only mode for testing without a microphone."""
    if utterance:
        text = utterance
    else:
        print("Text mode. Type a message (Ctrl+D to exit):")
        try:
            text = sys.stdin.readline().strip()
        except KeyboardInterrupt:
            return 0
        if not text:
            return 0

    if use_router:
        reply = route_utterance(text, settings)
    else:
        reply = chat_text_only(text, settings)
    print(reply)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Sahir: local voice assistant")
    parser.add_argument(
        "--text",
        nargs="?",
        const="",
        help="Text-only mode (optional inline message)",
    )
    parser.add_argument(
        "--route",
        action="store_true",
        help="Use full intent router in text mode",
    )
    parser.add_argument(
        "--web",
        action="store_true",
        help="Start web UI on LAN (WEB_HOST:WEB_PORT from .env)",
    )
    args = parser.parse_args()

    settings = Settings.from_env()
    _init_logging(settings)

    if args.web:
        from web_server import run_web_server

        run_web_server(settings)
        return 0

    if args.text is not None:
        return run_text_mode(
            settings,
            args.text or None,
            use_router=args.route,
        )

    assistant = VoiceAssistant(settings)
    assistant.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
