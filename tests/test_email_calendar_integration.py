"""Email and calendar integrations (mocked wire protocols; no real network)."""

from __future__ import annotations

pytest_plugins = ["tests.test_agent_api"]

import json
from email.message import EmailMessage
from pathlib import Path

import pytest

from agent.calendar_integration import CalendarService, CalendarWorker
from agent.calendar_time import parse_event_window
from agent.config import AgentConfig
from agent.delegation import Dispatcher, Task
from agent.email_integration import EmailError, EmailService, EmailWorker, execute_email_action
from agent.factory import build_agent_stack
from agent.integration_secrets import load_secret, redact
from agent.numbered_replies import action_requires_confirm
from agent.store import AgentStore
from config import Settings
class FakeImap:
    def __init__(self, host: str, port: int) -> None:
        self.host = host
        self.port = port
        self.logged_in = False

    def login(self, user: str, password: str) -> tuple[str, bytes]:
        self.logged_in = True
        if password == "bad":
            raise Exception("AUTHENTICATIONFAILED")
        return "OK", b""

    def select(self, mailbox: str) -> tuple[str, list]:
        return "OK", [1]

    def search(self, charset: str | None, query: str) -> tuple[str, list[bytes]]:
        return "OK", [b"1"]

    def fetch(self, mid: bytes, spec: str) -> tuple[str, list]:
        if b"RFC822" in spec.encode() or "RFC822" in spec:
            msg = EmailMessage()
            msg["From"] = "alice@example.com"
            msg["Subject"] = "Hello"
            msg.set_content("Body text here.")
            return "OK", [(mid, msg.as_bytes())]
        hdr = b"From: alice@example.com\r\nSubject: Hello\r\n\r\n"
        return "OK", [(mid, hdr)]

    def logout(self) -> tuple[str, bytes]:
        return "OK", b""

    def append(self, folder: str, flags: str | None, date: str | None, raw: bytes) -> tuple[str, bytes]:
        return "OK", b""


class FakeSmtp:
    instances: list["FakeSmtp"] = []

    def __init__(self, host: str, port: int) -> None:
        self.host = host
        self.port = port
        self.sent: list[EmailMessage] = []
        FakeSmtp.instances.append(self)

    def starttls(self, **kwargs: object) -> None:
        pass

    def login(self, user: str, password: str) -> None:
        pass

    def send_message(self, msg: EmailMessage, **kwargs: object) -> None:
        self.sent.append(msg)

    def quit(self) -> None:
        pass


def _email_settings(**over) -> Settings:
    s = Settings()
    s.email_imap_host = "imap.test"
    s.email_smtp_host = "smtp.test"
    s.email_username = "user@test"
    s.email_password = "secret123"
    s.email_password_source = "env"
    for k, v in over.items():
        setattr(s, k, v)
    return s


def test_load_secret_rejects_world_readable(tmp_path: Path) -> None:
    p = tmp_path / "pw.txt"
    p.write_text("x", encoding="utf-8")
    p.chmod(0o644)
    secret, source, err = load_secret(env_var="X", file_path=p, label="Test")
    assert not secret and source == "file" and err and "600" in err


def test_redact_password() -> None:
    assert "[REDACTED]" in redact("failed: secret123", "secret123")
    assert "secret123" not in redact("failed: secret123", "secret123")


def test_email_not_configured() -> None:
    svc = EmailService(Settings())
    assert not svc.configured
    with pytest.raises(EmailError, match="not configured"):
        execute_email_action(svc, {"action": "search"})


def test_email_verify_and_search(tmp_path: Path) -> None:
    store = AgentStore(tmp_path / "db.sqlite")
    svc = EmailService(
        _email_settings(),
        store=store,
        imap_factory=FakeImap,
        smtp_factory=FakeSmtp,
    )
    v = svc.verify()
    assert v["ok"] and v["mailbox"] == "INBOX"
    hits = svc.search_messages(unread=True, limit=5)
    assert hits and hits[0]["subject"] == "Hello"


def test_email_draft_and_send_with_mocks(tmp_path: Path) -> None:
    FakeSmtp.instances.clear()
    store = AgentStore(tmp_path / "db.sqlite")
    svc = EmailService(
        _email_settings(),
        store=store,
        imap_factory=FakeImap,
        smtp_factory=FakeSmtp,
    )
    draft = svc.save_draft(to_addrs=["bob@example.com"], subject="Hi", body="Hello Bob")
    out = execute_email_action(svc, {"kind": "email", "action": "send_draft", "draft_id": draft["draft_id"]})
    assert "Sent email" in out["summary"]
    assert FakeSmtp.instances and FakeSmtp.instances[-1].sent


def test_email_send_allowlist(tmp_path: Path) -> None:
    store = AgentStore(tmp_path / "db.sqlite")
    svc = EmailService(
        _email_settings(email_send_allowlist=["allowed@example.com"]),
        store=store,
        imap_factory=FakeImap,
        smtp_factory=FakeSmtp,
    )
    did = svc.save_draft(to_addrs=["other@example.com"], subject="x", body="y")["draft_id"]
    with pytest.raises(EmailError, match="ALLOWLIST"):
        svc.send_draft(did)


def test_email_imap_error_redacted(tmp_path: Path) -> None:
    svc = EmailService(
        _email_settings(email_password="bad"),
        store=AgentStore(tmp_path / "db.sqlite"),
        imap_factory=FakeImap,
    )
    with pytest.raises(EmailError):
        svc.verify()
    assert svc.last_error and "bad" not in svc.last_error


def test_send_requires_confirm() -> None:
    assert action_requires_confirm({"kind": "email", "action": "send_draft", "to": ["a@b.com"], "subject": "s"})
    assert not action_requires_confirm({"kind": "email", "action": "search"})


def test_email_worker_blocks_send() -> None:
    svc = EmailService(_email_settings(), store=None, imap_factory=FakeImap)
    w = EmailWorker(svc)
    from agent.delegation import WorkerError

    with pytest.raises(WorkerError, match="confirmation"):
        w.run(Task.create("email", "send", context={"action": "send_draft", "draft_id": 1}))


def test_calendar_ics_list(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ics = """BEGIN:VCALENDAR
BEGIN:VEVENT
SUMMARY:Standup
DTSTART:20300101T090000
DTEND:20300101T093000
END:VEVENT
END:VCALENDAR
"""

    class Resp:
        def read(self) -> bytes:
            return ics.encode()

        def __enter__(self):
            return self

        def __exit__(self, *a: object) -> None:
            pass

    s = Settings()
    s.calendar_ics_url = "https://example.invalid/cal.ics"
    svc = CalendarService(s, url_opener=lambda url, timeout=15: Resp())
    events = svc.list_upcoming(days=2000)
    assert any(e["title"] == "Standup" for e in events)


def test_calendar_availability_conflicts() -> None:
    from datetime import datetime
    from zoneinfo import ZoneInfo

    s = Settings()
    s.calendar_ics_url = "x"
    svc = CalendarService(s)

    def fake_list(**_):
        return [
            {"title": "Busy", "start": "2030-01-01T09:00:00+11:00", "end": "2030-01-01T10:00:00+11:00"},
        ]

    svc.list_upcoming = fake_list  # type: ignore[method-assign]
    tz = ZoneInfo("Australia/Melbourne")
    start = datetime(2030, 1, 1, 9, 30, tzinfo=tz)
    end = datetime(2030, 1, 1, 10, 30, tzinfo=tz)
    avail = svc.availability(start, end)
    assert not avail["free"] and avail["conflicts"]


def test_parse_time_ambiguous() -> None:
    r = parse_event_window("tomorrow at 3 for an hour", tz_name="Australia/Melbourne")
    assert r.ambiguous and r.clarify


def test_calendar_create_requires_confirm() -> None:
    assert action_requires_confirm({"kind": "calendar", "action": "create_event", "title": "Meet", "when": "tomorrow 3pm"})


def test_calendar_worker_proposes_before_create() -> None:
    s = Settings()
    s.calendar_ics_url = "https://x/cal.ics"
    svc = CalendarService(s)

    def fake_list(**_):
        return []

    svc.list_upcoming = fake_list  # type: ignore[method-assign]
    w = CalendarWorker(svc)
    out = w.run(
        Task.create(
            "calendar",
            "schedule team sync tomorrow at 3pm for an hour",
            context={"action": "create_event", "title": "Team sync", "when": "tomorrow at 3pm for an hour"},
        )
    )
    assert out.get("action") == "propose_create_event"
    assert out.get("numbered_actions")


def test_settings_email_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EMAIL_IMAP_HOST", "imap.x")
    monkeypatch.setenv("EMAIL_SMTP_HOST", "smtp.x")
    monkeypatch.setenv("EMAIL_USERNAME", "u")
    monkeypatch.setenv("EMAIL_PASSWORD", "pw")
    s = Settings.from_env()
    assert s.email_imap_host == "imap.x"
    assert s.email_password == "pw"


def test_api_email_calendar_status(client) -> None:
    r = client.get("/api/integrations/email/status").json()
    assert "configured" in r and "secret123" not in json.dumps(r)
    v = client.post("/api/integrations/email/verify").json()
    assert v["ok"] is False
    c = client.get("/api/integrations/calendar/status").json()
    assert "configured" in c
    cv = client.post("/api/integrations/calendar/verify").json()
    assert cv["ok"] is False


def test_factory_registers_workers(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EMAIL_IMAP_HOST", "")
    cfg = AgentConfig(db_path=tmp_path / "agent.db")
    stack = build_agent_stack(Settings(), cfg)
    assert "email" in stack.dispatcher.workers
    assert "calendar" in stack.dispatcher.workers