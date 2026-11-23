"""Configuration loaded from environment / .env file."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent
load_dotenv(PROJECT_ROOT / ".env")


def _resolve_workspace(value: str) -> Path:
    """Use CURSOR_WORKSPACE when it exists, else the folder containing this project."""
    if value.strip():
        candidate = Path(value.strip()).expanduser()
        if candidate.is_dir():
            return candidate
    return PROJECT_ROOT.parent


def _split_csv(value: str, default: list[str]) -> list[str]:
    if not value.strip():
        return default
    return [item.strip() for item in value.split(",") if item.strip()]


@dataclass
class Settings:
    # Audio
    sample_rate: int = 16_000
    channels: int = 1
    vad_silence_seconds: float = 1.5
    max_record_seconds: float = 30.0

    # Local pipeline
    ollama_host: str = "http://localhost:11434"
    ollama_model: str = "llama3.1:8b"
    whisper_model: str = "base"
    whisper_live_model: str = "tiny"
    whisper_final_model: str = "base"
    whisper_device: str = "cpu"
    whisper_compute_type: str = "int8"
    piper_voice: str = "en_US-lessac-medium"
    piper_model_path: Path = field(
        default_factory=lambda: PROJECT_ROOT / "models/piper/en_US-lessac-medium.onnx"
    )

    # TTS backend: "piper" (default) or "kokoro" (sherpa-onnx, optional download)
    tts_backend: str = "piper"
    kokoro_model_dir: Path = field(
        default_factory=lambda: PROJECT_ROOT / "models/kokoro/kokoro-int8-multi-lang-v1_1"
    )
    kokoro_speaker_id: int = 0
    kokoro_speed: float = 1.0

    # Silero VAD speech gate (skip Whisper on noise) + realtime voice endpointing
    vad_gate_enabled: bool = True
    vad_threshold: float = 0.5
    vad_min_speech_seconds: float = 0.25
    voice_end_silence_ms: int = 700

    # Persist /api/chat and /api/transcribe turns into the agent's chat history
    agent_persist_web: bool = True

    # Activation
    wakeword_model: str = "hey_jarvis"
    wakeword_enabled: bool = True
    ptt_hotkey: str = "ctrl_r"

    # Cursor CLI
    cursor_workspace: Path = field(default_factory=lambda: PROJECT_ROOT.parent)
    cursor_model: str = "composer-2.5"
    cursor_api_key: str = ""
    cursor_agent_bin: str = "cursor-agent"
    cursor_timeout_seconds: int = 300
    cursor_use_plan_mode: bool = False
    # -p mode: --force allows shell tools when ~/.cursor/cli-config.json would block them.
    cursor_agent_force: bool = True
    # Empty = omit; otherwise "enabled" or "disabled" for cursor-agent --sandbox.
    cursor_agent_sandbox: str = ""
    cursor_agent_auto_review: bool = False

    # Web interface (LAN)
    web_host: str = "0.0.0.0"
    web_port: int = 8765
    web_db_path: Path = field(
        default_factory=lambda: PROJECT_ROOT / "data" / "request_logs.db"
    )
    web_ssl_cert: Path | None = None
    web_ssl_key: Path | None = None
    web_conversation_silence_seconds: float = 3.0
    web_live_transcribe_interval_seconds: float = 1.0
    log_dir: Path = field(default_factory=lambda: PROJECT_ROOT / "data" / "logs")

    # Security whitelists
    allowed_apps: list[str] = field(default_factory=list)
    allowed_commands: list[str] = field(default_factory=list)
    allowed_directories: list[Path] = field(default_factory=list)

    @classmethod
    def from_env(cls) -> "Settings":
        piper_voice = os.getenv("PIPER_VOICE", "en_US-lessac-medium")
        piper_path_env = os.getenv("PIPER_MODEL_PATH", "").strip()
        piper_path = (
            piper_path_env
            if piper_path_env
            else str(PROJECT_ROOT / f"models/piper/{piper_voice}.onnx")
        )
        return cls(
            sample_rate=int(os.getenv("SAMPLE_RATE", "16000")),
            ollama_host=os.getenv("OLLAMA_HOST", "http://localhost:11434"),
            ollama_model=os.getenv("OLLAMA_MODEL", "llama3.1:8b"),
            whisper_model=os.getenv("WHISPER_MODEL", "base"),
            whisper_live_model=os.getenv("WHISPER_LIVE_MODEL", "tiny"),
            whisper_final_model=os.getenv("WHISPER_FINAL_MODEL") or os.getenv(
                "WHISPER_MODEL", "base"
            ),
            whisper_device=os.getenv("WHISPER_DEVICE", "cpu"),
            whisper_compute_type=os.getenv("WHISPER_COMPUTE_TYPE", "int8"),
            piper_voice=piper_voice,
            piper_model_path=Path(piper_path),
            tts_backend=os.getenv("TTS_BACKEND", "piper").strip().lower() or "piper",
            kokoro_model_dir=Path(
                os.getenv("KOKORO_MODEL_DIR")
                or str(PROJECT_ROOT / "models/kokoro/kokoro-int8-multi-lang-v1_1")
            ),
            kokoro_speaker_id=int(os.getenv("KOKORO_SPEAKER_ID", "0")),
            kokoro_speed=float(os.getenv("KOKORO_SPEED", "1.0")),
            vad_gate_enabled=os.getenv("VAD_GATE_ENABLED", "true").lower()
            in ("1", "true", "yes"),
            vad_threshold=float(os.getenv("VAD_THRESHOLD", "0.5")),
            vad_min_speech_seconds=float(os.getenv("VAD_MIN_SPEECH_SECONDS", "0.25")),
            voice_end_silence_ms=int(os.getenv("VOICE_END_SILENCE_MS", "700")),
            agent_persist_web=os.getenv("AGENT_PERSIST_WEB", "true").lower()
            in ("1", "true", "yes"),
            wakeword_model=os.getenv("WAKEWORD_MODEL", "hey_jarvis"),
            wakeword_enabled=os.getenv("WAKEWORD_ENABLED", "true").lower()
            in ("1", "true", "yes"),
            ptt_hotkey=os.getenv("PTT_HOTKEY", "ctrl_r"),
            cursor_workspace=_resolve_workspace(os.getenv("CURSOR_WORKSPACE", "")),
            cursor_model=os.getenv("CURSOR_MODEL", "composer-2.5"),
            cursor_api_key=os.getenv("CURSOR_API_KEY", ""),
            cursor_agent_bin=os.getenv("CURSOR_AGENT_BIN", "cursor-agent"),
            cursor_timeout_seconds=int(os.getenv("CURSOR_TIMEOUT_SECONDS", "300")),
            cursor_agent_force=os.getenv("CURSOR_AGENT_FORCE", "true").lower()
            in ("1", "true", "yes"),
            cursor_agent_sandbox=os.getenv("CURSOR_AGENT_SANDBOX", "").strip().lower(),
            cursor_agent_auto_review=os.getenv("CURSOR_AGENT_AUTO_REVIEW", "false").lower()
            in ("1", "true", "yes"),
            web_host=os.getenv("WEB_HOST", "0.0.0.0"),
            web_port=int(os.getenv("WEB_PORT", "8765")),
            web_db_path=Path(
                os.getenv("WEB_DB_PATH")
                or str(PROJECT_ROOT / "data" / "request_logs.db")
            ),
            web_ssl_cert=(
                Path(cert) if (cert := os.getenv("WEB_SSL_CERT", "").strip()) else None
            ),
            web_ssl_key=(
                Path(key) if (key := os.getenv("WEB_SSL_KEY", "").strip()) else None
            ),
            web_conversation_silence_seconds=float(
                os.getenv("WEB_CONVERSATION_SILENCE_SECONDS", "3")
            ),
            web_live_transcribe_interval_seconds=float(
                os.getenv("WEB_LIVE_TRANSCRIBE_INTERVAL_SECONDS", "1")
            ),
            allowed_apps=_split_csv(
                os.getenv("ALLOWED_APPS", ""),
                ["firefox", "cursor", "code", "thunar", "xdg-open"],
            ),
            allowed_commands=_split_csv(
                os.getenv("ALLOWED_COMMANDS", ""),
                ["df", "free", "uptime", "ls", "pactl"],
            ),
            allowed_directories=[
                Path(p)
                for p in _split_csv(
                    os.getenv("ALLOWED_DIRECTORIES", ""),
                    [str(Path.home())],
                )
            ],
        )
