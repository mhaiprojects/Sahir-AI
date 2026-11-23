"""ClickUp REST API v2 client + worker/sync helpers.

Credentials: ``CLICKUP_API_TOKEN``, ``CLICKUP_LIST_ID``, optional ``CLICKUP_TEAM_ID`` and
``CLICKUP_TOKEN_FILE``. Tokens are never logged. HTTP uses ``transport`` so tests mock all calls.
"""

from __future__ import annotations

import logging
import os
import re
import time
from pathlib import Path
from typing import Any, Callable

import requests

from agent.delegation import Task, TaskResult, WorkerError

logger = logging.getLogger(__name__)

API_BASE = "https://api.clickup.com/api/v2"
DEFAULT_TIMEOUT_S = 15.0
DEFAULT_TOKEN_FILE = Path.home() / ".config" / "jahash-secrets" / "clickup-token.txt"

Transport = Callable[..., Any]

CONFIGURED_HINT = (
    "ClickUp is not configured. Add CLICKUP_API_TOKEN and CLICKUP_LIST_ID "
    "(see docs/integrations/clickup.md)."
)


class ClickUpError(RuntimeError):
    pass


def _redact(text: str, token: str) -> str:
    if not token or len(token) < 4:
        return text
    return text.replace(token, "[REDACTED]")


def _default_token_path() -> Path:
    raw = os.getenv("CLICKUP_TOKEN_FILE", "").strip()
    return Path(raw).expanduser() if raw else DEFAULT_TOKEN_FILE


def load_clickup_token(
    *,
    env_token: str | None = None,
    token_file: Path | None = None,
) -> tuple[str, str, str | None]:
    """Return (token, source, load_error). source is env|file|none."""
    env = (env_token if env_token is not None else os.getenv("CLICKUP_API_TOKEN", "")).strip()
    if env:
        return env, "env", None
    path = token_file or _default_token_path()
    if not path.is_file():
        return "", "none", None
    try:
        mode = path.stat().st_mode
        if mode & 0o077:
            return (
                "",
                "file",
                f"ClickUp token file {path} must be mode 600 (not group/world readable)",
            )
        token = path.read_text(encoding="utf-8").strip()
        if not token:
            return "", "file", f"ClickUp token file {path} is empty"
        return token, "file", None
    except OSError as exc:
        return "", "file", f"Could not read ClickUp token file {path}: {exc}"


class ClickUpClient:
    def __init__(
        self,
        token: str | None = None,
        *,
        list_id: str | None = None,
        team_id: str | None = None,
        transport: Transport | None = None,
        timeout_s: float = DEFAULT_TIMEOUT_S,
        token_source: str = "none",
        token_load_error: str | None = None,
    ) -> None:
        if token is None:
            token, src, err = load_clickup_token()
            token_source = src
            token_load_error = err
        else:
            token_source = token_source or ("env" if token else "none")
        self._token = (token or "").strip()
        self.list_id = (list_id or os.getenv("CLICKUP_LIST_ID", "").strip())
        self.team_id = (team_id or os.getenv("CLICKUP_TEAM_ID", "").strip()) or None
        self._transport = transport or requests.request
        self.timeout_s = timeout_s
        self.token_source = token_source
        self.token_load_error = token_load_error
        self.last_error: str | None = token_load_error
        self.last_ok_at: float | None = None

    @classmethod
    def from_settings(cls, settings: Any, *, transport: Transport | None = None) -> ClickUpClient:
        token = getattr(settings, "clickup_api_token", "") or ""
        return cls(
            token,
            list_id=getattr(settings, "clickup_list_id", "") or "",
            team_id=getattr(settings, "clickup_team_id", "") or None,
            transport=transport,
            token_source=getattr(settings, "clickup_token_source", "none") or "none",
            token_load_error=getattr(settings, "clickup_token_load_error", None),
        )

    @property
    def has_token(self) -> bool:
        return bool(self._token) and not self.token_load_error

    @property
    def configured(self) -> bool:
        return self.has_token and bool(self.list_id)

    def status_payload(self) -> dict[str, Any]:
        return {
            "configured": self.configured,
            "has_token": self.has_token,
            "list_id_set": bool(self.list_id),
            "token_source": self.token_source,
            "last_error": self.last_error,
            "last_ok_at": self.last_ok_at,
            "needs_credentials_message": (
                None
                if self.configured
                else "Needs credentials: add CLICKUP_API_TOKEN and CLICKUP_LIST_ID (see docs/integrations/clickup.md)"
            ),
        }

    def _map_http_error(self, status: int, path: str, body: str) -> str:
        if status == 401:
            return "ClickUp token rejected"
        if status == 404 and "/list/" in path:
            return "ClickUp list id not found"
        if status == 429:
            return "ClickUp rate limited"
        if status >= 500:
            return f"ClickUp server error (HTTP {status})"
        short = _redact(body[:300], self._token)
        return f"ClickUp request failed: HTTP {status} {short}"

    def _call(self, method: str, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        if self.token_load_error:
            raise ClickUpError(self.token_load_error)
        if not self._token:
            raise ClickUpError(CONFIGURED_HINT)
        url = f"{API_BASE}{path}"
        headers = {"Authorization": self._token, "Content-Type": "application/json"}
        attempt = 0
        while True:
            attempt += 1
            try:
                req_kw: dict[str, Any] = {"headers": headers, "timeout": self.timeout_s}
                if payload is not None:
                    req_kw["json"] = payload
                resp = self._transport(method, url, **req_kw)
            except requests.Timeout:
                self.last_error = "ClickUp request timed out"
                raise ClickUpError(self.last_error) from None
            except requests.RequestException as exc:
                self.last_error = f"ClickUp network error: {exc}"
                raise ClickUpError(_redact(self.last_error, self._token)) from None
            status = getattr(resp, "status_code", 0)
            if status == 429 and attempt == 1:
                retry_after = 1.0
                try:
                    hdr = getattr(resp, "headers", {}) or {}
                    retry_after = float(hdr.get("Retry-After") or hdr.get("retry-after") or 1)
                except (TypeError, ValueError):
                    retry_after = 1.0
                time.sleep(min(max(retry_after, 0.5), 30.0))
                continue
            if status >= 400:
                text = getattr(resp, "text", "") or ""
                msg = self._map_http_error(status, path, text)
                self.last_error = msg
                raise ClickUpError(_redact(msg, self._token))
            try:
                data = resp.json() if getattr(resp, "content", b"x") else {}
            except ValueError:
                data = {}
            self.last_ok_at = time.time()
            self.last_error = None
            return data

    def verify_connection(self) -> dict[str, Any]:
        user = self._call("GET", "/user")
        u = user.get("user") or user
        list_data = self._call("GET", f"/list/{self.list_id}")
        return {
            "ok": True,
            "username": u.get("username") or u.get("email"),
            "list_id": self.list_id,
            "list_name": list_data.get("name"),
        }

    def create_task(
        self,
        name: str,
        *,
        description: str = "",
        tags: list[str] | None = None,
        status: str | None = None,
        priority: int | None = None,
        list_id: str | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {"name": name[:250], "markdown_description": description[:20000]}
        if tags:
            payload["tags"] = tags
        if status:
            payload["status"] = status
        if priority:
            payload["priority"] = priority
        lid = list_id or self.list_id
        if not lid:
            raise ClickUpError("CLICKUP_LIST_ID is not set")
        return self._call("POST", f"/list/{lid}/task", payload)

    def update_status(self, task_id: str, status: str) -> dict[str, Any]:
        return self._call("PUT", f"/task/{task_id}", {"status": status})

    def add_comment(self, task_id: str, text: str) -> dict[str, Any]:
        return self._call("POST", f"/task/{task_id}/comment", {"comment_text": text[:20000], "notify_all": False})

    def list_tasks(self, *, page: int = 0, limit: int = 25, list_id: str | None = None) -> list[dict[str, Any]]:
        lid = list_id or self.list_id
        if not lid:
            raise ClickUpError("CLICKUP_LIST_ID is not set")
        lim = max(1, min(int(limit), 100))
        data = self._call(
            "GET",
            f"/list/{lid}/task?page={int(page)}&order_by=created&reverse=true&include_closed=false",
        )
        tasks = data.get("tasks") or []
        return tasks[:lim]

    def get_task(self, task_id: str) -> dict[str, Any]:
        return self._call("GET", f"/task/{task_id}")

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
        payload = {
            "tid": task_id,
            "start": start_ms,
            "duration": max(1000, int(duration_ms)),
            "description": description[:500],
            "billable": False,
        }
        return self._call("POST", f"/team/{self.get_team_id()}/time_entries", payload)


def execute_clickup_action(client: ClickUpClient, action: dict[str, Any]) -> dict[str, Any]:
    """Run one numbered-list ClickUp action; returns summary dict for RouteResult."""
    if not client.configured:
        raise ClickUpError(CONFIGURED_HINT)
    kind = str(action.get("action") or action.get("clickup_action") or "create_task")
    if kind == "create_task":
        created = client.create_task(
            str(action.get("name") or "Sahir task"),
            description=str(action.get("description") or action.get("details") or ""),
            tags=list(action.get("tags") or []),
            status=action.get("status"),
            list_id=action.get("list_id"),
        )
        name = created.get("name") or action.get("name") or "task"
        url = created.get("url") or ""
        tid = created.get("id")
        summary = f"Created ClickUp task “{name}”"
        if url:
            summary += f" — {url}"
        return {"action": "create_task", "task_id": tid, "url": url, "name": name, "summary": summary, "reply": summary}
    tid = str(action.get("task_id") or "")
    if not tid:
        raise ClickUpError(f"ClickUp {kind} needs a task_id")
    if kind == "update_status":
        client.update_status(tid, str(action.get("status") or "complete"))
        return {"action": kind, "task_id": tid, "summary": f"Updated ClickUp task {tid} status", "reply": f"Updated status on ClickUp task {tid}."}
    if kind == "add_comment":
        client.add_comment(tid, str(action.get("text") or action.get("description") or ""))
        return {"action": kind, "task_id": tid, "summary": f"Commented on ClickUp task {tid}", "reply": f"Added a comment on ClickUp task {tid}."}
    if kind == "list_tasks":
        tasks = client.list_tasks(limit=int(action.get("limit") or 10))
        lines = [f"{t.get('id')}: {t.get('name')}" for t in tasks[:10]]
        reply = "Recent ClickUp tasks:\n" + "\n".join(lines) if lines else "No recent tasks in the list."
        return {"action": kind, "tasks": tasks, "summary": reply, "reply": reply}
    if kind == "get_task":
        task = client.get_task(tid)
        name = task.get("name") or tid
        url = task.get("url") or ""
        reply = f"Task {name}" + (f" — {url}" if url else "")
        return {"action": kind, "task_id": tid, "summary": reply, "reply": reply}
    raise ClickUpError(f"Unknown ClickUp action {kind!r}")


def _output_text(res: TaskResult) -> str:
    out = res.output
    if isinstance(out, dict):
        return str(out.get("summary") or out.get("text") or out.get("reply") or out)
    return str(out if out is not None else "")


class ClickUpSync:
    def __init__(
        self,
        client: ClickUpClient,
        *,
        done_status: str = "complete",
        failed_status: str | None = None,
        tags: list[str] | None = None,
        name_prefix: str = "[Voice agent] ",
    ) -> None:
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
            res.output = {
                **(res.output if isinstance(res.output, dict) else {"text": _output_text(res)}),
                "clickup": {"skipped": CONFIGURED_HINT},
            }
            return
        tid = spec.get("task_id")
        tags = list(dict.fromkeys(self.tags + list(spec.get("tags") or [])))
        if not tid:
            created = self.client.create_task(
                spec.get("name") or f"{self.name_prefix}{task.description[:120]}",
                description=f"Delegated {task.type} task `{task.id}`.\n\n{task.description}",
                tags=tags,
                list_id=spec.get("list_id"),
            )
            tid = created.get("id")
            actions.append({"action": "create_task", "task_id": tid, "url": created.get("url"), "name": created.get("name")})
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
    type = "clickup"
    description = "ClickUp tasks, comments, status when user says ClickUp."
    timeout_s = 30.0
    max_retries = 1
    consumes_results = True

    def __init__(self, client: ClickUpClient) -> None:
        self.client = client

    def run(self, task: Task) -> dict[str, Any]:
        if not self.client.configured:
            raise WorkerError(CONFIGURED_HINT)
        ctx = task.context
        action = ctx.get("action") or ctx.get("clickup_action") or "create_task"
        prev = ctx.get("previous_results") or []
        prev_text = "\n\n".join(str(p.get("summary") or p.get("output") or "") for p in prev if p)
        act = {
            "action": action,
            "name": ctx.get("name") or task.description[:120],
            "description": (ctx.get("details") or task.description) + (f"\n\n---\n{prev_text}" if prev_text else ""),
            "tags": list(dict.fromkeys(["voice-agent"] + list(ctx.get("tags") or []))),
            "list_id": ctx.get("list_id"),
            "task_id": ctx.get("task_id"),
            "status": ctx.get("status"),
            "text": ctx.get("text") or prev_text,
        }
        out = execute_clickup_action(self.client, act)
        if action == "create_task":
            return out
        return {"action": action, "task_id": out.get("task_id"), "summary": out.get("summary"), "reply": out.get("reply")}