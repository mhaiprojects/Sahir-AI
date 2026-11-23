"""Layer 1 — realtime voice loop over WebSocket with server-side Silero VAD and barge-in.

Protocol (``/ws/voice``):
  client → server
    binary frames            : PCM16 LE mono 16 kHz audio (any frame size; 20–100 ms recommended)
    {"type":"hello", "conversation_id":int|null, "mode":"agent|route|chat", "tts":bool}
    {"type":"text", "text":"..."}      : inject a typed utterance (skips STT)
    {"type":"flush"}                   : force end-of-utterance now
    {"type":"interrupt"}               : stop the current reply (manual barge-in)
  server → client (JSON unless noted)
    ready, speech_start, barge_in, status{state}, transcript{text}, reply{text,action,...},
    audio{seq,text,sample_rate,bytes} followed by ONE binary WAV frame, tts_done{interrupted}, error

Barge-in: if the VAD detects the user speaking while a reply is being generated or spoken, the server
cancels the in-flight reply (stops sending TTS sentences, drops a late LLM result) and emits
``barge_in`` so the client stops playback immediately. Replies are spoken sentence-by-sentence, so
the first audio arrives after one sentence of synthesis, not the whole reply.
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading
import time
from typing import Any, Callable

import numpy as np
from fastapi import FastAPI, WebSocket, WebSocketDisconnect

from agent.vad import Endpointer, SileroVAD
from markdown_speech import strip_markdown_for_speech
from tts_backends import split_sentences

logger = logging.getLogger(__name__)


class VoiceSession:
    def __init__(self, ws: WebSocket, *, get_settings: Callable, get_pipeline: Callable, get_agent: Callable,
                 get_command_store: Callable | None, request_lock: threading.Lock) -> None:
        self.ws = ws
        self.settings = get_settings()
        self.get_pipeline = get_pipeline
        self.get_agent = get_agent
        self.get_command_store = get_command_store
        self.request_lock = request_lock
        self.endpointer = Endpointer(threshold=self.settings.vad_threshold,
                                     end_silence_ms=self.settings.voice_end_silence_ms, vad=SileroVAD())
        self.conversation_id: int | None = None
        self.mode = "agent"
        self.tts = True
        self.generation = 0
        self.busy = False
        self.task: asyncio.Task | None = None
        self._send_lock = asyncio.Lock()

    async def send(self, payload: dict[str, Any]) -> None:
        async with self._send_lock:
            await self.ws.send_text(json.dumps(payload, ensure_ascii=False, default=str))

    async def send_audio(self, meta: dict[str, Any], wav: bytes) -> None:
        async with self._send_lock:  # keep JSON header + binary frame adjacent
            await self.ws.send_text(json.dumps(meta))
            await self.ws.send_bytes(wav)

    def cancel_current(self) -> bool:
        if self.busy:
            self.generation += 1
            return True
        return False

    async def on_speech_start(self) -> None:
        await self.send({"type": "speech_start"})
        if self.cancel_current():
            logger.info("Barge-in: user spoke during reply; cancelling generation")
            await self.send({"type": "barge_in"})

    def start_turn(self, *, audio: np.ndarray | None = None, text: str | None = None) -> None:
        self.generation += 1
        gen = self.generation
        self.task = asyncio.create_task(self.run_turn(gen, audio=audio, text=text))

    def _alive(self, gen: int) -> bool:
        return gen == self.generation

    async def run_turn(self, gen: int, *, audio: np.ndarray | None, text: str | None) -> None:
        self.busy = True
        t0 = time.monotonic()
        input_type = "text" if text is not None else "voice"
        try:
            pipeline = self.get_pipeline()
            if text is None:
                await self.send({"type": "status", "state": "transcribing"})
                text = (await asyncio.to_thread(pipeline.transcribe_final, audio)).strip()
                if not self._alive(gen):
                    return
                await self.send({"type": "transcript", "text": text, "discarded": not text,
                                 "audio_seconds": round(len(audio) / 16000, 2) if audio is not None else 0})
                if not text:
                    return
            await self.send({"type": "status", "state": "thinking"})

            def respond():
                from agent.dispatcher import dispatch_routed
                from agent.loop import AgentReply
                from tools.router import RouteResult

                if not self.request_lock.acquire(timeout=120):
                    raise RuntimeError("Assistant is busy")
                try:
                    agent = self.get_agent()
                    cmd_store = self.get_command_store() if self.get_command_store else None
                    raw, _mode, _decision = dispatch_routed(
                        agent,
                        text,
                        conversation_id=self.conversation_id,
                        input_type=input_type,
                        command_store=cmd_store,
                    )
                    if isinstance(raw, RouteResult):
                        return AgentReply(
                            reply=raw.reply,
                            action=raw.action,
                            action_details=raw.action_details,
                            routed=raw.routed,
                            conversation_id=self.conversation_id or 0,
                            user_message_id=0,
                            assistant_message_id=0,
                            sources={},
                        )
                    return raw
                finally:
                    self.request_lock.release()

            r = await asyncio.to_thread(respond)
            if r.action in {"stop_speech", "cancel_request"}:
                self.generation += 1
            elif r.action == "switch_chat" and r.conversation_id:
                self.conversation_id = r.conversation_id
            elif r.conversation_id:
                self.conversation_id = r.conversation_id
            if not self._alive(gen):
                logger.info("Dropping stale reply (barge-in)")
                return
            if r.action in {"stop_speech", "cancel_request", "pause_listening"}:
                await self.send({"type": "reply", "text": r.reply, "action": r.action,
                                 "conversation_id": r.conversation_id, "action_details": r.action_details,
                                 "sources": r.sources, "latency_ms": int((time.monotonic() - t0) * 1000)})
                await self.send({"type": "tts_done", "interrupted": True})
                return
            await self.send({"type": "reply", "text": r.reply, "action": r.action,
                             "conversation_id": r.conversation_id, "action_details": r.action_details,
                             "sources": r.sources, "latency_ms": int((time.monotonic() - t0) * 1000)})
            if not self.tts:
                return
            await self.send({"type": "status", "state": "speaking"})
            interrupted = False
            for seq, sentence in enumerate(split_sentences(strip_markdown_for_speech(r.reply))):
                if not self._alive(gen):
                    interrupted = True
                    break
                wav, sr = await asyncio.to_thread(pipeline.synthesize_wav, sentence)
                if not self._alive(gen):
                    interrupted = True
                    break
                if wav:
                    await self.send_audio({"type": "audio", "seq": seq, "text": sentence, "sample_rate": sr,
                                           "bytes": len(wav),
                                           "first_audio_ms": int((time.monotonic() - t0) * 1000) if seq == 0 else None},
                                          wav)
            await self.send({"type": "tts_done", "interrupted": interrupted})
        except WebSocketDisconnect:
            pass
        except Exception as exc:
            logger.exception("Voice turn failed")
            try:
                await self.send({"type": "error", "error": str(exc)})
            except Exception:
                pass
        finally:
            if self._alive(gen):
                self.busy = False
                try:
                    await self.send({"type": "status", "state": "listening"})
                except Exception:
                    pass

    async def handle_text(self, raw: str) -> None:
        try:
            msg = json.loads(raw)
        except json.JSONDecodeError:
            await self.send({"type": "error", "error": "invalid JSON"})
            return
        kind = msg.get("type")
        if kind == "hello":
            self.conversation_id = msg.get("conversation_id") or None
            self.mode = msg.get("mode") if msg.get("mode") in {"agent", "route", "chat"} else "agent"
            self.tts = bool(msg.get("tts", True))
            await self.send({"type": "ready", "conversation_id": self.conversation_id, "mode": self.mode,
                             "vad": self.endpointer.vad.backend, "sample_rate": 16000,
                             "tts_backend": self.get_pipeline().tts_backend})
        elif kind == "text" and str(msg.get("text", "")).strip():
            if self.cancel_current():
                await self.send({"type": "barge_in"})
            self.start_turn(text=str(msg["text"]).strip())
        elif kind == "flush":
            audio = self.endpointer.flush()
            if audio is not None:
                self.start_turn(audio=audio)
        elif kind == "interrupt":
            if self.cancel_current():
                await self.send({"type": "barge_in"})
        elif kind == "ping":
            await self.send({"type": "pong"})

    async def run(self) -> None:
        await self.send({"type": "ready", "vad": self.endpointer.vad.backend, "sample_rate": 16000})
        while True:
            msg = await self.ws.receive()
            if msg.get("type") == "websocket.disconnect":
                break
            if msg.get("bytes") is not None:
                pcm = np.frombuffer(msg["bytes"], dtype=np.int16).astype(np.float32) / 32768.0
                for event, audio in self.endpointer.feed(pcm):
                    if event == "speech_start":
                        await self.on_speech_start()
                    elif event == "utterance" and audio is not None:
                        self.start_turn(audio=audio)
            elif msg.get("text") is not None:
                await self.handle_text(msg["text"])
        self.generation += 1  # cancel anything in flight


def register_voice_ws(app: FastAPI, *, get_settings: Callable, get_pipeline: Callable, get_agent: Callable,
                      get_command_store: Callable | None = None, request_lock: threading.Lock) -> None:
    @app.websocket("/ws/voice")
    async def voice_ws(ws: WebSocket) -> None:
        await ws.accept()
        session = VoiceSession(ws, get_settings=get_settings, get_pipeline=get_pipeline, get_agent=get_agent,
                               get_command_store=get_command_store, request_lock=request_lock)
        try:
            await session.run()
        except WebSocketDisconnect:
            pass
        except Exception:
            logger.exception("Voice websocket error")
