"""FastAPI routes for runtime chat-model management and the model benchmark."""

from __future__ import annotations

import json
import logging
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from activity import publish
from agent.models import ModelManager

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
BENCH_DIR = PROJECT_ROOT / "data" / "bench"
BENCH_SCRIPT = PROJECT_ROOT / "scripts" / "bench_models.py"
# A progress.json updated within this many seconds (and not "done") means a run
# started elsewhere (CLI, or before a server restart) is still going (#41).
EXTERNAL_RUN_STALE_S = 300


class ModelRef(BaseModel):
    model: str = Field(..., min_length=1, max_length=200)
    keep_alive: str | None = Field(default=None, max_length=16)


class ModelOptions(BaseModel):
    auto_fallback: bool | None = None
    force_fallback: bool | None = None
    fallback_model: str | None = Field(default=None, max_length=200)


class BenchRequest(BaseModel):
    models: list[str] = Field(default_factory=list)
    quick: bool = False


class BenchRunner:
    """Runs scripts/bench_models.py in a subprocess so the web server stays responsive."""

    def __init__(self, bench_dir: Path = BENCH_DIR, script: Path = BENCH_SCRIPT) -> None:
        self.bench_dir = bench_dir
        self.script = script
        self._proc: subprocess.Popen | None = None
        self._started_at: float | None = None
        self._lock = threading.Lock()

    @property
    def running(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def _read_progress(self) -> dict[str, Any] | None:
        prog = self.bench_dir / "progress.json"
        if not prog.exists():
            return None
        try:
            data = json.loads(prog.read_text())
        except Exception:
            return None
        return data if isinstance(data, dict) else None

    def external_progress(self) -> dict[str, Any] | None:
        """Progress of a run this server didn't start (CLI or pre-restart), if it looks live."""
        if self.running:
            return None
        data = self._read_progress()
        if not data or str(data.get("message", "")).strip() == "done":
            return None
        try:
            at = float(data.get("at") or 0)
        except (TypeError, ValueError):
            return None
        if time.time() - at > EXTERNAL_RUN_STALE_S:
            return None
        return data

    def start(self, models: list[str], quick: bool) -> dict[str, Any]:
        with self._lock:
            if self.running or self.external_progress():
                raise RuntimeError("A benchmark is already running")
            self.bench_dir.mkdir(parents=True, exist_ok=True)
            cmd = [sys.executable, str(self.script), "--out", str(self.bench_dir)]
            if models:
                cmd += ["--models", ",".join(models)]
            if quick:
                cmd.append("--quick")
            log_path = self.bench_dir / "last_run.log"
            log = open(log_path, "w")
            self._proc = subprocess.Popen(cmd, cwd=str(PROJECT_ROOT), stdout=log, stderr=subprocess.STDOUT)
            self._started_at = time.time()
            shown = " ".join(Path(c).name if i < 2 else c for i, c in enumerate(cmd))
            publish("cmd", f"$ {shown}", source="bench", pid=self._proc.pid)
            threading.Thread(target=self._tail, args=(self._proc, log_path), name="bench-tail",
                             daemon=True).start()
            return {"started": True, "pid": self._proc.pid}

    @staticmethod
    def _tail(proc: subprocess.Popen, log_path: Path) -> None:
        """Stream the benchmark's output to the live activity panel while it runs."""
        try:
            with open(log_path, "r", errors="replace") as fh:
                while True:
                    line = fh.readline()
                    if line:
                        publish("out", line.rstrip(), source="bench")
                        continue
                    if proc.poll() is not None:
                        rest = fh.read()
                        for extra in rest.splitlines():
                            publish("out", extra, source="bench")
                        break
                    time.sleep(0.5)
            code = proc.returncode
            publish("step" if code == 0 else "error", f"benchmark exited with code {code}", source="bench",
                    level="info" if code == 0 else "error")
        except Exception as exc:
            publish("error", f"benchmark log tail stopped: {exc}", source="bench", level="warning")

    def status(self) -> dict[str, Any]:
        latest = self.bench_dir / "latest.json"
        results = None
        if latest.exists():
            try:
                results = json.loads(latest.read_text())
            except Exception:
                results = None
        external = self.external_progress()
        progress = self._read_progress() if self.running else external
        return {"running": self.running or external is not None,
                "external": external is not None,
                "started_at": self._started_at,
                "exit_code": None if self._proc is None else self._proc.poll(),
                "progress": progress, "latest": results}


def create_models_router(get_manager: Callable[[], ModelManager], request_lock: threading.Lock | None = None,
                         bench: BenchRunner | None = None) -> APIRouter:
    router = APIRouter(prefix="/api/models", tags=["models"])
    bench = bench or BenchRunner()

    def _call(fn: Callable[[], Any]) -> Any:
        try:
            return fn()
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except HTTPException:
            raise
        except Exception as exc:
            logger.exception("Model API call failed")
            raise HTTPException(status_code=502, detail=f"Ollama error: {exc}") from exc

    @router.get("")
    def list_models() -> dict[str, Any]:
        return _call(lambda: get_manager().status())

    @router.post("/select")
    def select(body: ModelRef) -> dict[str, Any]:
        return _call(lambda: get_manager().select(body.model))

    @router.post("/load")
    def load(body: ModelRef) -> dict[str, Any]:
        return _call(lambda: get_manager().load(body.model, body.keep_alive))

    @router.post("/eject")
    def eject(body: ModelRef) -> dict[str, Any]:
        return _call(lambda: get_manager().eject(body.model))

    @router.post("/options")
    def options(body: ModelOptions) -> dict[str, Any]:
        return _call(lambda: get_manager().set_options(auto_fallback=body.auto_fallback,
                                                       force_fallback=body.force_fallback,
                                                       fallback_model=body.fallback_model))

    @router.get("/benchmark")
    def bench_status() -> dict[str, Any]:
        return bench.status()

    @router.post("/benchmark")
    def bench_start(body: BenchRequest) -> dict[str, Any]:
        try:
            return bench.start(body.models, body.quick)
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    return router
