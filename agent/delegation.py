"""Layer 3 — delegation: structured tasks/results, worker registry, queue or direct transport,
timeout + retry, callbacks, and persistence of every handoff in SQLite.

Task schema   : {id, type, description, context, priority, callback}
Result schema : {task_id, status, output, errors, duration}
"""

from __future__ import annotations

import itertools
import json
import logging
import queue
import threading
import time
import uuid
from concurrent.futures import Future, ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from dataclasses import asdict, dataclass, field
from typing import Any, Callable, Protocol

from agent.store import AgentStore, utcnow

from activity import publish

logger = logging.getLogger(__name__)

PRIORITIES = {"urgent": 1, "high": 2, "normal": 3, "low": 4}

TASKS_SCHEMA = """
CREATE TABLE IF NOT EXISTS tasks (
    id TEXT PRIMARY KEY,
    type TEXT NOT NULL,
    description TEXT NOT NULL,
    context TEXT NOT NULL DEFAULT '{}',
    priority INTEGER NOT NULL DEFAULT 3,
    callback TEXT,
    conversation_id INTEGER,
    parent_id TEXT,
    status TEXT NOT NULL DEFAULT 'queued',
    output TEXT,
    errors TEXT NOT NULL DEFAULT '[]',
    duration REAL,
    attempts INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    finished_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_tasks_conv ON tasks (conversation_id, created_at);
"""


@dataclass
class Task:
    type: str
    description: str
    context: dict[str, Any] = field(default_factory=dict)
    priority: int = 3
    callback: str | None = None
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])

    @classmethod
    def create(cls, type: str, description: str, *, context: dict[str, Any] | None = None,
               priority: int | str = 3, callback: str | None = None) -> "Task":
        if isinstance(priority, str):
            priority = PRIORITIES.get(priority.lower(), 3)
        return cls(type=type, description=description.strip(), context=dict(context or {}),
                   priority=max(1, min(4, int(priority))), callback=callback)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class TaskResult:
    task_id: str
    status: str  # success | error | timeout
    output: Any = None
    errors: list[str] = field(default_factory=list)
    duration: float = 0.0
    worker: str = ""
    attempts: int = 1

    @property
    def ok(self) -> bool:
        return self.status == "success"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class Worker(Protocol):
    type: str
    description: str
    timeout_s: float
    max_retries: int

    def run(self, task: Task) -> Any: ...


class WorkerError(RuntimeError):
    """Raise from a worker for a non-retryable failure."""


class Dispatcher:
    """Routes tasks to workers. ``dispatch`` = direct call; ``submit`` = priority queue."""

    def __init__(self, store: AgentStore | None = None, *, queue_workers: int = 2,
                 retry_backoff_s: float = 0.5) -> None:
        self.store = store
        self.workers: dict[str, Worker] = {}
        self.callbacks: dict[str, Callable[[Task, TaskResult], None]] = {}
        self.result_hooks: list[Callable[[Task, TaskResult], None]] = []
        self.retry_backoff_s = retry_backoff_s
        self._exec = ThreadPoolExecutor(max_workers=8, thread_name_prefix="worker")
        self._queue: queue.PriorityQueue = queue.PriorityQueue()
        self._seq = itertools.count()
        self._queue_threads: list[threading.Thread] = []
        self._queue_workers = queue_workers
        if store is not None:
            conn = store._connect()
            try:
                conn.executescript(TASKS_SCHEMA)
                conn.commit()
            finally:
                conn.close()

    # ------------------------------------------------------------ registry
    def register(self, worker: Worker) -> None:
        self.workers[worker.type] = worker

    def register_callback(self, name: str, fn: Callable[[Task, TaskResult], None]) -> None:
        self.callbacks[name] = fn

    def catalog(self) -> list[dict[str, str]]:
        return [{"type": w.type, "description": w.description} for w in self.workers.values()]

    # --------------------------------------------------------- persistence
    def _persist_task(self, task: Task) -> None:
        if not self.store:
            return
        self.store._exec(
            """INSERT OR REPLACE INTO tasks (id, type, description, context, priority, callback, conversation_id,
               parent_id, status, created_at) VALUES (?,?,?,?,?,?,?,?, 'queued', ?)""",
            (task.id, task.type, task.description, json.dumps(task.context, ensure_ascii=False, default=str),
             task.priority, task.callback, task.context.get("conversation_id"), task.context.get("parent_task_id"),
             utcnow()),
        )

    def _persist_result(self, res: TaskResult) -> None:
        if not self.store:
            return
        self.store._exec(
            "UPDATE tasks SET status=?, output=?, errors=?, duration=?, attempts=?, finished_at=? WHERE id=?",
            (res.status, json.dumps(res.output, ensure_ascii=False, default=str), json.dumps(res.errors),
             res.duration, res.attempts, utcnow(), res.task_id),
        )

    def list_tasks(self, conversation_id: int | None = None, limit: int = 50) -> list[dict[str, Any]]:
        if not self.store:
            return []
        sql, params = "SELECT * FROM tasks", []
        if conversation_id is not None:
            sql += " WHERE conversation_id=?"
            params.append(conversation_id)
        rows = self.store._query(sql + " ORDER BY created_at DESC LIMIT ?", (*params, limit))
        out = []
        for r in rows:
            d = dict(r)
            for k in ("context", "output", "errors"):
                try:
                    d[k] = json.loads(d[k]) if d[k] else None
                except (TypeError, json.JSONDecodeError):
                    pass
            out.append(d)
        return out

    # ----------------------------------------------------------- execution
    def dispatch(self, task: Task) -> TaskResult:
        """Direct-call transport: run now (in a worker thread) with timeout + retry."""
        self._persist_task(task)
        worker = self.workers.get(task.type)
        started = time.monotonic()
        if worker is None:
            res = TaskResult(task.id, "error", errors=[f"No worker registered for type {task.type!r}"],
                             duration=0.0, worker="none", attempts=0)
            self._finish(task, res)
            return res
        publish("worker", f"task {task.id} → {task.type} worker: {' '.join(task.description.split())[:160]}",
                source="dispatcher")
        errors: list[str] = []
        attempts = 0
        status = "error"
        output: Any = None
        for attempt in range(worker.max_retries + 1):
            attempts = attempt + 1
            fut: Future = self._exec.submit(worker.run, task)
            try:
                output = fut.result(timeout=worker.timeout_s)
                status = "success"
                break
            except FutureTimeout:
                status = "timeout"
                errors.append(f"attempt {attempts}: timed out after {worker.timeout_s}s")
            except WorkerError as exc:
                status = "error"
                errors.append(f"attempt {attempts}: {exc}")
                break
            except Exception as exc:
                status = "error"
                errors.append(f"attempt {attempts}: {type(exc).__name__}: {exc}")
            if attempt < worker.max_retries:
                publish("error", f"task {task.id} attempt {attempts} failed ({errors[-1]}); retrying",
                        source="dispatcher", level="warning")
                time.sleep(self.retry_backoff_s * (2**attempt))
        res = TaskResult(task.id, status, output=output, errors=errors,
                         duration=round(time.monotonic() - started, 3), worker=worker.type, attempts=attempts)
        self._finish(task, res)
        return res

    def _finish(self, task: Task, res: TaskResult) -> None:
        self._persist_result(res)
        logger.info("Task %s (%s) -> %s in %.2fs [%d attempt(s)]", task.id, task.type, res.status, res.duration,
                    res.attempts)
        for hook in self.result_hooks:
            try:
                hook(task, res)
            except Exception as exc:
                logger.warning("Result hook failed for %s: %s", task.id, exc)
                res.errors.append(f"hook: {exc}")
        if task.callback and task.callback in self.callbacks:
            try:
                self.callbacks[task.callback](task, res)
            except Exception as exc:
                logger.warning("Callback %s failed for %s: %s", task.callback, task.id, exc)

    def dispatch_many(self, tasks: list[Task]) -> list[TaskResult]:
        """Run independent tasks in parallel (direct transport)."""
        futures = [self._exec.submit(self.dispatch, t) for t in tasks]
        return [f.result() for f in futures]

    # ---------------------------------------------------------- queue mode
    def _ensure_queue_threads(self) -> None:
        self._queue_threads = [t for t in self._queue_threads if t.is_alive()]
        while len(self._queue_threads) < self._queue_workers:
            t = threading.Thread(target=self._queue_loop, daemon=True, name="task-queue")
            t.start()
            self._queue_threads.append(t)

    def _queue_loop(self) -> None:
        while True:
            _prio, _seq, task, fut = self._queue.get()
            try:
                fut.set_result(self.dispatch(task))
            except Exception as exc:  # pragma: no cover
                fut.set_exception(exc)
            finally:
                self._queue.task_done()

    def submit(self, task: Task) -> Future:
        """Queue transport: returns a Future[TaskResult]; lower priority number runs first."""
        self._ensure_queue_threads()
        fut: Future = Future()
        self._queue.put((task.priority, next(self._seq), task, fut))
        return fut
