"""ClickUp integration with a mocked HTTP layer (no real API calls)."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from agent.clickup import (
    CONFIGURED_HINT,
    ClickUpClient,
    ClickUpError,
    ClickUpSync,
    ClickUpWorker,
    execute_clickup_action,
    load_clickup_token,
)
from agent.delegation import Dispatcher, Task, TaskResult, WorkerError
from agent.numbered_actions import execute_numbered_action
from agent.numbered_replies import action_requires_confirm
from config import Settings


class FakeResp:
    def __init__(self, status=200, data=None, headers=None):
        self.status_code = status
        self._data = data or {}
        self.content = b"{}"
        self.text = json.dumps(self._data) if data else ""
        self.headers = headers or {}

    def json(self):
        return self._data


class FakeTransport:
    def __init__(self):
        self.calls = []

    def __call__(self, method, url, headers=None, json=None, timeout=None, **_kw):
        self.calls.append({"method": method, "url": url, "headers": headers, "json": json})
        if url.endswith("/user"):
            return FakeResp(data={"user": {"username": "tester"}})
        if "/list/" in url and url.endswith("/task") and method == "GET":
            return FakeResp(data={"tasks": [{"id": "t1", "name": "One"}]})
        if url.endswith("/team"):
            return FakeResp(data={"teams": [{"id": "9001"}]})
        if method == "POST" and url.endswith("/task") and "/list/" in url:
            name = (json or {}).get("name", "task")
            return FakeResp(data={"id": "abc123", "url": "https://app.clickup.com/t/abc123", "name": name})
        if method == "GET" and "/list/" in url and not url.endswith("/task"):
            return FakeResp(data={"name": "Inbox", "id": "list123"})
        if method == "GET" and "/task/" in url:
            return FakeResp(data={"id": "abc123", "name": "Read back", "url": "https://app.clickup.com/t/abc123"})
        return FakeResp(data={})


def test_requires_token():
    c = ClickUpClient(token="", list_id="list123", transport=FakeTransport())
    assert not c.configured
    with pytest.raises(ClickUpError, match="not configured"):
        c.add_comment("t1", "x")


def test_actions_hit_correct_endpoints():
    t = FakeTransport()
    c = ClickUpClient(token="pk_test", list_id="list123", transport=t)
    assert c.create_task("Test", description="d", tags=["verification"])["id"] == "abc123"
    c.update_status("abc123", "complete")
    c.add_comment("abc123", "hello")
    c.list_tasks(limit=5)
    c.get_task("abc123")
    c.log_time("abc123", 5000, description="work")
    urls = [(x["method"], x["url"].split("/api/v2")[1]) for x in t.calls]
    assert ("POST", "/list/list123/task") in urls
    assert ("GET", "/list/list123/task?page=0&order_by=created&reverse=true&include_closed=false") in urls
    assert t.calls[0]["headers"]["Authorization"] == "pk_test"
    assert t.calls[0]["json"]["tags"] == ["verification"]


def test_http_401_mapping():
    c = ClickUpClient(token="pk_secret", transport=lambda *a, **k: FakeResp(401, {"err": "Token invalid"}))
    with pytest.raises(ClickUpError, match="token rejected") as e:
        c.add_comment("t", "x")
    assert "pk_secret" not in str(e.value)


def test_http_404_list_mapping():
    c = ClickUpClient(token="pk", list_id="bad", transport=lambda *a, **k: FakeResp(404, {}))
    with pytest.raises(ClickUpError, match="list id not found"):
        c.create_task("x")


def test_429_retries_once():
    calls = {"n": 0}

    def transport(method, url, headers=None, json=None, timeout=None, **_kw):
        calls["n"] += 1
        if calls["n"] == 1:
            return FakeResp(429, {}, headers={"Retry-After": "0"})
        return FakeResp(data={"id": "ok", "url": "https://app.clickup.com/t/ok"})

    c = ClickUpClient(token="pk", list_id="list123", transport=transport)
    assert c.create_task("x")["id"] == "ok"
    assert calls["n"] == 2


def test_token_file_permissions(tmp_path, monkeypatch):
    path = tmp_path / "clickup-token.txt"
    path.write_text("pk_file_token", encoding="utf-8")
    path.chmod(0o644)
    token, source, err = load_clickup_token(token_file=path)
    assert token == ""
    assert source == "file"
    assert err and "600" in err


def test_settings_loads_clickup_from_env(monkeypatch):
    monkeypatch.setenv("CLICKUP_API_TOKEN", "pk_env")
    monkeypatch.setenv("CLICKUP_LIST_ID", "L1")
    s = Settings.from_env()
    assert s.clickup_has_token
    assert s.clickup_configured
    assert s.clickup_token_source == "env"


def test_status_payload_no_secrets():
    c = ClickUpClient(token="pk_test", list_id="L1", transport=FakeTransport())
    payload = c.status_payload()
    assert payload["configured"] is True
    assert "pk_test" not in json.dumps(payload)
    assert payload["token_source"] == "none" or payload["token_source"]


def test_sync_maps_worker_output_to_four_actions():
    t = FakeTransport()
    sync = ClickUpSync(ClickUpClient(token="pk_test", list_id="list123", transport=t), tags=["verification"])
    task = Task.create("research", "Summarize notes", context={"clickup": True})
    res = TaskResult(task.id, "success", output={"summary": "Notes say X."}, duration=2.5, worker="research")
    sync(task, res)
    acts = [a["action"] for a in res.output["clickup"]]
    assert acts == ["create_task", "add_comment", "update_status", "log_time"]


def test_sync_skips_without_token_and_without_flag():
    task = Task.create("research", "x", context={"clickup": True})
    res = TaskResult(task.id, "success", output={"summary": "s"})
    ClickUpSync(ClickUpClient(token="", transport=FakeTransport()))(task, res)
    assert res.output["clickup"]["skipped"] == CONFIGURED_HINT
    t = FakeTransport()
    ClickUpSync(ClickUpClient(token="pk", list_id="list123", transport=t))(Task.create("research", "x"), res)
    assert t.calls == []


def test_worker_create_includes_url_summary():
    t = FakeTransport()
    d = Dispatcher(retry_backoff_s=0.01)
    d.register(ClickUpWorker(ClickUpClient(token="pk", list_id="list123", transport=t)))
    task = Task.create("clickup", "File research", context={"action": "create_task", "name": "TEST verification task"})
    res = d.dispatch(task)
    assert res.ok and res.output["url"]
    assert "https://app.clickup.com" in res.output["summary"]


def test_worker_without_token_fails_fast():
    d = Dispatcher(retry_backoff_s=0.01)
    d.register(ClickUpWorker(ClickUpClient(token="", transport=FakeTransport())))
    res = d.dispatch(Task.create("clickup", "x"))
    assert res.status == "error" and "not configured" in res.errors[0].lower()


def test_numbered_clickup_requires_confirm():
    assert action_requires_confirm({"kind": "clickup", "action": "create_task", "name": "x"})


def test_execute_numbered_clickup_action(tmp_path, monkeypatch):
    from agent.config import AgentConfig
    from agent.loop import Agent
    from agent.store import AgentStore
    from tests.conftest import FakeEmbedder, FakeLLM

    store = AgentStore(tmp_path / "cu.db")
    settings = Settings()
    settings.clickup_api_token = "pk"
    settings.clickup_list_id = "list123"
    settings.clickup_token_source = "env"
    agent = Agent(settings, AgentConfig(db_path=store.db_path), store, FakeEmbedder(), FakeLLM())
    monkeypatch.setattr(
        "agent.clickup.ClickUpClient.from_settings",
        lambda _s: ClickUpClient(token="pk", list_id="list123", transport=FakeTransport()),
    )
    r = execute_numbered_action(
        agent,
        {"kind": "clickup", "action": "create_task", "name": "Hello", "description": "d"},
    )
    assert "Created ClickUp task" in r.reply
    assert "https://app.clickup.com" in r.reply


def test_clickup_api_status_verify(client):
    r = client.get("/api/integrations/clickup/status").json()
    assert "configured" in r and "has_token" in r
    assert "pk_" not in json.dumps(r)
    v = client.post("/api/integrations/clickup/verify").json()
    assert v["ok"] is False or "detail" in v


def test_clickup_e2e_script_skip():
    script = Path(__file__).resolve().parents[1] / "scripts" / "clickup_e2e.py"
    out = subprocess.run(
        [sys.executable, str(script)],
        cwd=str(script.parent.parent),
        capture_output=True,
        text=True,
        timeout=30,
        env={**__import__("os").environ, "CLICKUP_API_TOKEN": "", "CLICKUP_LIST_ID": ""},
    )
    assert out.returncode == 0
    assert "SKIP" in out.stdout


pytest_plugins = ["tests.test_agent_api"]