"""ClickUp REST API v2 client + mapping of worker results to ClickUp actions.

Token: ``CLICKUP_API_TOKEN`` (from .env). It is sent only in the Authorization header and is
never logged. All HTTP goes through ``transport`` so tests can mock it.
"""

from __future__ import annotations

import logging
import os
import time
from typing import Any, Callable

import requests

from agent.delegation import Task, TaskResult, WorkerError

logger = logging.getLogger(__name__)

API_BASE = "https://api.clickup.com/api/v2"
DEFAULT_LIST_ID = ""  # set CLICKUP_LIST_ID in .env

Transport = Callable[..., Any]  # (method, url, headers=..., json=..., timeout=...) -> response-like


class ClickUpError(RuntimeError):
    pass


class ClickUpClient:
    def __init__(self, token: str | None = None, *, list_id: str | None = None, team_id: str | None = None,
                 transport: Transport | None = None, timeout_s: float = 20.0) -> None:
        self._token = token if token is not None else os.getenv("CLICKUP_API_TOKEN", "").strip()
        self.list_id = list_id or os.getenv("CLICKUP_LIST_ID", "").strip() or DEFAULT_LIST_ID
        self.team_id = team_id or os.getenv("CLICKUP_TEAM_ID", "").strip() or None
        self._transport = transport or requests.request
        self.timeout_s = timeout_s

    @property
    def configured(self) -> bool:
        return bool(self._token and self.list_id)

    def _call(self, method: str, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        if not self._token:
            raise ClickUpError("CLICKUP_API_TOKEN is not set (add it to .env)")
        url = f"{API_BASE}{path}"
        headers = {"Authorization": self._token, "Content-Type": "application/json"}
        resp = self._transport(method, url, headers=headers, json=payload, timeout=self.timeout_s)
        status = getattr(resp, "status_code", 0)
        if status >= 400:
            text = getattr(resp, "text", "")[:300]
            raise ClickUpError(f"ClickUp {method} {path} failed: HTTP {status} {text}")
        try:
            return resp.json() if getattr(resp, "content", b"x") else {}
        except ValueError:
            return {}

    # ------------------------------------------------------------ actions
    def create_task(self, name: str, *, description: str = "", tags: list[str] | None = None,
                    status: str | None = None, priority: int | None = None, list_id: str | None = None) -> dict[str, Any]:
        payload: dict[str, Any] = {"name": name[:250], "markdown_description": description[:20000]}
        if tags:
            payload["tags"] = tags
        if status:
            payload["status"] = status
        if priority:
            payload["priority"] = priority
        return self._call("POST", f"/list/{list_id or self.list_id}/task", payload)

    def update_status(self, task_id: str, status: str) -> dict[str, Any]:
        return self._call("PUT", f"/task/{task_id}", {"status": status})

    def add_comment(self, task_id: str, text: str) -> dict[str, Any]:
        return self._call("POST", f"/task/{task_id}/comment", {"comment_text": text[:20000], "notify_all": False})

    def get_team_id(self) -> str:
        if self.team_id:
            return self.team_id
        teams = self._call("GET", "/team").get("teams") or []
        if not teams:
            raise ClickUpError("No ClickUp workspace visible to this token")
        self.team_id = str(teams[0]["id"])
        return self.team_id

    def log_time(self, task_id: str, duration_ms: int, *, description: str = "", start_ms: int | None = None) -> dict[str, Any]:
        start_ms = start_ms or int(time.time() * 1000) - int(duration_ms)
        payload = {"tid": task_id, "start": start_ms, "duration": max(1000, int(duration_ms)),
                   "description": description[:500], "billable": False}
        return self._call("POST", f"/team/{self.get_team_id()}/time_entries", payload)


def _output_text(res: TaskResult) -> str:
    out = res.output
    if isinstance(out, dict):
        return str(out.get("summary") or out.get("text") or out.get("reply") or out)
    return str(out if out is not None else "")


class ClickUpSync:
    """Result hook: maps a finished worker result to ClickUp actions.

    Triggered when ``task.context["clickup"]`` is truthy (dict or True):
      * no ``task_id`` → create a task in the default list (tagged; name prefixed)
      * always → add a comment with the worker output (or errors)
      * success → update status to ``done_status``; failure → ``failed_status`` if set
      * always → log a time entry equal to the worker duration
    """

    def __init__(self, client: ClickUpClient, *, done_status: str = "complete", failed_status: str | None = None,
                 tags: list[str] | None = None, name_prefix: str = "[Voice agent] ") -> None:
        self.client = client
        self.done_status = done_status
        self.failed_status = failed_status
        self.tags = tags or ["voice-agent"]
        self.name_prefix = name_prefix

    def __call__(self, task: Task, res: TaskResult) -> None:
        spec = task.context.get("clickup")
        if not spec or task.type == "clickup":
            return
        spec = spec if isinstance(spec, dict) else {}
        actions: list[dict[str, Any]] = []
        if not self.client.configured:
            res.output = {**(res.output if isinstance(res.output, dict) else {"text": _output_text(res)}),
                          "clickup": {"skipped": "CLICKUP_API_TOKEN not set"}}
            return
        tid = spec.get("task_id")
        tags = list(dict.fromkeys(self.tags + list(spec.get("tags") or [])))
        if not tid:
            created = self.client.create_task(
                spec.get("name") or f"{self.name_prefix}{task.description[:120]}",
                description=f"Delegated {task.type} task `{task.id}`.\n\n{task.description}",
                tags=tags, list_id=spec.get("list_id"),
            )
            tid = created.get("id")
            actions.append({"action": "create_task", "task_id": tid, "url": created.get("url")})
        body = _output_text(res) if res.ok else "Errors:\n" + "\n".join(res.errors)
        self.client.add_comment(tid, f"Worker `{res.worker}` → {res.status} in {res.duration:.1f}s\n\n{body[:15000]}")
        actions.append({"action": "add_comment", "task_id": tid})
        new_status = self.done_status if res.ok else self.failed_status
        if new_status:
            self.client.update_status(tid, new_status)
            actions.append({"action": "update_status", "task_id": tid, "status": new_status})
        self.client.log_time(tid, int(res.duration * 1000), description=f"voice-agent {task.type} task {task.id}")
        actions.append({"action": "log_time", "task_id": tid, "duration_ms": int(res.duration * 1000)})
        if isinstance(res.output, dict):
            res.output["clickup"] = actions
        else:
            res.output = {"text": _output_text(res), "clickup": actions}


class ClickUpWorker:
    """Worker for explicit ClickUp requests: context["action"] in create_task|update_status|add_comment|log_time."""

    type = "clickup"
    description = "ClickUp tasks, comments, time when user says ClickUp."
    timeout_s = 30.0
    max_retries = 1
    consumes_results = True  # runs after producer workers in the same orchestration round

    def __init__(self, client: ClickUpClient) -> None:
        self.client = client

    def run(self, task: Task) -> dict[str, Any]:
        if not self.client.configured:
            raise WorkerError("CLICKUP_API_TOKEN is not set; add it to .env to enable ClickUp actions")
        ctx = task.context
        action = ctx.get("action") or "create_task"
        prev = ctx.get("previous_results") or []
        prev_text = "\n\n".join(str(p.get("summary") or p.get("output") or "") for p in prev if p)
        if action == "create_task":
            created = self.client.create_task(
                ctx.get("name") or task.description[:120],
                description=(ctx.get("details") or task.description) + (f"\n\n---\n{prev_text}" if prev_text else ""),
                tags=list(dict.fromkeys(["voice-agent"] + list(ctx.get("tags") or []))),
                list_id=ctx.get("list_id"),
            )
            return {"action": "create_task", "task_id": created.get("id"), "url": created.get("url"),
                    "summary": f"Created ClickUp task {created.get('id')}"}
        tid = ctx.get("task_id")
        if not tid:
            raise WorkerError(f"{action} needs a ClickUp task_id")
        if action == "update_status":
            self.client.update_status(tid, ctx.get("status") or "complete")
        elif action == "add_comment":
            self.client.add_comment(tid, ctx.get("text") or prev_text or task.description)
        elif action == "log_time":
            self.client.log_time(tid, int(ctx.get("duration_ms") or 60_000), description=task.description)
        else:
            raise WorkerError(f"Unknown ClickUp action {action!r}")
        return {"action": action, "task_id": tid, "summary": f"ClickUp {action} on {tid}"}
