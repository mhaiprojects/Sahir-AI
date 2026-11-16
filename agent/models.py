"""Runtime chat-model management: list / select / load / eject Ollama models, plus the
fallback ("big model") policy used when a small model produces invalid tool/action JSON.

The active model is applied by mutating the shared ``Settings.ollama_model`` so every
caller (router, orchestrator, OllamaChat) picks it up without extra plumbing. The
``OLLAMA_MODEL`` env value stays the default; the runtime choice is persisted in
``data/model_state.json`` (gitignored with the rest of ``data/``).
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
import urllib.request
from dataclasses import replace
from pathlib import Path
from typing import Any, Callable

from config import Settings

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_STATE_PATH = PROJECT_ROOT / "data" / "model_state.json"
DEFAULT_FALLBACK = "llama3.1:8b"

# Embedding-only families never offered as chat models.
_EMBED_RE = re.compile(r"(embed|bge|minilm|e5-|nomic)", re.I)
# Families with a "thinking" mode that must be disabled for low-latency voice replies.
_THINKING_RE = re.compile(r"^(qwen3|qwq|deepseek-r1|magistral|gpt-oss)", re.I)
# User phrases that force the fallback (big) model for one turn.
ESCALATE_RE = re.compile(
    r"\b(think (harder|carefully|deeper|more)|use the (big|bigger|large|larger|smart|smarter) model|"
    r"big model|bigger model|smarter model)\b",
    re.I,
)

HttpFn = Callable[[str, str, dict[str, Any] | None, float], dict[str, Any]]


def is_thinking_model(name: str) -> bool:
    return bool(_THINKING_RE.match(name or ""))


def think_kwargs(model: str) -> dict[str, Any]:
    """Extra ollama.chat kwargs: disable Qwen3-style thinking for voice latency."""
    return {"think": False} if is_thinking_model(model) else {}


def wants_escalation(text: str) -> bool:
    return bool(ESCALATE_RE.search(text or ""))


def _default_http(method: str, url: str, payload: dict[str, Any] | None, timeout: float) -> dict[str, Any]:
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={"Content-Type": "application/json"} if data else {})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = resp.read().decode() or "{}"
    # /api/generate without stream=false may return NDJSON; take the last object.
    lines = [ln for ln in body.strip().splitlines() if ln.strip()]
    return json.loads(lines[-1]) if lines else {}


class ModelManager:
    def __init__(self, settings: Settings, *, state_path: Path | None = None,
                 fallback_model: str | None = None, http: HttpFn | None = None) -> None:
        self.settings = settings
        self.default_model = settings.ollama_model
        self.fallback_model = fallback_model or os.getenv("FALLBACK_MODEL", DEFAULT_FALLBACK)
        self.state_path = Path(state_path or os.getenv("MODEL_STATE_PATH") or DEFAULT_STATE_PATH)
        self.keep_alive = os.getenv("MODEL_KEEP_ALIVE", "30m")
        self._http = http or _default_http
        self._lock = threading.Lock()
        self.auto_fallback = True
        self.force_fallback = False
        self._load_state()

    # ------------------------------------------------------------ state
    @property
    def active_model(self) -> str:
        return self.settings.ollama_model

    def _load_state(self) -> None:
        try:
            state = json.loads(self.state_path.read_text())
        except FileNotFoundError:
            return
        except Exception as exc:
            logger.warning("Ignoring unreadable model state %s: %s", self.state_path, exc)
            return
        if state.get("active_model"):
            self.settings.ollama_model = str(state["active_model"])
        self.auto_fallback = bool(state.get("auto_fallback", True))
        self.force_fallback = bool(state.get("force_fallback", False))
        if state.get("fallback_model") and not os.getenv("FALLBACK_MODEL"):
            self.fallback_model = str(state["fallback_model"])

    def _save_state(self) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.state_path.with_suffix(".tmp")
        tmp.write_text(json.dumps({
            "active_model": self.active_model, "fallback_model": self.fallback_model,
            "auto_fallback": self.auto_fallback, "force_fallback": self.force_fallback,
            "updated_at": int(time.time()),
        }, indent=2))
        tmp.replace(self.state_path)

    # ------------------------------------------------------------ ollama
    def _url(self, path: str) -> str:
        return f"{self.settings.ollama_host.rstrip('/')}{path}"

    def installed(self) -> list[dict[str, Any]]:
        return list(self._http("GET", self._url("/api/tags"), None, 5).get("models", []))

    def running(self) -> list[dict[str, Any]]:
        return list(self._http("GET", self._url("/api/ps"), None, 5).get("models", []))

    def chat_model_names(self) -> list[str]:
        return [m.get("name", "") for m in self.installed() if not _EMBED_RE.search(m.get("name", ""))]

    def status(self) -> dict[str, Any]:
        installed = self.installed()
        running = {m.get("name"): m for m in self.running()}
        models = []
        for m in installed:
            name = m.get("name", "")
            if _EMBED_RE.search(name):
                continue
            details = m.get("details") or {}
            r = running.get(name)
            loaded = None
            if r:
                size = int(r.get("size") or 0)
                vram = int(r.get("size_vram") or 0)
                loaded = {"size": size, "size_vram": vram, "size_ram": max(size - vram, 0),
                          "gpu_percent": round(100 * vram / size) if size else 0,
                          "expires_at": r.get("expires_at"), "context_length": r.get("context_length")}
            models.append({
                "name": name, "size": int(m.get("size") or 0),
                "parameter_size": details.get("parameter_size"),
                "quantization": details.get("quantization_level"), "family": details.get("family"),
                "thinking": is_thinking_model(name),
                "active": name == self.active_model, "fallback": name == self.fallback_model,
                "default": name == self.default_model, "loaded": loaded,
            })
        models.sort(key=lambda x: x["size"])
        other_loaded = [
            {"name": n, "size": int(r.get("size") or 0), "size_vram": int(r.get("size_vram") or 0)}
            for n, r in running.items() if _EMBED_RE.search(n or "")
        ]
        return {"active_model": self.active_model, "fallback_model": self.fallback_model,
                "default_model": self.default_model, "auto_fallback": self.auto_fallback,
                "force_fallback": self.force_fallback, "keep_alive": self.keep_alive,
                "models": models, "other_loaded": other_loaded}

    def _require_installed(self, name: str) -> None:
        if name not in self.chat_model_names():
            raise ValueError(f"Model {name!r} is not an installed chat model")

    def select(self, name: str) -> dict[str, Any]:
        self._require_installed(name)
        with self._lock:
            self.settings.ollama_model = name
            self._save_state()
        logger.info("Active chat model -> %s", name)
        return {"active_model": name}

    def set_options(self, *, auto_fallback: bool | None = None, force_fallback: bool | None = None,
                    fallback_model: str | None = None) -> dict[str, Any]:
        if fallback_model:
            self._require_installed(fallback_model)
        with self._lock:
            if auto_fallback is not None:
                self.auto_fallback = bool(auto_fallback)
            if force_fallback is not None:
                self.force_fallback = bool(force_fallback)
            if fallback_model:
                self.fallback_model = fallback_model
            self._save_state()
        return {"auto_fallback": self.auto_fallback, "force_fallback": self.force_fallback,
                "fallback_model": self.fallback_model}

    def load(self, name: str, keep_alive: str | None = None) -> dict[str, Any]:
        self._require_installed(name)
        t0 = time.monotonic()
        resp = self._http("POST", self._url("/api/generate"),
                          {"model": name, "prompt": "", "stream": False, "keep_alive": keep_alive or self.keep_alive},
                          300)
        return {"model": name, "loaded": True, "seconds": round(time.monotonic() - t0, 2),
                "load_duration_ms": round((resp.get("load_duration") or 0) / 1e6)}

    def eject(self, name: str) -> dict[str, Any]:
        self._http("POST", self._url("/api/generate"), {"model": name, "prompt": "", "stream": False,
                                                        "keep_alive": 0}, 60)
        return {"model": name, "loaded": False}

    # ------------------------------------------------------------ fallback
    def settings_for(self, model: str) -> Settings:
        return replace(self.settings, ollama_model=model)

    def initial_model(self, text: str) -> tuple[str, str | None]:
        """Model for this turn and the reason when the fallback is used up front."""
        if self.force_fallback:
            return self.fallback_model, "forced"
        if wants_escalation(text):
            return self.fallback_model, "user_request"
        return self.active_model, None

    def can_escalate(self, model: str) -> bool:
        return self.auto_fallback and bool(self.fallback_model) and model != self.fallback_model


_manager: ModelManager | None = None


def get_manager() -> ModelManager | None:
    return _manager


def set_manager(manager: ModelManager | None) -> None:
    global _manager
    _manager = manager


def tool_calls_invalid(message: dict[str, Any], tools: list[dict[str, Any]]) -> str | None:
    """Return a reason when the model's tool/action output is malformed, else None.

    Checks tool name, JSON-decodable arguments, required arguments and enum values against
    the tool schema. Plain text content (no tool call) is valid unless it looks like a tool
    call the model wrote into the content instead of using the tool-call channel.
    """
    schema = {t["function"]["name"]: t["function"].get("parameters", {}) for t in tools}
    calls = message.get("tool_calls") or []
    if not calls:
        content = (message.get("content") or "").strip()
        if not content:
            return "empty_response"
        if content[:1] in "{[" or "<tool_call>" in content or re.search(r'"(name|function)"\s*:', content[:300]):
            return "tool_call_in_content"
        return None
    for c in calls:
        fn = c.get("function", {}) if isinstance(c, dict) else c.function.model_dump()
        name = fn.get("name", "")
        if name not in schema:
            return f"unknown_tool:{name}"
        args = fn.get("arguments") or {}
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except json.JSONDecodeError:
                return f"bad_json:{name}"
        if not isinstance(args, dict):
            return f"bad_args:{name}"
        params = schema[name]
        for key in params.get("required", []):
            if args.get(key) in (None, ""):
                return f"missing_arg:{name}.{key}"
        for key, spec in (params.get("properties") or {}).items():
            enum = spec.get("enum") if isinstance(spec, dict) else None
            if enum and key in args and args[key] not in enum:
                return f"bad_enum:{name}.{key}"
    return None
