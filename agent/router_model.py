"""Pin and keep-warm the small utterance-router model (future dispatcher), separate from tools/router.py."""

from __future__ import annotations

import logging
import threading
import time
from contextlib import contextmanager
from typing import Any, Callable

from activity import publish
from agent.gpu_config import GpuRuntimeConfig, get_gpu_config
from agent.models import _default_http
from config import Settings

logger = logging.getLogger(__name__)

HttpFn = Callable[[str, str, dict[str, Any] | None, float], dict[str, Any]]

_service: "RouterModelService | None" = None
_classify_lock = threading.Lock()
_classify_inflight = 0
_last_classify_at = 0.0


def router_classify_active() -> bool:
    with _classify_lock:
        return _classify_inflight > 0


def router_recently_classifying(grace_sec: float) -> bool:
    with _classify_lock:
        return _classify_inflight > 0 or (time.monotonic() - _last_classify_at) < grace_sec


@contextmanager
def router_classify_guard():
    global _classify_inflight, _last_classify_at
    with _classify_lock:
        _classify_inflight += 1
        _last_classify_at = time.monotonic()
    try:
        yield
    finally:
        with _classify_lock:
            _classify_inflight = max(0, _classify_inflight - 1)
            _last_classify_at = time.monotonic()


def _model_matches(name: str, target: str) -> bool:
    if not name or not target:
        return False
    if name == target:
        return True
    base = target.split(":")[0]
    return name.split(":")[0] == base or name.startswith(f"{base}:")


class RouterModelService:
    def __init__(self, settings: Settings, cfg: GpuRuntimeConfig | None = None,
                 http: HttpFn | None = None) -> None:
        self.settings = settings
        self.cfg = cfg or get_gpu_config()
        self._http = http or _default_http
        self._stop = threading.Event()
        self._warm_thread: threading.Thread | None = None
        self._started = False
        self._reload_lock = threading.Lock()
        self._last_reload_at = 0.0
        self._last_reload_mono = 0.0
        self._last_check_at = 0.0
        self._placement_state = "unknown"
        self._activity_state: str | None = None
        self._last_gpu_fraction: float | None = None

    @property
    def enabled(self) -> bool:
        return self.cfg.router_pin_enabled and bool(self.cfg.router_model)

    def _url(self, path: str) -> str:
        return f"{self.settings.ollama_host.rstrip('/')}{path}"

    def fetch_ps(self) -> list[dict[str, Any]]:
        return list(self._http("GET", self._url("/api/ps"), None, 10).get("models", []))

    def _router_row(self, ps: list[dict[str, Any]]) -> dict[str, Any] | None:
        for row in ps:
            if _model_matches(str(row.get("name") or ""), self.cfg.router_model):
                return row
        return None

    @staticmethod
    def gpu_fraction(row: dict[str, Any]) -> float:
        size = int(row.get("size") or 0)
        vram = int(row.get("size_vram") or 0)
        if size <= 0:
            return 0.0
        return vram / size

    def free_vram_for_router(self, ps: list[dict[str, Any]]) -> int:
        """VRAM bytes left for the router on GPU after desktop reserve and other loaded models."""
        cap = int(max(0.0, self.cfg.vram_total_gb - self.cfg.vram_desktop_gb) * 1_000_000_000)
        other = sum(
            int(m.get("size_vram") or 0)
            for m in ps
            if not _model_matches(str(m.get("name") or ""), self.cfg.router_model)
        )
        return max(0, cap - other)

    def has_gpu_room(self, ps: list[dict[str, Any]], router_row: dict[str, Any]) -> bool:
        need = int(router_row.get("size") or 0)
        if need <= 0:
            return False
        return self.free_vram_for_router(ps) >= need

    def placement_status(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "model": self.cfg.router_model,
            "state": self._placement_state,
            "gpu_fraction": self._last_gpu_fraction,
            "gpu_percent": round(100 * self._last_gpu_fraction) if self._last_gpu_fraction is not None else None,
            "last_check_at": self._last_check_at or None,
            "last_reload_at": self._last_reload_at or None,
            "min_gpu_fraction": self.cfg.router_min_gpu_fraction,
            "check_interval_sec": self.cfg.router_placement_check_sec,
        }

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

    def _unload_router(self) -> None:
        self._http(
            "POST",
            self._url("/api/generate"),
            {"model": self.cfg.router_model, "prompt": "", "stream": False, "keep_alive": 0},
            60,
        )

    def _reload_router(self, *, reason: str = "placement") -> dict[str, Any]:
        with self._reload_lock:
            self._unload_router()
            info = self.pin()
            self._last_reload_at = time.time()
            self._last_reload_mono = time.monotonic()
            ps = self.fetch_ps()
            row = self._router_row(ps) or {}
            frac = self.gpu_fraction(row) if row else 1.0
            pct = int(round(frac * 100))
            publish(
                "model",
                f"router reloaded onto GPU ({pct}%)",
                source="router",
                data={"reason": reason, "seconds": info.get("seconds")},
            )
            self._set_activity_state("gpu_ok" if frac >= self.cfg.router_min_gpu_fraction else "cpu_partial")
            self._placement_state = "gpu_ok" if frac >= self.cfg.router_min_gpu_fraction else "cpu_partial"
            self._last_gpu_fraction = frac
            return {"reloaded": True, "reason": reason, "gpu_percent": pct, **info}

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

    def _set_activity_state(self, state: str, message: str | None = None) -> None:
        if state == self._activity_state:
            return
        self._activity_state = state
        if message:
            publish("model", message, source="router", data={"placement_state": state})

    def placement_check(self) -> dict[str, Any]:
        """Read /api/ps and optionally reload the router back onto GPU."""
        self._last_check_at = time.time()
        if not self.enabled:
            self._placement_state = "disabled"
            return self.placement_status()
        ps = self.fetch_ps()
        row = self._router_row(ps)
        if not row:
            self._placement_state = "not_loaded"
            self._last_gpu_fraction = None
            return self.placement_status()
        frac = self.gpu_fraction(row)
        self._last_gpu_fraction = round(frac, 4)
        min_frac = self.cfg.router_min_gpu_fraction
        if frac >= min_frac:
            self._placement_state = "gpu_ok"
            self._set_activity_state("gpu_ok")
            return self.placement_status()
        pct = int(round(frac * 100))
        self._placement_state = "cpu_partial"
        room = self.has_gpu_room(ps, row)
        if not room:
            self._set_activity_state(
                "cpu_wait",
                f"router on CPU ({pct}%) — waiting for GPU room",
            )
            return self.placement_status()
        if router_recently_classifying(self.cfg.router_classify_grace_sec):
            return self.placement_status()
        if time.monotonic() - self._last_reload_mono < self.cfg.router_placement_reload_cooldown_sec:
            return self.placement_status()
        try:
            return {**self.placement_status(), **self._reload_router(reason="placement")}
        except Exception as exc:
            logger.warning("Router placement reload failed: %s", exc)
            publish("error", f"router GPU reload failed: {exc}", source="router", level="warning")
            return self.placement_status()

    def force_reload(self) -> dict[str, Any]:
        if router_classify_active():
            raise RuntimeError("router classify in flight")
        with self._reload_lock:
            self._unload_router()
            info = self.pin()
            self._last_reload_at = time.time()
            self._last_reload_mono = time.monotonic()
            ps = self.fetch_ps()
            row = self._router_row(ps) or {}
            frac = self.gpu_fraction(row) if row else 1.0
            pct = int(round(frac * 100))
            self._last_gpu_fraction = frac
            self._placement_state = "gpu_ok" if frac >= self.cfg.router_min_gpu_fraction else "cpu_partial"
            publish("model", f"router reloaded onto GPU ({pct}%)", source="router", data={"manual": True})
            self._set_activity_state("gpu_ok" if pct >= 90 else "cpu_partial")
            return {"reloaded": True, "manual": True, "gpu_percent": pct, **info}

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
        warm_iv = max(30.0, self.cfg.router_keep_warm_sec)
        place_iv = max(5.0, self.cfg.router_placement_check_sec)
        self._warm_thread = threading.Thread(
            target=self._maintenance_loop, args=(warm_iv, place_iv), name="router-warm", daemon=True)
        self._warm_thread.start()

    def _maintenance_loop(self, warm_interval: float, placement_interval: float) -> None:
        tick = min(warm_interval, placement_interval, 15.0)
        last_warm = 0.0
        last_place = 0.0
        while not self._stop.wait(tick):
            now = time.monotonic()
            if now - last_place >= placement_interval:
                last_place = now
                try:
                    self.placement_check()
                except Exception as exc:
                    logger.debug("Router placement check failed: %s", exc)
            if now - last_warm >= warm_interval:
                last_warm = now
                try:
                    self.warm_ping()
                    logger.debug("Router keep-warm ping ok (%s)", self.cfg.router_model)
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