"""Orchestrator decisions with a scripted fake Ollama client (no model needed)."""

from agent.delegation import Dispatcher
from agent.orchestrator import Orchestrator
from config import Settings


class ScriptedClient:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def chat(self, model, messages, tools=None, options=None):
        self.calls.append({"messages": list(messages), "tools": tools})
        return self.responses.pop(0) if self.responses else {"message": {"content": "Done."}}


def call(name, **args):
    return {"message": {"content": "", "tool_calls": [{"function": {"name": name, "arguments": args}}]}}


class RecWorker:
    def __init__(self, type_):
        self.type = type_
        self.description = f"{type_} worker"
        self.timeout_s = 5
        self.max_retries = 0
        self.tasks = []

    def run(self, task):
        self.tasks.append(task)
        return {"summary": f"{self.type} result for: {task.description}"}


def make(responses, *types):
    d = Dispatcher(retry_backoff_s=0.01)
    workers = {t: RecWorker(t) for t in types}
    for w in workers.values():
        d.register(w)
    client = ScriptedClient(responses)
    return Orchestrator(Settings(), d, client=client), workers, client


def test_direct_answer():
    o, w, _ = make([call("answer", reply="Paris.")], "research")
    r = o.run("Capital of France?", history=[])
    assert r.reply == "Paris." and r.action == "direct_answer" and not w["research"].tasks


def test_delegate_and_inject_result():
    o, w, client = make([call("delegate", type="research", description="Summarize my notes on X"),
                         call("answer", reply="ignored"), {"message": {"content": "Your notes say X is great."}}],
                        "research")
    history = [{"role": "user", "content": "I'm working on project X"}, {"role": "assistant", "content": "Cool"}]
    r = o.run("Summarize my notes", history=history)
    assert r.action == "delegated" and r.reply == "Your notes say X is great."
    task = w["research"].tasks[0]
    assert task.context["history"][0]["content"] == "I'm working on project X"  # context preserved
    tool_msgs = [m for m in client.calls[1]["messages"] if m["role"] == "tool"]
    assert tool_msgs and "research result" in tool_msgs[0]["content"]  # result injected back


def test_multi_worker_chain_keeps_context():
    o, w, _ = make([
        call("delegate", type="research", description="Research A"),
        call("delegate", type="clickup", description="File it", clickup_action="create_task"),
        call("answer", reply="x"), {"message": {"content": "Researched and filed."}},
    ], "research", "clickup")
    r = o.run("research A and file it in clickup", history=[{"role": "user", "content": "context turn"}])
    assert r.action_details["workers"] == ["research", "clickup"]
    ck = w["clickup"].tasks[0]
    assert ck.context["previous_results"][0]["summary"].startswith("research result")
    assert ck.context["history"][0]["content"] == "context turn"
    assert ck.context["action"] == "create_task"


def test_heuristic_fallback_when_model_just_answers():
    o, w, _ = make([call("answer", reply="I can't read files."), {"message": {"content": "Here is the summary."}}],
                   "research")
    r = o.run("Please summarize my notes about the garden", history=[])
    assert w["research"].tasks and r.action == "delegated"


def test_same_round_consumer_runs_after_producer():
    o, w, _ = make([
        {"message": {"content": "", "tool_calls": [
            {"function": {"name": "delegate", "arguments": {"type": "clickup", "description": "File it"}}},
            {"function": {"name": "delegate", "arguments": {"type": "research", "description": "Research B"}}}]}},
        call("answer", reply="ok"),
    ], "research", "clickup")
    w["clickup"].consumes_results = True
    r = o.run("research B and file it", history=[{"role": "user", "content": "h"}])
    assert r.action_details["workers"] == ["research", "clickup"]
    assert w["clickup"].tasks[0].context["previous_results"][0]["type"] == "research"
