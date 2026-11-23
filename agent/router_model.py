"""Pin and keep-warm the small utterance-router model (future dispatcher), separate from tools/router.py."""

from __future__ import annotations

import logging
import threading
import time
from typing import Any, Callable

from activity import publish
from agent.gpu_config import GpuRuntimeConfig, get_gpu_config
from agent.models import _default_http
from config import Settings

logger = logging.getLogger(__name__)

HttpFn = Callable[[str, str, dict[str, Any] | None, float], dict[str, Any]]

_service: "RouterModelService | None" = None


class RouterModelService:
    def __init__(self, settings: Settings, cfg: GpuRuntimeConfig | None = None,
                 http: HttpFn | None = None) -> None:
        self.settings = settings
        self.cfg = cfg or get_gpu_config()
        self._http = http or _default_http
        self._stop = threading.Event()
        self._warm_thread: threading.Thread | None = None
        self._started = False

    @property
    def enabled(self) -> bool:
        return self.cfg.router_pin_enabled and bool(self.cfg.router_model)

    def _url(self, path: str) -> str:
        return f"{self.settings.ollama_host.rstrip('/')}{path}"

    def pin(self) -> dict[str, Any]:
        """Load the router model with keep_alive=-1 and a small context window."""
        model = self.cfg.router_model
        payload = {
            "model": model,
            "prompt": " ",
            "stream": False,
            "keep_alive": -1,
            "options": {"num_ctx": self.cfg.router_num_ctx, "num_predict": 1},
        }
        t0 = time.monotonic()
        resp = self._http("POST", self._url("/api/generate"), payload, 300)
        elapsed = round(time.monotonic() - t0, 2)
        return {
            "model": model,
            "seconds": elapsed,
            "load_duration_ms": round((resp.get("load_duration") or 0) / 1e6),
        }

    def warm_ping(self) -> None:
        """Minimal generation to keep clocks up on RDNA1-class cards."""
        payload = {
            "model": self.cfg.router_model,
            "prompt": "x",
            "stream": False,
            "keep_alive": -1,
            "options": {"num_ctx": self.cfg.router_num_ctx, "num_predict": 1},
        }
        self._http("POST", self._url("/api/generate"), payload, 120)

    def start(self) -> None:
        if not self.enabled or self._started:
            return
        self._started = True
        threading.Thread(target=self._bootstrap, name="router-pin", daemon=True).start()

    def _bootstrap(self) -> None:
        model = self.cfg.router_model
        publish("model", f"pinning router model {model} (ctx {self.cfg.router_num_ctx}, keep_alive=-1)…",
                source="router")
        try:
            info = self.pin()
            publish("step",
                    f"router model {model} pinned ({info['seconds']}s"
                    + (f", load {info['load_duration_ms']}ms" if info["load_duration_ms"] else "")
                    + ")",
                    source="router")
        except Exception as exc:
            logger.warning("Router model pin failed (non-fatal): %s", exc)
            publish("error", f"router pin failed: {exc}", source="router", level="warning")
        interval = max(30.0, self.cfg.router_keep_warm_sec)
        self._warm_thread = threading.Thread(target=self._warm_loop, args=(interval,), name="router-warm",
                                             daemon=True)
        self._warm_thread.start()

    def _warm_loop(self, interval: float) -> None:
        while not self._stop.wait(interval):
            try:
                self.warm_ping()
                publish("model", f"router keep-warm ping ({self.cfg.router_model})", source="router")
            except Exception as exc:
                logger.debug("Router keep-warm failed: %s", exc)
                publish("error", f"router keep-warm failed: {exc}", source="router", level="warning")

    def stop(self) -> None:
        self._stop.set()
        if self._warm_thread and self._warm_thread.is_alive():
            self._warm_thread.join(timeout=2.0)


def get_router_service() -> RouterModelService | None:
    return _service


def start_router_service(settings: Settings, *, http: HttpFn | None = None) -> RouterModelService | None:
    global _service
    if _service is not None:
        return _service
    cfg = get_gpu_config()
    if not cfg.router_pin_enabled:
        return None
    _service = RouterModelService(settings, cfg, http=http)
    _service.start()
    return _service


def stop_router_service() -> None:
    global _service
    if _service is not None:
        _service.stop()
        _service = None