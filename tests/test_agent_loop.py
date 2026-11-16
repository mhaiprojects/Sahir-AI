from agent.delegation import Dispatcher
from agent.orchestrator import Orchestrator
from tests.test_orchestrator import RecWorker, ScriptedClient, call
from tools import router
from tools.router import RouteResult


def test_respond_persists_and_uses_context(agent, monkeypatch):
    agent.memory.add("User's favourite colour is teal")
    agent.rag.ingest("garden.md", b"The tomatoes are planted in bed three near the fence.")
    seen = {}

    def fake_chat(text, settings, *, history=None, context=None):
        seen["history"], seen["context"] = history, context
        return RouteResult(reply="Bed three.", action="direct_chat", routed=False)

    monkeypatch.setattr(router, "chat_text_only_detailed", fake_chat)
    r1 = agent.respond("Where are the tomatoes planted?", mode="chat")
    assert r1.reply == "Bed three."
    assert "garden.md" in seen["context"]
    r2 = agent.respond("And what colour do I like?", conversation_id=r1.conversation_id, mode="chat")
    assert seen["history"][0]["content"] == "Where are the tomatoes planted?"
    assert "teal" in seen["context"]
    msgs = agent.store.get_messages(r2.conversation_id)
    assert [m["role"] for m in msgs] == ["user", "assistant", "user", "assistant"]
    assert agent.store.get_conversation(r1.conversation_id)["title"].startswith("Where are the tomatoes")


def test_agent_mode_uses_orchestrator(agent):
    d = Dispatcher(retry_backoff_s=0.01)
    d.register(RecWorker("research"))
    agent.orchestrator = Orchestrator(agent.settings, d, client=ScriptedClient([
        call("delegate", type="research", description="look it up"), call("answer", reply="x"),
        {"message": {"content": "Found it."}}]))
    r = agent.respond("research something", mode="agent")
    assert r.reply == "Found it." and r.action == "delegated"
    meta = agent.store.get_messages(r.conversation_id)[-1]["meta"]
    assert meta["tasks"][0]["task"]["type"] == "research"


def test_stream_yields_tokens_and_saves(agent):
    events = list(agent.stream("hello there"))
    assert events[0]["type"] == "meta" and events[-1]["type"] == "done"
    assert "".join(e["text"] for e in events if e["type"] == "token").strip() == "Fake reply."
    assert agent.store.get_messages(events[-1]["conversation_id"])[-1]["content"] == "Fake reply."


def test_auto_session_reuses_recent_conversation(agent, monkeypatch):
    monkeypatch.setattr(router, "chat_text_only_detailed",
                        lambda *a, **k: RouteResult(reply="ok", action="direct_chat", routed=False))
    a = agent.respond("one", mode="chat", source="voice", auto_session=True)
    b = agent.respond("two", mode="chat", source="voice", auto_session=True)
    assert a.conversation_id == b.conversation_id


def test_auto_memory_extraction_background(agent, monkeypatch):
    agent.config.auto_memory = True
    monkeypatch.setattr(router, "chat_text_only_detailed",
                        lambda *a, **k: RouteResult(reply="ok", action="direct_chat", routed=False))
    agent.respond("I love green tea", mode="chat")
    agent.wait_background(5)
    assert [m["content"] for m in agent.store.list_memories(status="pending")] == ["User likes green tea."]
