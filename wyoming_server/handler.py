"""Wyoming AsyncEventHandler implementation."""

from __future__ import annotations

import logging

from wyoming.asr import Transcribe, Transcript
from wyoming.audio import AudioChunk, AudioStart, AudioStop
from wyoming.error import Error
from wyoming.handle import Handled, NotHandled
from wyoming.info import Describe, Info
from wyoming.tts import Synthesize
from wyoming_server.audio_utils import TARGET_CHANNELS, TARGET_RATE, TARGET_WIDTH
from wyoming_server.services import WyomingServices

logger = logging.getLogger(__name__)


class SahirWyomingHandler:
    """Async Wyoming event handler (wrapped for wyoming.server.AsyncEventHandler)."""

    def __init__(self, services: WyomingServices, writer) -> None:
        self.services = services
        self._write = writer
        self._asr_chunks: list[AudioChunk] = []
        self._asr_active = False
        self._audio_seconds = 0.0

    async def write_event(self, event) -> None:
        await self._write(event)

    async def handle_event(self, event) -> bool:
        if Describe.is_type(event.type):
            info: Info = self.services.build_info()
            await self.write_event(info.event())
            return True

        if Transcribe.is_type(event.type):
            self._asr_active = True
            self._asr_chunks = []
            self._audio_seconds = 0.0
            return True

        if AudioStart.is_type(event.type) and self._asr_active:
            return True

        if AudioChunk.is_type(event.type) and self._asr_active:
            chunk = AudioChunk.from_event(event)
            self._asr_chunks.append(chunk)
            self._audio_seconds += float(chunk.seconds)
            if self._audio_seconds > self.services.wyoming_settings.max_audio_seconds + 0.05:
                await self.write_event(
                    Error(
                        text=f"Audio exceeds {self.services.wyoming_settings.max_audio_seconds:.0f}s limit",
                        code="audio-too-long",
                    ).event()
                )
                self._reset_asr()
                return False
            return True

        if AudioStop.is_type(event.type) and self._asr_active:
            try:
                text = await self.services.transcribe_audio(self._asr_chunks)
            except ValueError as exc:
                await self.write_event(Error(text=str(exc), code="audio-too-long").event())
                self._reset_asr()
                return False
            except Exception as exc:
                logger.exception("Wyoming transcribe failed")
                await self.write_event(Error(text=str(exc), code="transcribe-failed").event())
                self._reset_asr()
                return False
            await self.write_event(Transcript(text=text, language="en").event())
            self._reset_asr()
            return True

        if Synthesize.is_type(event.type):
            req = Synthesize.from_event(event)
            text = req.text or ""
            try:
                audio, sr = await self.services.synthesize_text(text)
            except Exception as exc:
                logger.exception("Wyoming synthesize failed")
                await self.write_event(Error(text=str(exc), code="synthesize-failed").event())
                return False
            chunks = self.services.pcm16_chunks_for_wyoming(audio, sr)
            await self.write_event(
                AudioStart(rate=TARGET_RATE, width=TARGET_WIDTH, channels=TARGET_CHANNELS).event()
            )
            for blob in chunks:
                if not blob:
                    continue
                await self.write_event(
                    AudioChunk(
                        rate=TARGET_RATE,
                        width=TARGET_WIDTH,
                        channels=TARGET_CHANNELS,
                        audio=blob,
                    ).event()
                )
            await self.write_event(AudioStop().event())
            return True

        if Transcript.is_type(event.type) and not self._asr_active:
            text = Transcript.from_event(event).text or ""
            if not self.services.wyoming_settings.handle_enabled:
                await self.write_event(
                    NotHandled(text="Sahir Wyoming handle is disabled. Set WYOMING_HANDLE_ENABLED=true.").event()
                )
                return True
            try:
                ok, reply = await self.services.handle_transcript(text)
            except Exception as exc:
                logger.exception("Wyoming handle failed")
                await self.write_event(Error(text=str(exc), code="handle-failed").event())
                return False
            if ok and reply:
                await self.write_event(Handled(text=reply).event())
            else:
                await self.write_event(NotHandled(text=reply or "Not handled.").event())
            return True

        return True

    def _reset_asr(self) -> None:
        self._asr_active = False
        self._asr_chunks = []
        self._audio_seconds = 0.0


def handler_factory(services: WyomingServices):
    from wyoming.event import Event
    from wyoming.server import AsyncEventHandler

    class _Handler(AsyncEventHandler):
        def __init__(self, reader, writer) -> None:
            super().__init__(reader, writer)
            self._inner = SahirWyomingHandler(services, self.write_event)

        async def handle_event(self, event: Event) -> bool:
            return await self._inner.handle_event(event)

    return _Handler