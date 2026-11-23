"""Orchestrator decisions with a scripted fake Ollama client (no model needed)."""

import pytest

from agent.delegation import Dispatcher
from agent.orchestrator import Orchestrator, heuristic_calendar_intent, heuristic_email_intent
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


def test_cursor_cli_query_delegates_to_code_worker():
    o, w, _ = make([
        call("answer", reply="I'm not connected to the cursor CLI on your PC."),
        {"message": {"content": "Last task was the voice assistant UI."}},
    ], "code")
    r = o.run("what was the last task ran in cursror CLI", history=[])
    assert w["code"].tasks and r.action == "delegated"


@pytest.mark.parametrize(
    "text,expected",
    [
        ("check my inbox for unread mail", "email"),
        ("what's in my email from Alice", "email"),
        ("draft an email to bob@example.com", "email"),
        ("reply to Sarah's email", "email"),
        ("what's on my calendar tomorrow", "calendar"),
        ("am I free at 3pm tomorrow", "calendar"),
        ("book a meeting with the team Friday", "calendar"),
        ("create an appointment for the dentist", "calendar"),
        ("check my calendar this week", "calendar"),
        ("summarise my unread email", "email"),
        ("summarize my inbox", "email"),
        ("give me a summary of today's emails", "email"),
        ("summarise my calendar this week", "calendar"),
    ],
)
def test_integration_heuristic_positive_routes(text, expected):
    o, w, _ = make(
        [call("answer", reply="I cannot do that."), {"message": {"content": "Done."}}],
        "research",
        "email",
        "calendar",
        "code",
    )
    o.run(text, history=[])
    assert w[expected].tasks, f"expected {expected} for: {text!r}"
    assert heuristic_email_intent(text) == (expected == "email")
    assert heuristic_calendar_intent(text) == (expected == "calendar")


@pytest.mark.parametrize(
    "text",
    [
        "how do I schedule a cron job",
        "summarise the meeting notes in my docs",
        "check GPU availability",
        "write a Python function that validates an email address",
        "email regex for RFC 5322",
        "what is a meeting in parliamentary procedure",
        "schedule variable in cron syntax",
    ],
)
def test_integration_heuristic_negative_no_email_calendar(text):
    o, w, _ = make(
        [call("answer", reply="Here is general info."), {"message": {"content": "Done."}}],
        "research",
        "email",
        "calendar",
        "code",
    )
    o.run(text, history=[])
    assert not w["email"].tasks, text
    assert not w["calendar"].tasks, text
    assert not (heuristic_email_intent(text) and heuristic_calendar_intent(text))


def test_summarise_unread_email_intent_not_blocked():
    assert heuristic_email_intent("summarise my unread email") is True


def test_meeting_notes_in_docs_prefers_research():
    o, w, _ = make(
        [call("answer", reply="I can't read files."), {"message": {"content": "Summary."}}],
        "research",
        "email",
        "calendar",
    )
    text = "summarise the meeting notes in my docs"
    o.run(text, history=[])
    assert w["research"].tasks
    assert not w["calendar"].tasks
    assert not w["email"].tasks
    assert not heuristic_email_intent(text)


@pytest.mark.parametrize(
    "text",
    [
        "summarise my unread email",
        "summarize my inbox",
        "give me a summary of today's emails",
    ],
)
def test_mail_summarize_routes_email_worker(text):
    o, w, _ = make(
        [call("answer", reply="Cannot."), {"message": {"content": "Done."}}],
        "research",
        "email",
        "calendar",
    )
    o.run(text, history=[])
    assert w["email"].tasks
    assert not w["research"].tasks
    task = w["email"].tasks[0]
    assert task.context.get("action") == "summarize"


def test_calendar_summarize_routes_calendar_not_research():
    o, w, _ = make(
        [call("answer", reply="Cannot."), {"message": {"content": "Done."}}],
        "research",
        "email",
        "calendar",
    )
    text = "summarise my calendar this week"
    o.run(text, history=[])
    assert w["calendar"].tasks
    assert not w["research"].tasks
    assert not w["email"].tasks


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
