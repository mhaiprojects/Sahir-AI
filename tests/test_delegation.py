import time

from agent.delegation import Dispatcher, Task, TaskResult, WorkerError


class EchoWorker:
    type = "echo"
    description = "echo"
    timeout_s = 2.0
    max_retries = 0

    def run(self, task):
        return {"summary": f"echo:{task.description}", "ctx": task.context.get("previous_results")}


class FlakyWorker:
    type = "flaky"
    description = "fails once"
    timeout_s = 2.0
    max_retries = 2

    def __init__(self):
        self.n = 0

    def run(self, task):
        self.n += 1
        if self.n < 2:
            raise RuntimeError("transient")
        return "ok"


class SlowWorker:
    type = "slow"
    description = "slow"
    timeout_s = 0.2
    max_retries = 1

    def run(self, task):
        time.sleep(1.0)
        return "late"


class FatalWorker:
    type = "fatal"
    description = "fatal"
    timeout_s = 1
    max_retries = 3

    def __init__(self):
        self.n = 0

    def run(self, task):
        self.n += 1
        raise WorkerError("bad input")


def _d(store=None):
    d = Dispatcher(store, retry_backoff_s=0.01)
    for w in (EchoWorker(), FlakyWorker(), SlowWorker()):
        d.register(w)
    return d


def test_task_schema_fields():
    t = Task.create("echo", " hi ", priority="high", callback="cb", context={"a": 1})
    assert set(t.to_dict()) == {"id", "type", "description", "context", "priority", "callback"}
    assert t.priority == 2 and t.description == "hi"
    r = TaskResult(t.id, "success", output=1)
    assert {"task_id", "status", "output", "errors", "duration"} <= set(r.to_dict())


def test_dispatch_success_and_persistence(store):
    d = _d(store)
    res = d.dispatch(Task.create("echo", "x", context={"conversation_id": 7}))
    assert res.ok and res.output["summary"] == "echo:x" and res.attempts == 1
    rows = d.list_tasks(conversation_id=7)
    assert rows[0]["status"] == "success" and rows[0]["output"]["summary"] == "echo:x"


def test_retry_then_success():
    d = _d()
    res = d.dispatch(Task.create("flaky", "x"))
    assert res.ok and res.attempts == 2 and "transient" in res.errors[0]


def test_timeout_with_retry():
    d = _d()
    res = d.dispatch(Task.create("slow", "x"))
    assert res.status == "timeout" and res.attempts == 2 and len(res.errors) == 2


def test_worker_error_not_retried():
    d = Dispatcher(retry_backoff_s=0.01)
    w = FatalWorker()
    d.register(w)
    res = d.dispatch(Task.create("fatal", "x"))
    assert res.status == "error" and w.n == 1


def test_unknown_worker():
    assert _d().dispatch(Task.create("nope", "x")).status == "error"


def test_queue_transport_priority_and_callback():
    d = Dispatcher(queue_workers=1, retry_backoff_s=0.01)
    d.register(EchoWorker())
    seen = []
    d.register_callback("cb", lambda t, r: seen.append((t.id, r.status)))
    futs = [d.submit(Task.create("echo", f"t{i}", priority="low" if i else "urgent", callback="cb")) for i in range(3)]
    results = [f.result(timeout=5) for f in futs]
    assert all(r.ok for r in results) and len(seen) == 3


def test_result_hook_runs():
    d = _d()
    hooked = []
    d.result_hooks.append(lambda t, r: hooked.append(t.type))
    d.dispatch_many([Task.create("echo", "a"), Task.create("echo", "b")])
    assert hooked == ["echo", "echo"]
