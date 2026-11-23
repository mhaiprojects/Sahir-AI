"""Layer 1 — realtime voice loop over WebSocket with server-side Silero VAD and barge-in.

Protocol (``/ws/voice``):
  client → server
    binary frames            : PCM16 LE mono 16 kHz audio (any frame size; 20–100 ms recommended)
    {"type":"hello", "conversation_id":int|null, "mode":"agent|route|chat", "tts":bool}
    {"type":"text", "text":"..."}      : inject text (typed router rules)
    {"type":"text", "text":"...", "as_voice":true} : same routing as STT final (Tier 0–2)
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

from agent.chat_router import build_route_context
from agent.dispatcher import execute_route_decision, plan_route
from agent.router_partials import VoiceRouteState, prefix_meaning_changed
from agent.speculative import (
    SpeculativeDraft,
    can_reuse_draft,
    commit_chat_draft,
    run_speculative,
)
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
        self.route_state = VoiceRouteState()
        self._partial_task: asyncio.Task | None = None
        self._in_speech = False
        self._speculative_gen = 0

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
        self._in_speech = True
        self.route_state.stabilizer.reset()
        self.route_state.speculative_draft = None
        self.route_state.speculative_partial = ""
        self._partial_task = asyncio.create_task(self._partial_router_loop())
        if self.cancel_current():
            logger.info("Barge-in: user spoke during reply; cancelling generation")
            await self.send({"type": "barge_in"})

    def start_turn(
        self,
        *,
        audio: np.ndarray | None = None,
        text: str | None = None,
        as_voice: bool = False,
    ) -> None:
        self.generation += 1
        gen = self.generation
        self.task = asyncio.create_task(self.run_turn(gen, audio=audio, text=text, as_voice=as_voice))

    def _alive(self, gen: int) -> bool:
        return gen == self.generation

    def _cancel_speculative_workers(self) -> int:
        """Invalidate in-flight speculative threads (no lock). Returns generation captured for draft reuse."""
        captured = self._speculative_gen
        self._speculative_gen += 1
        return captured

    def _start_speculative(self, text: str) -> None:
        if text == self.route_state.speculative_partial:
            return
        self.route_state.speculative_partial = text
        self._speculative_gen += 1
        gen = self._speculative_gen
        conv_id = self.conversation_id
        agent = self.get_agent()
        slot: list[SpeculativeDraft | None] = [None]
        gen_box = [gen]

        def still_valid() -> bool:
            return self._speculative_gen == gen_box[0]

        def on_draft(d: SpeculativeDraft) -> None:
            if still_valid():
                slot[0] = d
                self.route_state.speculative_draft = d

        def work() -> None:
            run_speculative(
                agent,
                partial=text,
                conversation_id=conv_id,
                generation=gen,
                still_valid=still_valid,
                on_draft=on_draft,
            )

        threading.Thread(target=work, name=f"voice-spec-{gen}", daemon=True).start()

    async def _partial_router_loop(self) -> None:
        pipeline = self.get_pipeline()
        cmd_store = self.get_command_store() if self.get_command_store else None
        last_caption = ""
        try:
            while self._in_speech:
                await asyncio.sleep(0.35)
                if not self._in_speech:
                    break
                audio = self.endpointer.speech_snapshot()
                if audio is None or len(audio) < int(16000 * 0.35):
                    continue
                text = (await asyncio.to_thread(pipeline.transcribe_live, audio)).strip()
                if not text:
                    continue
                if text != last_caption:
                    last_caption = text
                    await self.send({"type": "transcript_partial", "text": text})
                stable = self.route_state.stabilizer.observe(text)
                if not stable:
                    continue

                def cache_route() -> None:
                    from agent.chat_router import try_tier0_voice_control

                    if try_tier0_voice_control(stable):
                        return
                    agent = self.get_agent()
                    decision, _ctx = plan_route(
                        agent,
                        stable,
                        conversation_id=self.conversation_id,
                        input_type="voice",
                        command_store=cmd_store,
                        legacy_route=False,
                        route_state=self.route_state,
                    )
                    self.route_state.stabilizer.cached_decision = decision

                await asyncio.to_thread(cache_route)
                dec = self.route_state.stabilizer.cached_decision
                if dec and dec.label == "C" and dec.payload.get("sticky", True):
                    self._start_speculative(stable)
        except asyncio.CancelledError:
            pass

    async def run_turn(
        self,
        gen: int,
        *,
        audio: np.ndarray | None,
        text: str | None,
        as_voice: bool = False,
    ) -> None:
        self.busy = True
        t0 = time.monotonic()
        if text is not None:
            from agent.chat_router import normalize_voice_control_stt

            text = normalize_voice_control_stt(text.strip())
            input_type = "voice" if as_voice else "text"
        else:
            input_type = "voice"
        try:
            pipeline = self.get_pipeline()
            self._in_speech = False
            if self._partial_task:
                self._partial_task.cancel()
                self._partial_task = None
            if text is None:
                await self.send({"type": "status", "state": "transcribing"})
                self.route_state.speech_end_mono = time.monotonic()
                from agent.chat_router import normalize_voice_control_stt

                text = normalize_voice_control_stt(
                    (await asyncio.to_thread(pipeline.transcribe_final, audio)).strip()
                )
                if not self._alive(gen):
                    return
                await self.send({"type": "transcript", "text": text, "discarded": not text,
                                 "audio_seconds": round(len(audio) / 16000, 2) if audio is not None else 0})
                if not text:
                    return
            await self.send({"type": "status", "state": "thinking"})

            def respond():
                from agent.loop import AgentReply
                from tools.router import RouteResult

                draft_snapshot = self.route_state.speculative_draft
                draft_gen = self._cancel_speculative_workers()

                agent = self.get_agent()
                cmd_store = self.get_command_store() if self.get_command_store else None
                from agent.chat_router import try_tier0_voice_control

                voice_ctl = try_tier0_voice_control(text)

                def _finish(decision, ctx, *, draft_ok: bool) -> tuple:
                    self.route_state.route_final_mono = time.monotonic()
                    if draft_ok and can_reuse_draft(
                        draft_snapshot,
                        generation_at_capture=draft_gen,
                        decision=decision,
                        final_text=text,
                        conversation_id=self.conversation_id,
                    ):
                        raw = commit_chat_draft(
                            agent,
                            conversation_id=decision.target_chat_id or self.conversation_id,
                            user_text=text,
                            reply=draft_snapshot.reply,
                        )
                    else:
                        raw, _mode = execute_route_decision(
                            agent,
                            text,
                            decision,
                            ctx,
                            input_type=input_type,
                            extra_meta={"input_type": input_type, "route_label": decision.label},
                        )
                        if isinstance(raw, RouteResult):
                            raw = AgentReply(
                                reply=raw.reply,
                                action=raw.action,
                                action_details=raw.action_details,
                                routed=raw.routed,
                                conversation_id=self.conversation_id or 0,
                                user_message_id=0,
                                assistant_message_id=0,
                                sources={},
                            )
                    self.route_state.speculative_draft = None
                    self.route_state.last_decision = decision
                    return raw, decision

                if voice_ctl is not None:
                    self.route_state.stabilizer.cached_decision = None
                    self.route_state.speculative_draft = None
                    ctx = build_route_context(agent.store, self.conversation_id)
                    ctx.input_type = input_type
                    ctx.command_store = cmd_store
                    ctx.route_state = self.route_state
                    return _finish(voice_ctl, ctx, draft_ok=False)

                from agent.router_partials import prefix_meaning_changed

                if not self.request_lock.acquire(timeout=120):
                    raise RuntimeError("Assistant is busy")
                try:
                    cached = self.route_state.stabilizer.cached_decision
                    max_tier = 2
                    if cached and not prefix_meaning_changed(cached.utterance, text):
                        max_tier = 1
                    decision, ctx = plan_route(
                        agent,
                        text,
                        conversation_id=self.conversation_id,
                        input_type=input_type,
                        command_store=cmd_store,
                        legacy_route=False,
                        route_state=self.route_state,
                        cached_decision=cached,
                        max_tier=max_tier,
                    )
                    return _finish(decision, ctx, draft_ok=True)
                finally:
                    self.request_lock.release()

            outcome = await asyncio.to_thread(respond)
            r, route_decision = outcome
            if r.action == "switch_chat" and r.conversation_id:
                self.conversation_id = r.conversation_id
            elif r.conversation_id:
                self.conversation_id = r.conversation_id
            if not self._alive(gen):
                logger.info("Dropping stale reply (barge-in)")
                return
            if r.action in {"stop_speech", "cancel_request", "pause_listening"}:
                route_ms = (
                    int((self.route_state.route_final_mono - t0) * 1000)
                    if self.route_state.route_final_mono
                    else None
                )
                await self.send(
                    {
                        "type": "reply",
                        "text": r.reply,
                        "action": r.action,
                        "conversation_id": r.conversation_id,
                        "action_details": r.action_details,
                        "sources": r.sources,
                        "latency_ms": int((time.monotonic() - t0) * 1000),
                        "route_tier": route_decision.tier if route_decision else 0,
                        "route_ms": route_ms,
                        "route_command": route_decision.command if route_decision else None,
                    }
                )
                await self.send({"type": "tts_done", "interrupted": True})
                self.generation += 1
                return
            route_ms = (
                int((self.route_state.route_final_mono - t0) * 1000)
                if self.route_state.route_final_mono
                else None
            )
            timing = (route_decision.timing if route_decision else {}) or {}
            e2e_ms = None
            if self.route_state.speech_end_mono and self.route_state.route_final_mono:
                e2e_ms = int((self.route_state.route_final_mono - self.route_state.speech_end_mono) * 1000)
            await self.send({"type": "reply", "text": r.reply, "action": r.action,
                             "conversation_id": r.conversation_id, "action_details": r.action_details,
                             "sources": r.sources, "latency_ms": int((time.monotonic() - t0) * 1000),
                             "route_tier": route_decision.tier if route_decision else None,
                             "route_timing": timing, "route_final_ms": e2e_ms, "route_ms": route_ms})
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
            as_voice = bool(msg.get("as_voice"))
            self.start_turn(text=str(msg["text"]).strip(), as_voice=as_voice)
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
        from web.auth import get_web_auth

        web_auth = get_web_auth()
        if web_auth and web_auth.auth_required and not web_auth.websocket_authenticated(ws):
            reason = "Auth not configured" if web_auth.misconfigured else "Unauthorized"
            await ws.close(code=4401, reason=reason)
            return
        await ws.accept()
        session = VoiceSession(ws, get_settings=get_settings, get_pipeline=get_pipeline, get_agent=get_agent,
                               get_command_store=get_command_store, request_lock=request_lock)
        try:
            await session.run()
        except WebSocketDisconnect:
            pass
        except Exception:
            logger.exception("Voice websocket error")