"""Wyoming STT/TTS/handle services (testable, lazy-loaded)."""

from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

import numpy as np

from config import Settings
from markdown_speech import strip_markdown_for_speech
from wyoming_server.agent_handle import run_wyoming_handle
from wyoming_server.audio_utils import (
    TARGET_CHANNELS,
    TARGET_RATE,
    TARGET_WIDTH,
    audio_chunks_to_mono16k,
    float_to_pcm16_chunks,
)
from wyoming_server.config import SAHIR_ATTRIBUTION_NAME, WyomingSettings

if TYPE_CHECKING:
    from agent.loop import Agent
    from audio_pipeline import SpeechPipeline

logger = logging.getLogger(__name__)

TranscribeFn = Callable[[np.ndarray], str]
SynthesizeFn = Callable[[str], tuple[np.ndarray, int]]
HandleFn = Callable[[str], tuple[bool, str]]


@dataclass
class WyomingServices:
    """Shared STT/TTS/handle logic for the Wyoming TCP server."""

    app_settings: Settings
    wyoming_settings: WyomingSettings
    pipeline: "SpeechPipeline | None" = None
    agent: "Agent | None" = None
    embedder: Any | None = None
    orchestrator_client: Any | None = None
    transcribe_fn: TranscribeFn | None = None
    synthesize_fn: SynthesizeFn | None = None
    handle_fn: HandleFn | None = None
    _transcribe_lock: asyncio.Semaphore = field(default_factory=lambda: asyncio.Semaphore(1))

    def get_pipeline(self) -> "SpeechPipeline":
        if self.pipeline is None:
            from audio_pipeline import SpeechPipeline

            self.pipeline = SpeechPipeline(self.app_settings)
        return self.pipeline

    def get_agent(self) -> "Agent":
        if self.agent is None:
            from agent.config import AgentConfig
            from agent.factory import build_agent_stack

            from dataclasses import replace

            cfg = AgentConfig.from_env()
            env_db = os.getenv("AGENT_DB_PATH", "").strip()
            if env_db:
                cfg = replace(cfg, db_path=Path(env_db))
            self.agent = build_agent_stack(
                self.app_settings,
                cfg,
                embedder=self.embedder,
                orchestrator_client=self.orchestrator_client,
            ).agent
        return self.agent

    async def _run_sync(self, fn: Callable[..., object], *args: object) -> object:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, fn, *args)

    async def transcribe_audio(self, chunks: list) -> str:
        audio, seconds = audio_chunks_to_mono16k(chunks)
        if seconds > self.wyoming_settings.max_audio_seconds + 0.05:
            raise ValueError(f"Audio too long ({seconds:.1f}s > {self.wyoming_settings.max_audio_seconds}s)")
        if audio.size == 0:
            return ""

        async with self._transcribe_lock:
            if self.transcribe_fn is not None:
                return await self._run_sync(self.transcribe_fn, audio)  # type: ignore[return-value]
            pipeline = self.get_pipeline()
            return await self._run_sync(pipeline.transcribe_final, audio)  # type: ignore[return-value]

    async def synthesize_text(self, text: str) -> tuple[np.ndarray, int]:
        clean = strip_markdown_for_speech(text or "")
        if not clean.strip():
            return np.array([], dtype=np.float32), TARGET_RATE
        if self.synthesize_fn is not None:
            audio, sr = await self._run_sync(self.synthesize_fn, clean)  # type: ignore[misc]
            return np.asarray(audio, dtype=np.float32), int(sr)
        pipeline = self.get_pipeline()
        audio, sr = await self._run_sync(pipeline.synthesize_float, clean)  # type: ignore[misc]
        return np.asarray(audio, dtype=np.float32), int(sr)

    async def handle_transcript(self, text: str) -> tuple[bool, str]:
        if not self.wyoming_settings.handle_enabled:
            return False, ""
        if self.handle_fn is not None:
            return await self._run_sync(self.handle_fn, text)  # type: ignore[return-value]
        agent = self.get_agent()
        import functools

        fn = functools.partial(
            run_wyoming_handle,
            agent,
            text,
            project_name=self.wyoming_settings.project_name,
            conversation_title=self.wyoming_settings.conversation_title,
        )
        return await self._run_sync(fn)

    def pcm16_chunks_for_wyoming(self, audio: np.ndarray, sample_rate: int) -> list[bytes]:
        from wake_kws import resample_float_mono

        if sample_rate != TARGET_RATE:
            audio = resample_float_mono(audio.astype(np.float32), sample_rate, TARGET_RATE)
        return float_to_pcm16_chunks(audio, TARGET_RATE)

    def build_info(self):
        from wyoming.info import (
            AsrModel,
            AsrProgram,
            Attribution,
            HandleModel,
            HandleProgram,
            Info,
            TtsProgram,
            TtsVoice,
        )

        attr = Attribution(name=SAHIR_ATTRIBUTION_NAME, url="https://github.com/mhaiprojects/Sahir-AI")
        asr = AsrProgram(
            name="sahir-asr",
            attribution=attr,
            installed=True,
            description="Sahir faster-whisper (final model)",
            version=None,
            models=[
                AsrModel(
                    name=self.app_settings.whisper_final_model,
                    attribution=attr,
                    installed=True,
                    description="faster-whisper final",
                    version=None,
                    languages=["en"],
                )
            ],
            supports_transcript_streaming=False,
            requires_external_vad=False,
        )
        voices: list[TtsVoice] = [
            TtsVoice(
                name=self.app_settings.piper_voice,
                attribution=attr,
                installed=self.app_settings.piper_model_path.exists(),
                description="Piper",
                version=None,
                languages=["en"],
            )
        ]
        kokoro_dir = self.app_settings.kokoro_model_dir
        if kokoro_dir.is_dir() and any(kokoro_dir.glob("model*.onnx")):
            voices.append(
                TtsVoice(
                    name="kokoro",
                    attribution=attr,
                    installed=True,
                    description="Kokoro (sherpa-onnx)",
                    version=None,
                    languages=["en"],
                )
            )
        tts = TtsProgram(
            name="sahir-tts",
            attribution=attr,
            installed=True,
            description=f"Sahir TTS ({self.app_settings.tts_backend})",
            version=None,
            voices=voices,
            supports_synthesize_streaming=False,
        )
        handle: list[HandleProgram] = []
        if self.wyoming_settings.handle_enabled:
            handle.append(
                HandleProgram(
                    name="sahir-handle",
                    attribution=attr,
                    installed=True,
                    description="Sahir agent (Wyoming conversation)",
                    version=None,
                    models=[
                        HandleModel(
                            name="sahir-agent",
                            attribution=attr,
                            installed=True,
                            description="Orchestrator agent mode",
                            version=None,
                            languages=["en"],
                        )
                    ],
                    supports_handled_streaming=False,
                    supports_home_control=False,
                )
            )
        return Info(asr=[asr], tts=[tts], handle=handle)