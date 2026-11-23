"""Web interface for Sahir, the local voice assistant (LAN accessible)."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import sqlite3
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import numpy as np
import uvicorn
from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, RedirectResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.middleware.base import BaseHTTPMiddleware

import activity
from audio_decode import PartialAudioError, decode_audio_bytes
from config import Settings
from logging_setup import configure_logging
from tools.cursor_cli import resolve_agent_bin
from tools.router import (
    RouteResult,
    chat_text_only_detailed,
    route_utterance_detailed,
    run_cursor_task_detailed,
)
from agent.dispatcher import agent_reply_as_route, dispatch_routed, dispatch_voice_or_text, legacy_route_tier0
from agent.factory import AgentStack, build_agent_stack
from agent.loop import AgentReply
from agent.vad import speech_seconds
from web.agent_api import create_agent_router
from web.models_api import create_models_router
from agent.models import ModelManager, get_manager, set_manager
from agent.router_model import start_router_service, stop_router_service
from web.auth import (
    ensure_web_auth_ready,
    get_web_auth,
    init_web_auth,
    register_auth_routes,
    WebAuthMiddleware,
)
from web.voice_ws import register_voice_ws
from web.commands import VALID_ACTIONS, VoiceCommandStore
from web.db import RequestLogStore

ACTIVITY_STREAM_MAX_S = 120
logger = logging.getLogger(__name__)


class ActivityPublishBody(BaseModel):
    kind: str
    text: str = ""
    source: str = "ui"
    data: dict[str, Any] = Field(default_factory=dict)

STATIC_DIR = Path(__file__).resolve().parent / "web" / "static"

_request_lock = threading.Lock()
_live_transcribe_lock = threading.Lock()
_pipeline: Any | None = None

_VOICE_UNAVAILABLE = HTTPException(
    status_code=503,
    detail="Voice stack disabled (WEB_VOICE_ENABLED=false). Text chat and agent API remain available.",
)
_settings: Settings | None = None
_log_store: RequestLogStore | None = None
_command_store: VoiceCommandStore | None = None
_agent_stack: AgentStack | None = None
_agent_stack_lock = threading.Lock()
_SERVER_STARTED_AT = int(time.time())


def _build_id() -> str:
    """Changes on server restart or when static assets are redeployed."""
    try:
        newest = max(
            int(path.stat().st_mtime)
            for path in STATIC_DIR.glob("*")
            if path.is_file()
        )
    except ValueError:
        newest = 0
    return f"{_SERVER_STARTED_AT}-{newest}"


class ChatMessage(BaseModel):
    role: str = Field(..., pattern="^(user|assistant)$")
    content: str = Field(..., min_length=1, max_length=4000)


class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=8000)
    route: bool = True
    history: list[ChatMessage] = Field(default_factory=list)
    force_cursor: bool = False
    conversation_id: int | None = None


class CursorRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=8000)
    history: list[ChatMessage] = Field(default_factory=list)
    plan_mode: bool = False


class ChatResponse(BaseModel):
    reply: str
    routed: bool
    action: str
    action_details: dict[str, Any]
    log_id: int
    conversation_id: int | None = None


class VoiceCommandCreate(BaseModel):
    keyword: str = Field(..., min_length=1, max_length=120)
    action: str = Field(..., min_length=1, max_length=64)


class VoiceCommandUpdate(BaseModel):
    keyword: str | None = Field(default=None, min_length=1, max_length=120)
    action: str | None = Field(default=None, min_length=1, max_length=64)
    enabled: bool | None = None


class CommandMatchRequest(BaseModel):
    text: str = Field(..., min_length=1, max_length=2000)


class MicPermissionMiddleware(BaseHTTPMiddleware):
    """Allow microphone access from embedded pages on LAN."""

    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        response.headers["Permissions-Policy"] = "microphone=*"
        return response


def _get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings.from_env()
    return _settings


def _get_model_manager() -> ModelManager:
    mgr = get_manager()
    if mgr is None:
        # Applies the persisted active model to Settings, then (auto_default) makes the smallest
        # installed chat model active unless the user picked one that is still installed.
        mgr = ModelManager(_get_settings(), auto_default=True)
        set_manager(mgr)
    return mgr


def _require_voice_stack() -> None:
    if not _get_settings().web_voice_enabled:
        raise _VOICE_UNAVAILABLE


def _get_pipeline() -> Any:
    global _pipeline
    _require_voice_stack()
    if _pipeline is None:
        from audio_pipeline import SpeechPipeline

        _pipeline = SpeechPipeline(_get_settings())
    return _pipeline


def _get_log_store() -> RequestLogStore:
    global _log_store
    if _log_store is None:
        _log_store = RequestLogStore(_get_settings().web_db_path)
    return _log_store


def _get_command_store() -> VoiceCommandStore:
    global _command_store
    if _command_store is None:
        _command_store = VoiceCommandStore(_get_settings().web_db_path)
    return _command_store


def _get_agent_stack() -> AgentStack:
    global _agent_stack
    if _agent_stack is None:
        with _agent_stack_lock:
            if _agent_stack is None:
                _agent_stack = build_agent_stack(_get_settings())
    return _agent_stack


def _agent_context(text: str) -> str | None:
    """Memory + RAG context block for the legacy /api/chat and /api/transcribe routes."""
    if not _get_settings().agent_persist_web:
        return None
    try:
        return _get_agent_stack().agent.retrieve(text).to_block() or None
    except Exception as exc:
        logger.warning("Agent context unavailable: %s", exc)
        return None


def _persist_turn(
    conversation_id: int | None,
    user_text: str,
    result: RouteResult,
    *,
    input_type: str,
    source: str,
) -> int | None:
    """Store a legacy-route exchange in the agent chat history; returns conversation id."""
    if not _get_settings().agent_persist_web or not user_text.strip():
        return conversation_id
    try:
        agent = _get_agent_stack().agent
        conv = agent.resolve_conversation(conversation_id, source=source, auto_session=True)
        agent.store.add_message(conv["id"], "user", user_text, {"input_type": input_type})
        agent._auto_title(conv, user_text)
        agent.store.add_message(
            conv["id"],
            "assistant",
            result.reply or "(no reply)",
            {"action": result.action, "action_details": result.action_details},
        )
        if result.action != "error":
            agent._schedule_memory_extraction(conv["id"])
        return conv["id"]
    except Exception as exc:
        logger.warning("Could not persist chat turn: %s", exc)
        return conversation_id


def _vad_has_speech(pcm: np.ndarray) -> tuple[bool, float]:
    settings = _get_settings()
    if not settings.vad_gate_enabled or pcm.size == 0:
        return True, -1.0
    try:
        secs = speech_seconds(pcm, threshold=settings.vad_threshold)
    except Exception as exc:
        logger.warning("VAD gate failed (allowing audio): %s", exc)
        return True, -1.0
    return secs >= settings.vad_min_speech_seconds, secs


def _client_ip(request: Request) -> str | None:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    if request.client:
        return request.client.host
    return None


def _history_payload(history: list[ChatMessage]) -> list[dict[str, str]]:
    return [{"role": msg.role, "content": msg.content.strip()} for msg in history]


def _parse_history_json(raw: str) -> list[dict[str, str]]:
    if not raw.strip():
        return []
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return []
    if not isinstance(payload, list):
        return []
    parsed: list[dict[str, str]] = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        role = str(item.get("role", "")).strip()
        content = str(item.get("content", "")).strip()
        if role in {"user", "assistant"} and content:
            parsed.append({"role": role, "content": content})
    return parsed


def _route_result_to_response(
    result: RouteResult, log_id: int, conversation_id: int | None = None
) -> ChatResponse:
    return ChatResponse(
        reply=result.reply,
        routed=result.routed,
        action=result.action,
        action_details=result.action_details,
        log_id=log_id,
        conversation_id=conversation_id,
    )


def _record_log(
    *,
    request: Request,
    input_type: str,
    user_message: str,
    result: RouteResult | None = None,
    ai_reply: str | None = None,
    routed: bool = False,
    action: str | None = None,
    action_details: dict[str, Any] | None = None,
    duration_ms: int | None = None,
    status: str = "success",
    error_message: str | None = None,
) -> int:
    store = _get_log_store()
    if result is not None:
        ai_reply = result.reply
        routed = result.routed
        action = result.action
        action_details = result.action_details
        if result.action == "error":
            status = "error"
            error_message = str(result.action_details.get("error", ""))

    return store.add(
        client_ip=_client_ip(request),
        input_type=input_type,
        user_message=user_message,
        ai_reply=ai_reply,
        routed=routed,
        action=action,
        action_details=action_details,
        duration_ms=duration_ms,
        status=status,
        error_message=error_message,
    )


def check_health(settings: Settings | None = None) -> dict[str, Any]:
    """Return component health for the web status panel."""
    settings = settings or _get_settings()
    health: dict[str, Any] = {
        "ollama_host": settings.ollama_host,
        "ollama_model": settings.ollama_model,
        "fallback_model": get_manager().fallback_model if get_manager() else None,
        "cursor_workspace": str(settings.cursor_workspace),
        "web_db": str(settings.web_db_path),
        "https_enabled": bool(settings.web_ssl_cert and settings.web_ssl_key),
        "ffmpeg_available": shutil.which("ffmpeg") is not None,
        "build_id": _build_id(),
        "server_started_at": _SERVER_STARTED_AT,
        "transcription": {
            "conversation_silence_seconds": settings.web_conversation_silence_seconds,
            "live_interval_seconds": settings.web_live_transcribe_interval_seconds,
            "whisper_live_model": settings.whisper_live_model,
            "whisper_final_model": settings.whisper_final_model,
        },
    }

    try:
        import json
        import urllib.request

        with urllib.request.urlopen(
            f"{settings.ollama_host.rstrip('/')}/api/tags", timeout=3
        ) as resp:
            data = json.loads(resp.read().decode())
            models = [m.get("name", "") for m in data.get("models", [])]
            health["ollama"] = {
                "ok": True,
                "models": models,
                "target_loaded": settings.ollama_model in models
                or any(settings.ollama_model in m for m in models),
            }
    except Exception as exc:
        health["ollama"] = {"ok": False, "error": str(exc)}

    cursor_bin = shutil.which(resolve_agent_bin(settings.cursor_agent_bin))
    health["cursor_cli"] = {
        "ok": cursor_bin is not None,
        "bin": settings.cursor_agent_bin,
        "path": cursor_bin,
    }

    health["voice_stack"] = {"enabled": settings.web_voice_enabled}
    piper_path = settings.piper_model_path
    if settings.web_voice_enabled:
        health["piper"] = {"ok": piper_path.exists(), "path": str(piper_path)}
        health["tts"] = {
            "backend": settings.tts_backend,
            "kokoro_model_present": settings.kokoro_model_dir.exists(),
        }
    else:
        health["piper"] = {"ok": False, "reason": "disabled"}
        health["tts"] = {"backend": settings.tts_backend, "ok": False, "reason": "disabled"}
    health["vad_gate"] = {
        "enabled": settings.vad_gate_enabled,
        "threshold": settings.vad_threshold,
        "min_speech_seconds": settings.vad_min_speech_seconds,
    }
    if _agent_stack is not None:
        health["agent"] = {"ok": True, **_agent_stack.agent.store.stats()}
    if settings.web_voice_enabled:
        health["whisper"] = {
            "ok": True,
            "model": settings.whisper_model,
            "live_model": settings.whisper_live_model,
            "final_model": settings.whisper_final_model,
            "device": settings.whisper_device,
        }
    else:
        health["whisper"] = {"ok": False, "reason": "disabled"}
    from agent.llm_factory import llm_status
    from stt_live import live_stt_status

    health["llm"] = llm_status(settings)

    tx = live_stt_status(settings)
    health["transcription"] = {
        "conversation_silence_seconds": settings.web_conversation_silence_seconds,
        "live_interval_seconds": settings.web_live_transcribe_interval_seconds,
        "whisper_live_model": settings.whisper_live_model,
        "whisper_final_model": settings.whisper_final_model,
        "live_stt_backend": tx["backend"],
        "moonshine_ready": tx["moonshine_ready"],
    }

    return health


def _decode_audio_bytes(data: bytes, content_type: str | None) -> tuple[np.ndarray, int]:
    return decode_audio_bytes(data, content_type)


def _resample(audio: np.ndarray, src_rate: int, dst_rate: int) -> np.ndarray:
    if src_rate == dst_rate or audio.size == 0:
        return audio
    duration = len(audio) / src_rate
    dst_len = int(duration * dst_rate)
    if dst_len <= 0:
        return np.array([], dtype=np.float32)
    src_x = np.linspace(0, 1, num=len(audio), endpoint=False)
    dst_x = np.linspace(0, 1, num=dst_len, endpoint=False)
    return np.interp(dst_x, src_x, audio).astype(np.float32)


def create_app() -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # Pin the router model first (background); chat model manager stays lazy until first use.
        start_router_service(_get_settings())
        from agent.router_t1 import warm_router_index_background

        warm_router_index_background(lambda: _get_agent_stack().agent.embedder)
        yield
        stop_router_service()

    app = FastAPI(title="Sahir", version="1.1.0", lifespan=lifespan)
    settings = _get_settings()
    auth = init_web_auth(settings)
    app.add_middleware(MicPermissionMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )
    if auth.auth_required:
        app.add_middleware(WebAuthMiddleware, auth=auth)
        register_auth_routes(app, auth, static_dir=STATIC_DIR)

    # Live routine / WIP panel: every command, CLI line, tool call and background step (activity.py).
    activity.install_log_bridge()

    @app.post("/api/activity/publish")
    def api_activity_publish(payload: ActivityPublishBody) -> dict[str, Any]:
        if payload.kind not in activity.CLIENT_PUBLISH_KINDS:
            raise HTTPException(400, f"kind must be one of: {', '.join(sorted(activity.CLIENT_PUBLISH_KINDS))}")
        ev = activity.publish(payload.kind, payload.text, source=payload.source, **payload.data)
        if ev is None:
            raise HTTPException(400, "event text or structured data required")
        return {"ok": True, "id": ev["id"]}

    @app.get("/api/activity")
    def api_activity(since: int = 0, limit: int = 300) -> dict[str, Any]:
        return {"events": activity.bus.history(since=since, limit=max(1, min(limit, 600)))}

    @app.get("/api/activity/stream")
    async def api_activity_stream(request: Request, since: int = 0) -> StreamingResponse:
        last = request.headers.get("last-event-id", "")
        if last.isdigit():
            since = int(last)
        q = activity.bus.subscribe()

        async def events():
            # Each connection lives at most ACTIVITY_STREAM_MAX_S; EventSource reconnects with
            # Last-Event-ID so nothing is lost, and a server restart never waits on an open stream.
            last_id = since
            ends_at = time.monotonic() + ACTIVITY_STREAM_MAX_S
            try:
                yield "retry: 2000\n\n"
                for ev in activity.bus.history(since=since, limit=300):
                    last_id = ev["id"]
                    yield f"id: {ev['id']}\ndata: {json.dumps(ev, ensure_ascii=False)}\n\n"
                while time.monotonic() < ends_at:
                    if await request.is_disconnected():
                        break
                    try:
                        ev = await asyncio.wait_for(q.get(), timeout=min(10.0, max(0.05, ends_at - time.monotonic())))
                    except asyncio.TimeoutError:
                        yield ": keep-alive\n\n"
                        continue
                    if ev["id"] <= last_id:
                        continue
                    last_id = ev["id"]
                    yield f"id: {ev['id']}\ndata: {json.dumps(ev, ensure_ascii=False)}\n\n"
            finally:
                activity.bus.unsubscribe(q)

        return StreamingResponse(events(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    @app.get("/api/health")
    def api_health(request: Request) -> dict[str, Any]:
        if auth.auth_required and (auth.misconfigured or not auth.is_authenticated(request)):
            return {
                "status": "ok",
                "auth_required": True,
                "auth_configured": auth.configured,
            }
        return check_health()

    @app.get("/api/config")
    def api_config() -> dict[str, Any]:
        settings = _get_settings()
        return {
            "conversation_silence_seconds": settings.web_conversation_silence_seconds,
            "live_transcribe_interval_seconds": settings.web_live_transcribe_interval_seconds,
            "whisper_live_model": settings.whisper_live_model,
            "whisper_final_model": settings.whisper_final_model,
            "web_kws_available": __import__("wake_kws").kws_model_ready(),
            "web_wake_phrases": __import__("wake_kws").parse_wake_phrases(settings.wake_phrases),
            "wakeword_enabled": settings.wakeword_enabled,
            "wakeword_engine": settings.wakeword_engine,
            "wakeword_model": settings.wakeword_model,
            "wake_phrases": settings.wake_phrases,
            "ptt_hotkey": settings.ptt_hotkey,
            "desktop_voice_only": True,
            "web_port": settings.web_port,
        }

    @app.get("/api/logs")
    def api_logs(limit: int = 100) -> dict[str, Any]:
        limit = max(1, min(limit, 500))
        store = _get_log_store()
        entries = store.list_recent(limit=limit)
        return {"logs": [store.to_dict(entry) for entry in entries]}

    @app.get("/api/commands")
    def api_commands_list() -> dict[str, Any]:
        store = _get_command_store()
        commands = store.list_all()
        return {
            "commands": [store.to_dict(cmd) for cmd in commands],
            "actions": sorted(VALID_ACTIONS),
        }

    @app.post("/api/commands")
    def api_commands_create(body: VoiceCommandCreate) -> dict[str, Any]:
        store = _get_command_store()
        try:
            command = store.add(body.keyword, body.action)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except sqlite3.IntegrityError as exc:
            raise HTTPException(status_code=409, detail="Keyword already exists") from exc
        return store.to_dict(command)

    @app.put("/api/commands/{command_id}")
    def api_commands_update(command_id: int, body: VoiceCommandUpdate) -> dict[str, Any]:
        store = _get_command_store()
        try:
            command = store.update(
                command_id,
                keyword=body.keyword,
                action=body.action,
                enabled=body.enabled,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except sqlite3.IntegrityError as exc:
            raise HTTPException(status_code=409, detail="Keyword already exists") from exc
        if command is None:
            raise HTTPException(status_code=404, detail="Command not found")
        return store.to_dict(command)

    @app.delete("/api/commands/{command_id}")
    def api_commands_delete(command_id: int) -> dict[str, bool]:
        store = _get_command_store()
        if not store.delete(command_id):
            raise HTTPException(status_code=404, detail="Command not found")
        return {"deleted": True}

    @app.post("/api/commands/match")
    def api_commands_match(body: CommandMatchRequest) -> dict[str, Any]:
        store = _get_command_store()
        matched = store.match(body.text)
        if matched is None:
            return {"matched": False}
        return {"matched": True, **store.to_dict(matched)}

    @app.post("/api/chat", response_model=ChatResponse)
    def api_chat(body: ChatRequest, request: Request) -> ChatResponse:
        settings = _get_settings()
        text = body.message.strip()
        if not text:
            raise HTTPException(status_code=400, detail="Message is empty")

        if not _request_lock.acquire(blocking=False):
            raise HTTPException(
                status_code=429,
                detail="Assistant is busy with another request. Try again shortly.",
            )

        started = time.monotonic()
        try:
            history = _history_payload(body.history)
            if body.force_cursor:
                result = run_cursor_task_detailed(
                    text,
                    settings,
                    history=history,
                )
            elif body.route:
                early = legacy_route_tier0(
                    _get_agent_stack().agent,
                    text,
                    conversation_id=body.conversation_id,
                    command_store=_get_command_store(),
                )
                if early is not None:
                    result = early
                else:
                    result = route_utterance_detailed(
                        text,
                        settings,
                        history=history,
                        context=_agent_context(text),
                    )
            else:
                result = chat_text_only_detailed(
                    text,
                    settings,
                    history=history,
                    context=_agent_context(text),
                )
            duration_ms = int((time.monotonic() - started) * 1000)
            log_id = _record_log(
                request=request,
                input_type="text",
                user_message=text,
                result=result,
                duration_ms=duration_ms,
            )
            conv_id = _persist_turn(
                body.conversation_id, text, result, input_type="text", source="web"
            )
            return _route_result_to_response(result, log_id, conv_id)
        except Exception as exc:
            duration_ms = int((time.monotonic() - started) * 1000)
            logger.exception("Text chat failed for message=%r", text[:120])
            _record_log(
                request=request,
                input_type="text",
                user_message=text,
                ai_reply=None,
                routed=body.route,
                action="error",
                action_details={"error": str(exc)},
                duration_ms=duration_ms,
                status="error",
                error_message=str(exc),
            )
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        finally:
            _request_lock.release()

    @app.post("/api/cursor", response_model=ChatResponse)
    def api_cursor(body: CursorRequest, request: Request) -> ChatResponse:
        settings = _get_settings()
        text = body.message.strip()
        if not text:
            raise HTTPException(status_code=400, detail="Message is empty")

        if not _request_lock.acquire(blocking=False):
            raise HTTPException(
                status_code=429,
                detail="Assistant is busy with another request. Try again shortly.",
            )

        started = time.monotonic()
        try:
            result = run_cursor_task_detailed(
                text,
                settings,
                history=_history_payload(body.history),
                plan_mode=body.plan_mode,
            )
            duration_ms = int((time.monotonic() - started) * 1000)
            log_id = _record_log(
                request=request,
                input_type="cursor",
                user_message=text,
                result=result,
                duration_ms=duration_ms,
            )
            return _route_result_to_response(result, log_id)
        except Exception as exc:
            duration_ms = int((time.monotonic() - started) * 1000)
            _record_log(
                request=request,
                input_type="cursor",
                user_message=text,
                ai_reply=None,
                routed=True,
                action="error",
                action_details={"error": str(exc)},
                duration_ms=duration_ms,
                status="error",
                error_message=str(exc),
            )
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        finally:
            _request_lock.release()

    @app.post("/api/transcribe/live")
    async def api_transcribe_live(
        audio: UploadFile = File(...),
    ) -> dict[str, str]:
        """Fast partial transcription while audio is still being captured."""
        settings = _get_settings()
        _require_voice_stack()
        data = await audio.read()
        if not data:
            raise HTTPException(status_code=400, detail="No audio uploaded")

        if not _live_transcribe_lock.acquire(blocking=False):
            return {"transcript": "", "phase": "live", "skipped": "busy"}

        try:
            pcm, sample_rate = _decode_audio_bytes(data, audio.content_type)
            pcm = _resample(pcm, sample_rate, settings.sample_rate)
            if pcm.size == 0:
                return {"transcript": "", "phase": "live"}
            has_speech, _secs = _vad_has_speech(pcm)
            if not has_speech:
                return {"transcript": "", "phase": "live", "skipped": "no_speech"}

            pipeline = _get_pipeline()
            transcript = pipeline.transcribe_live(pcm).strip()
            return {"transcript": transcript, "phase": "live"}
        except PartialAudioError as exc:
            logger.warning("Live transcribe skipped partial audio: %s", exc)
            return {"transcript": "", "phase": "live", "skipped": "partial_audio"}
        except ValueError as exc:
            if "Could not decode audio" in str(exc):
                logger.warning("Live transcribe skipped partial audio: %s", exc)
                return {"transcript": "", "phase": "live", "skipped": "partial_audio"}
            logger.exception("Live transcription failed")
            return {"transcript": "", "phase": "live", "error": str(exc)}
        except Exception as exc:
            logger.exception("Live transcription failed")
            return {"transcript": "", "phase": "live", "error": str(exc)}
        finally:
            _live_transcribe_lock.release()

    @app.post("/api/transcribe")
    async def api_transcribe(
        request: Request,
        audio: UploadFile = File(...),
        route: bool = True,
        force_cursor: bool = False,
        mode: str = "ptt",
        phase: str = "final",
        live_transcript: str = "",
        history: str = "",
        conversation_id: int | None = None,
    ) -> dict[str, Any]:
        settings = _get_settings()
        _require_voice_stack()
        data = await audio.read()
        if not data:
            raise HTTPException(status_code=400, detail="No audio uploaded")

        if mode == "always":
            raise HTTPException(
                status_code=400,
                detail="Hands-free uses /ws/voice. Hold-to-listen should use mode=ptt.",
            )

        if not _request_lock.acquire(blocking=False):
            raise HTTPException(status_code=429, detail="Assistant is busy.")

        started = time.monotonic()
        user_label = "[voice input]"
        try:
            try:
                pcm, sample_rate = _decode_audio_bytes(
                    data, audio.content_type
                )
            except Exception as exc:
                duration_ms = int((time.monotonic() - started) * 1000)
                partial = isinstance(exc, PartialAudioError)
                log_fn = logger.warning if partial else logger.exception
                log_fn(
                    "Audio decode failed (%s bytes, content_type=%s, mode=%s, phase=%s)",
                    len(data),
                    audio.content_type,
                    mode,
                    phase,
                )
                _record_log(
                    request=request,
                    input_type="voice",
                    user_message=user_label,
                    ai_reply=None,
                    routed=route,
                    action="transcribe",
                    action_details={"stage": "decode", "content_type": audio.content_type, "listening_mode": mode},
                    duration_ms=duration_ms,
                    status="error",
                    error_message=str(exc),
                )
                detail = (
                    "Recording was too short or incomplete. Please try again."
                    if partial
                    else str(exc)
                )
                raise HTTPException(status_code=400, detail=detail) from exc

            pcm = _resample(pcm, sample_rate, settings.sample_rate)
            pipeline = _get_pipeline()
            if phase == "final":
                transcript = pipeline.transcribe_final(pcm).strip()
            else:
                transcript = pipeline.transcribe(pcm).strip()
            user_label = transcript or user_label

            if not transcript:
                duration_ms = int((time.monotonic() - started) * 1000)
                reply = "I didn't catch that."
                log_id = _record_log(
                    request=request,
                    input_type="voice",
                    user_message=user_label,
                    ai_reply=reply,
                    routed=route,
                    action="transcribe",
                    action_details={"transcript": "", "content_type": audio.content_type, "listening_mode": mode},
                    duration_ms=duration_ms,
                    status="success",
                )
                return {
                    "transcript": "",
                    "reply": reply,
                    "action": "transcribe",
                    "action_details": {"transcript": ""},
                    "log_id": log_id,
                }

            hist = _parse_history_json(history)
            if route:
                raw, _loop_mode, _dec = dispatch_routed(
                    _get_agent_stack().agent,
                    transcript,
                    conversation_id=conversation_id,
                    input_type="voice",
                    command_store=_get_command_store(),
                    force_cursor=force_cursor,
                    history=hist,
                    extra_meta={"input_type": "voice", "listening_mode": mode},
                )
                if isinstance(raw, AgentReply):
                    conversation_id = raw.conversation_id
                    result = agent_reply_as_route(raw)
                else:
                    result = raw
            else:
                result = chat_text_only_detailed(
                    transcript,
                    settings,
                    history=hist,
                    context=_agent_context(transcript),
                )
            conv_id = _persist_turn(
                conversation_id, transcript, result, input_type="voice", source="voice-ui"
            )

            duration_ms = int((time.monotonic() - started) * 1000)
            details = dict(result.action_details)
            details["transcript"] = transcript
            details["live_transcript"] = live_transcript.strip()
            details["content_type"] = audio.content_type
            details["listening_mode"] = mode
            details["transcription_phase"] = phase
            details["corrected"] = bool(
                live_transcript.strip()
                and live_transcript.strip().lower() != transcript.lower()
            )
            log_id = _get_log_store().add(
                client_ip=_client_ip(request),
                input_type="voice",
                user_message=transcript,
                ai_reply=result.reply,
                routed=result.routed if route else False,
                action=result.action,
                action_details=details,
                duration_ms=duration_ms,
                status="success" if result.action != "error" else "error",
                error_message=(
                    str(details.get("error"))
                    if result.action == "error"
                    else None
                ),
            )
            return {
                "transcript": transcript,
                "live_transcript": live_transcript.strip(),
                "corrected": details["corrected"],
                "reply": result.reply,
                "action": result.action,
                "action_details": details,
                "log_id": log_id,
                "conversation_id": conv_id,
            }
        except HTTPException:
            raise
        except Exception as exc:
            duration_ms = int((time.monotonic() - started) * 1000)
            _record_log(
                request=request,
                input_type="voice",
                user_message=user_label,
                ai_reply=None,
                routed=route,
                action="error",
                action_details={"stage": "transcribe"},
                duration_ms=duration_ms,
                status="error",
                error_message=str(exc),
            )
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        finally:
            _request_lock.release()

    @app.post("/api/speak")
    def api_speak(body: ChatRequest) -> Response:
        text = body.message.strip()
        if not text:
            raise HTTPException(status_code=400, detail="Message is empty")

        pipeline = _get_pipeline()
        activity.publish("step", f"text-to-speech: {len(text)} characters", source="tts")
        wav, _sample_rate = pipeline.synthesize_wav(text)
        if not wav:
            raise HTTPException(status_code=400, detail="Could not synthesize speech")
        return Response(content=wav, media_type="audio/wav")

    app.include_router(
        create_agent_router(
            lambda: _get_agent_stack().agent,
            _request_lock,
            get_command_store=_get_command_store,
        )
    )
    app.include_router(create_models_router(_get_model_manager, _request_lock))
    register_voice_ws(
        app,
        get_settings=_get_settings,
        get_pipeline=_get_pipeline,
        get_agent=lambda: _get_agent_stack().agent,
        get_command_store=_get_command_store,
        request_lock=_request_lock,
    )

    @app.get("/api/agent/tasks")
    def api_agent_tasks(conversation_id: int | None = None, limit: int = 50) -> dict[str, Any]:
        limit = max(1, min(limit, 500))
        return {
            "tasks": _get_agent_stack().dispatcher.list_tasks(conversation_id, limit),
            "workers": _get_agent_stack().dispatcher.catalog(),
            "clickup_configured": _get_agent_stack().clickup.configured,
            "email_configured": _get_agent_stack().email.configured,
            "calendar_configured": _get_agent_stack().calendar.configured,
        }

    @app.get("/api/integrations/clickup/status")
    def clickup_status() -> dict[str, Any]:
        return _get_agent_stack().clickup.status_payload()

    @app.post("/api/integrations/clickup/verify")
    def clickup_verify() -> dict[str, Any]:
        client = _get_agent_stack().clickup
        if not client.configured:
            from agent.clickup import CONFIGURED_HINT

            return {"ok": False, "detail": CONFIGURED_HINT, **client.status_payload()}
        try:
            details = client.verify_connection()
            return {"ok": True, "details": details, **client.status_payload()}
        except Exception as exc:
            from agent.clickup import ClickUpError

            msg = str(exc) if isinstance(exc, ClickUpError) else str(exc)
            return {"ok": False, "detail": msg, **client.status_payload()}

    @app.get("/api/integrations/email/status")
    def email_status() -> dict[str, Any]:
        return _get_agent_stack().email.status_payload()

    @app.post("/api/integrations/email/verify")
    def email_verify() -> dict[str, Any]:
        svc = _get_agent_stack().email
        if not svc.configured:
            from agent.email_integration import CONFIGURED_HINT

            return {"ok": False, "detail": CONFIGURED_HINT, **svc.status_payload()}
        try:
            details = svc.verify()
            return {"ok": True, "details": details, **svc.status_payload()}
        except Exception as exc:
            from agent.email_integration import EmailError

            msg = str(exc) if isinstance(exc, EmailError) else str(exc)
            return {"ok": False, "detail": msg, **svc.status_payload()}

    @app.get("/api/integrations/calendar/status")
    def calendar_status() -> dict[str, Any]:
        return _get_agent_stack().calendar.status_payload()

    @app.post("/api/integrations/calendar/verify")
    def calendar_verify() -> dict[str, Any]:
        svc = _get_agent_stack().calendar
        if not svc.configured:
            from agent.calendar_integration import CONFIGURED_HINT

            return {"ok": False, "detail": CONFIGURED_HINT, **svc.status_payload()}
        try:
            details = svc.verify()
            return {"ok": True, "details": details, **svc.status_payload()}
        except Exception as exc:
            from agent.calendar_integration import CalendarError

            msg = str(exc) if isinstance(exc, CalendarError) else str(exc)
            return {"ok": False, "detail": msg, **svc.status_payload()}

    @app.get("/agent")
    def agent_page() -> RedirectResponse:
        return RedirectResponse(url="/?view=chat", status_code=302)

    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(
            STATIC_DIR / "index.html",
            headers={"Cache-Control": "no-cache"},
        )

    if STATIC_DIR.exists():
        app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    return app


def run_web_server(settings: Settings | None = None) -> None:
    settings = settings or Settings.from_env()
    ensure_web_auth_ready(settings)
    log_path = configure_logging(settings.log_dir)
    logger.info("Logging to %s", log_path)
    _get_log_store()
    _get_command_store()
    _get_model_manager()
    app = create_app()

    ssl_kwargs: dict[str, Any] = {}
    if settings.web_ssl_cert and settings.web_ssl_key:
        if not settings.web_ssl_cert.exists() or not settings.web_ssl_key.exists():
            raise FileNotFoundError(
                "WEB_SSL_CERT or WEB_SSL_KEY path missing. "
                "Run scripts/generate_web_cert.sh"
            )
        ssl_kwargs["ssl_certfile"] = str(settings.web_ssl_cert)
        ssl_kwargs["ssl_keyfile"] = str(settings.web_ssl_key)
        scheme = "https"
        try:
            from web.ssl_cert_check import check_certificate

            cn = os.environ.get("WEB_CERT_CN", "voice-assistant.local")
            ok, messages = check_certificate(settings.web_ssl_cert, cn=cn)
            if not ok:
                for msg in messages:
                    logger.warning("TLS certificate: %s", msg)
        except Exception as exc:
            logger.debug("TLS SAN check skipped: %s", exc)
    else:
        scheme = "http"

    logger.info(
        "Web UI at %s://%s:%s (LAN: use this machine's IP)",
        scheme,
        settings.web_host,
        settings.web_port,
    )
    if scheme == "http" and settings.web_host in ("0.0.0.0", "::"):
        logger.warning(
            "Microphone access from other LAN devices requires HTTPS. "
            "Run scripts/generate_web_cert.sh and set WEB_SSL_CERT/WEB_SSL_KEY."
        )

    uvicorn.run(
        app,
        host=settings.web_host,
        port=settings.web_port,
        log_level="info",
        # Don't let open streams (activity panel, live voice) hold a restart for long.
        timeout_graceful_shutdown=5,
        **ssl_kwargs,
    )


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    run_web_server()
