"""ClickUp integration with a mocked HTTP layer (no real API calls, no token needed)."""

import pytest

from agent.clickup import ClickUpClient, ClickUpError, ClickUpSync, ClickUpWorker
from agent.delegation import Dispatcher, Task, TaskResult, WorkerError


class FakeResp:
    def __init__(self, status=200, data=None):
        self.status_code = status
        self._data = data or {}
        self.content = b"{}"
        self.text = str(self._data)

    def json(self):
        return self._data


class FakeTransport:
    def __init__(self):
        self.calls = []

    def __call__(self, method, url, headers=None, json=None, timeout=None):
        self.calls.append({"method": method, "url": url, "headers": headers, "json": json})
        if url.endswith("/team"):
            return FakeResp(data={"teams": [{"id": "9001"}]})
        if method == "POST" and url.endswith("/task") and "/list/" in url:
            return FakeResp(data={"id": "abc123", "url": "https://app.clickup.com/t/abc123"})
        return FakeResp(data={})


def test_requires_token():
    c = ClickUpClient(token="", transport=FakeTransport())
    assert not c.configured
    with pytest.raises(ClickUpError):
        c.create_task("x")


def test_actions_hit_correct_endpoints():
    t = FakeTransport()
    c = ClickUpClient(token="pk_test", list_id="list123", transport=t)
    assert c.create_task("Test", description="d", tags=["verification"])["id"] == "abc123"
    c.update_status("abc123", "complete")
    c.add_comment("abc123", "hello")
    c.log_time("abc123", 5000, description="work")
    urls = [(x["method"], x["url"].split("/api/v2")[1]) for x in t.calls]
    assert urls == [
        ("POST", "/list/list123/task"),
        ("PUT", "/task/abc123"),
        ("POST", "/task/abc123/comment"),
        ("GET", "/team"),
        ("POST", "/team/9001/time_entries"),
    ]
    assert t.calls[0]["headers"]["Authorization"] == "pk_test"
    assert t.calls[0]["json"]["tags"] == ["verification"]
    assert t.calls[-1]["json"]["tid"] == "abc123" and t.calls[-1]["json"]["duration"] == 5000


def test_http_error_raises_without_leaking_token():
    c = ClickUpClient(token="pk_secret", transport=lambda *a, **k: FakeResp(401, {"err": "Token invalid"}))
    with pytest.raises(ClickUpError) as e:
        c.add_comment("t", "x")
    assert "pk_secret" not in str(e.value)


def test_sync_maps_worker_output_to_four_actions():
    t = FakeTransport()
    sync = ClickUpSync(ClickUpClient(token="pk_test", list_id="list123", transport=t), tags=["verification"])
    task = Task.create("research", "Summarize notes", context={"clickup": True})
    res = TaskResult(task.id, "success", output={"summary": "Notes say X."}, duration=2.5, worker="research")
    sync(task, res)
    acts = [a["action"] for a in res.output["clickup"]]
    assert acts == ["create_task", "add_comment", "update_status", "log_time"]
    comment = next(c for c in t.calls if c["url"].endswith("/comment"))
    assert "Notes say X." in comment["json"]["comment_text"]


def test_sync_skips_without_token_and_without_flag():
    task = Task.create("research", "x", context={"clickup": True})
    res = TaskResult(task.id, "success", output={"summary": "s"})
    ClickUpSync(ClickUpClient(token="", transport=FakeTransport()))(task, res)
    assert res.output["clickup"] == {"skipped": "CLICKUP_API_TOKEN not set"}
    t = FakeTransport()
    ClickUpSync(ClickUpClient(token="pk", list_id="list123", transport=t))(Task.create("research", "x"), res)
    assert t.calls == []


def test_worker_via_dispatcher_uses_previous_results():
    t = FakeTransport()
    d = Dispatcher(retry_backoff_s=0.01)
    d.register(ClickUpWorker(ClickUpClient(token="pk", list_id="list123", transport=t)))
    task = Task.create("clickup", "File research", context={
        "action": "create_task", "name": "TEST verification task", "tags": ["verification"],
        "previous_results": [{"type": "research", "summary": "Research said Y."}]})
    res = d.dispatch(task)
    assert res.ok and res.output["task_id"] == "abc123"
    assert "Research said Y." in t.calls[0]["json"]["markdown_description"]
    assert "verification" in t.calls[0]["json"]["tags"]


def test_worker_without_token_fails_fast():
    d = Dispatcher(retry_backoff_s=0.01)
    d.register(ClickUpWorker(ClickUpClient(token="", transport=FakeTransport())))
    res = d.dispatch(Task.create("clickup", "x"))
    assert res.status == "error" and res.attempts == 1 and "CLICKUP_API_TOKEN" in res.errors[0]
