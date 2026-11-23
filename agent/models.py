"""Runtime chat-model management: list / select / load / eject Ollama models, plus the
fallback ("big model") policy used when a small model produces invalid tool/action JSON.

The active model is applied by mutating the shared ``Settings.ollama_model`` so every
caller (router, orchestrator, OllamaChat) picks it up without extra plumbing. The
``OLLAMA_MODEL`` env value stays the default; the runtime choice is persisted in
``data/model_state.json`` (gitignored with the rest of ``data/``).

Default model (``auto_default=True``, used by the web server): when the app starts and
whenever the model list is read, the best auto pick becomes active (VRAM-fitting 1–4B
candidates, ranked by benchmark ``effective_correct_pct`` when ``data/bench/latest.json``
exists, else size). The quality ``FALLBACK_MODEL`` (e.g. llama3.1:8b) is never auto-selected.
User picks (``active_source == "user"``) are kept while still installed.
Legacy state files without ``active_source`` count as a user pick when their model
differs from the ``OLLAMA_MODEL`` default.
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

from activity import publish
from agent.gpu_config import get_gpu_config
from agent.model_selection import choose_auto_default, load_bench_scores
from agent.vram_budget import budget_summary, model_fits_vram, vram_warning_for_model
from agent.ollama_runtime import chat_options
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
                 fallback_model: str | None = None, http: HttpFn | None = None,
                 auto_default: bool = False) -> None:
        self.settings = settings
        self.default_model = settings.ollama_model
        self.fallback_model = fallback_model or os.getenv("FALLBACK_MODEL", DEFAULT_FALLBACK)
        self.state_path = Path(state_path or os.getenv("MODEL_STATE_PATH") or DEFAULT_STATE_PATH)
        self.keep_alive = os.getenv("MODEL_KEEP_ALIVE", "30m")
        self._http = http or _default_http
        self._lock = threading.Lock()
        self.auto_fallback = True
        self.force_fallback = False
        self.auto_default = auto_default
        # "user" = picked in the UI/API, "auto" = VRAM/bench auto pick, "env" = OLLAMA_MODEL only.
        self.active_source = "env"
        self.auto_default_meta: dict[str, Any] = {}
        self._load_state()
        if self.auto_default:
            try:
                self.ensure_default()
            except Exception as exc:  # Ollama down at startup: retried on the next model-list read
                logger.warning("Default model check skipped: %s", exc)

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
            source = state.get("active_source")
            if source in ("user", "auto"):
                self.active_source = source
            else:  # legacy file: a model other than the env default must have been picked by hand
                self.active_source = "user" if self.settings.ollama_model != self.default_model else "env"
        self.auto_fallback = bool(state.get("auto_fallback", True))
        self.force_fallback = bool(state.get("force_fallback", False))
        if state.get("fallback_model") and not os.getenv("FALLBACK_MODEL"):
            self.fallback_model = str(state["fallback_model"])
        if isinstance(state.get("auto_default_meta"), dict):
            self.auto_default_meta = dict(state["auto_default_meta"])

    def _save_state(self) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.state_path.with_suffix(".tmp")
        tmp.write_text(json.dumps({
            "active_model": self.active_model, "active_source": self.active_source,
            "fallback_model": self.fallback_model,
            "auto_fallback": self.auto_fallback, "force_fallback": self.force_fallback,
            "auto_default_meta": self.auto_default_meta,
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

    def recommend_auto_default(self, installed: list[dict[str, Any]]) -> tuple[str | None, dict[str, Any]]:
        return choose_auto_default(
            installed,
            fallback_model=self.fallback_model,
            router_model=get_gpu_config().router_model,
        )

    def ensure_default(self, installed: list[dict[str, Any]] | None = None) -> str | None:
        """Apply the VRAM/bench auto default unless the user's pick is still installed."""
        if not self.auto_default:
            return None
        installed = self.installed() if installed is None else installed
        chosen, meta = self.recommend_auto_default(installed)
        if not chosen:
            return None
        names = {m.get("name") for m in installed}
        current = self.active_model
        if self.active_source == "user" and current in names:
            return None
        self.auto_default_meta = meta
        if current == chosen and self.active_source == "auto":
            self._save_state()
            return None
        reason = ("saved model %r is no longer installed" % current) if current not in names else "auto_default"
        with self._lock:
            self.settings.ollama_model = chosen
            self.active_source = "auto"
            self._save_state()
        if current != chosen:
            logger.info("Default chat model -> %s (%s; %s)", chosen, meta.get("reason"), reason)
        return chosen if current != chosen else None

    def _default_selection_status(self, installed: list[dict[str, Any]],
                                  active_row: dict[str, Any] | None) -> dict[str, Any]:
        auto_name, auto_meta = self.recommend_auto_default(installed)
        scores = load_bench_scores()
        if self.active_source == "user":
            size = int(active_row["size"]) if active_row else 0
            active = self.active_model
            sc = scores.get(active) or scores.get(active.split(":", 1)[0])
            return {
                "active_source": "user",
                "active_model": active,
                "fits_vram": model_fits_vram(size),
                "bench_effective_correct_pct": sc.get("effective_correct_pct") if sc else None,
                "auto_default_model": auto_name,
                "auto_default_meta": auto_meta,
            }
        meta = dict(self.auto_default_meta or auto_meta)
        meta["active_source"] = self.active_source
        meta["active_model"] = self.active_model
        meta["auto_default_model"] = auto_name
        if meta.get("bench_effective_correct_pct") is None and self.active_model:
            sc = scores.get(self.active_model) or scores.get(self.active_model.split(":", 1)[0])
            if sc:
                meta["bench_effective_correct_pct"] = sc.get("effective_correct_pct")
        return meta

    def status(self) -> dict[str, Any]:
        installed = self.installed()
        try:
            self.ensure_default(installed)
        except Exception as exc:
            logger.warning("Default model check failed: %s", exc)
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
            size = int(m.get("size") or 0)
            warn = vram_warning_for_model(size)
            models.append({
                "name": name, "size": size,
                "parameter_size": details.get("parameter_size"),
                "quantization": details.get("quantization_level"), "family": details.get("family"),
                "thinking": is_thinking_model(name),
                "active": name == self.active_model, "fallback": name == self.fallback_model,
                "default": name == self.default_model, "loaded": loaded,
                "vram_warning": warn,
            })
        models.sort(key=lambda x: x["size"])
        other_loaded = [
            {"name": n, "size": int(r.get("size") or 0), "size_vram": int(r.get("size_vram") or 0)}
            for n, r in running.items() if _EMBED_RE.search(n or "")
        ]
        cfg = get_gpu_config()
        active_row = next((m for m in models if m["active"]), None)
        auto_name, _ = self.recommend_auto_default(installed)
        return {"active_model": self.active_model, "active_source": self.active_source,
                "smallest_model": auto_name,  # legacy key: recommended auto default
                "auto_default_model": auto_name,
                "default_selection": self._default_selection_status(installed, active_row),
                "fallback_model": self.fallback_model,
                "default_model": self.default_model, "auto_fallback": self.auto_fallback,
                "force_fallback": self.force_fallback, "keep_alive": self.keep_alive,
                "vram_budget": budget_summary(cfg),
                "router_model": cfg.router_model,
                "vram_active_warning": active_row.get("vram_warning") if active_row else None,
                "embed_on_cpu": cfg.embed_on_cpu,
                "models": models, "other_loaded": other_loaded}

    def _require_installed(self, name: str) -> None:
        if name not in self.chat_model_names():
            raise ValueError(f"Model {name!r} is not an installed chat model")

    def select(self, name: str) -> dict[str, Any]:
        self._require_installed(name)
        with self._lock:
            self.settings.ollama_model = name
            self.active_source = "user"
            self._save_state()
        logger.info("Active chat model -> %s (picked by user)", name)
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
        publish("model", f"loading {name} into memory…", source="models")
        t0 = time.monotonic()
        resp = self._http("POST", self._url("/api/generate"),
                          {"model": name, "prompt": "", "stream": False,
                           "keep_alive": keep_alive or self.keep_alive,
                           "options": chat_options()},
                          300)
        return {"model": name, "loaded": True, "seconds": round(time.monotonic() - t0, 2),
                "load_duration_ms": round((resp.get("load_duration") or 0) / 1e6)}

    def eject(self, name: str) -> dict[str, Any]:
        publish("model", f"ejecting {name} from memory", source="models")
        self._http("POST", self._url("/api/generate"), {"model": name, "prompt": "", "stream": False,
                                                        "keep_alive": 0}, 60)
        return {"model": name, "loaded": False}

    # ------------------------------------------------------------ fallback
    def settings_for(self, model: str) -> Settings:
        return replace(self.settings, ollama_model=model)

    def notify_fallback_escalation(self, reason: str) -> None:
        """Activity warning: quality fallback may evict the pinned router from GPU."""
        publish(
            "model",
            f"Using quality fallback {self.fallback_model!r} ({reason}). "
            "Loading it may push the pinned router model mostly to CPU and slow routing.",
            source="models",
            level="warning",
        )

    def initial_model(self, text: str) -> tuple[str, str | None]:
        """Model for this turn and the reason when the fallback is used up front."""
        if self.force_fallback:
            self.notify_fallback_escalation("forced")
            return self.fallback_model, "forced"
        if wants_escalation(text):
            self.notify_fallback_escalation("user_request")
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
