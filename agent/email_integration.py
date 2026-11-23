"""Email via IMAP (read/search) and SMTP (send). Stdlib only for wire protocol."""

from __future__ import annotations

import email
import imaplib
import json
import logging
import re
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import parseaddr
from typing import Any, Callable

from agent.delegation import Task, WorkerError
from agent.integration_secrets import load_secret, redact
from agent.store import AgentStore

logger = logging.getLogger(__name__)

CONFIGURED_HINT = (
    "Email is not configured. Set EMAIL_IMAP_HOST, EMAIL_SMTP_HOST, EMAIL_USERNAME, "
    "and EMAIL_PASSWORD or EMAIL_PASSWORD_FILE (see docs/integrations/email.md)."
)

ImapFactory = Callable[..., Any]
SmtpFactory = Callable[..., Any]


class EmailError(RuntimeError):
    pass


class EmailService:
    def __init__(
        self,
        settings: Any,
        *,
        store: AgentStore | None = None,
        imap_factory: ImapFactory | None = None,
        smtp_factory: SmtpFactory | None = None,
        llm: Any | None = None,
    ) -> None:
        self.settings = settings
        self.store = store
        self._imap_factory = imap_factory or imaplib.IMAP4_SSL
        self._smtp_factory = smtp_factory
        self.llm = llm
        self.last_error: str | None = getattr(settings, "email_password_load_error", None)
        self.last_ok_at: float | None = None

    @classmethod
    def from_settings(
        cls,
        settings: Any,
        *,
        store: AgentStore | None = None,
        llm: Any | None = None,
        imap_factory: ImapFactory | None = None,
        smtp_factory: SmtpFactory | None = None,
    ) -> EmailService:
        return cls(
            settings,
            store=store,
            llm=llm,
            imap_factory=imap_factory,
            smtp_factory=smtp_factory,
        )

    @property
    def password(self) -> str:
        return getattr(self.settings, "email_password", "") or ""

    @property
    def configured(self) -> bool:
        s = self.settings
        return bool(
            getattr(s, "email_imap_host", "")
            and getattr(s, "email_smtp_host", "")
            and getattr(s, "email_username", "")
            and self.password
            and not getattr(s, "email_password_load_error", None)
        )

    def status_payload(self) -> dict[str, Any]:
        s = self.settings
        return {
            "configured": self.configured,
            "has_password": bool(self.password) and not getattr(s, "email_password_load_error", None),
            "imap_host_set": bool(getattr(s, "email_imap_host", "")),
            "smtp_host_set": bool(getattr(s, "email_smtp_host", "")),
            "username_set": bool(getattr(s, "email_username", "")),
            "password_source": getattr(s, "email_password_source", "none"),
            "send_allowlist": getattr(s, "email_send_allowlist", []) or [],
            "last_error": self.last_error,
            "last_ok_at": self.last_ok_at,
            "needs_credentials_message": (
                None
                if self.configured
                else "Needs credentials: set EMAIL_IMAP_HOST, EMAIL_SMTP_HOST, EMAIL_USERNAME, EMAIL_PASSWORD (see docs/integrations/email.md)"
            ),
        }

    def _connect_imap(self) -> Any:
        if not self.configured:
            raise EmailError(CONFIGURED_HINT)
        host = self.settings.email_imap_host
        port = int(getattr(self.settings, "email_imap_port", 993) or 993)
        try:
            imap = self._imap_factory(host, port)
            imap.login(self.settings.email_username, self.password)
            return imap
        except Exception as exc:
            msg = redact(str(exc), self.password)
            self.last_error = f"IMAP login failed: {msg}"
            raise EmailError(self.last_error) from exc

    def verify(self) -> dict[str, Any]:
        imap = self._connect_imap()
        try:
            imap.select("INBOX")
            self.last_ok_at = __import__("time").time()
            self.last_error = None
            return {"ok": True, "mailbox": "INBOX"}
        finally:
            try:
                imap.logout()
            except Exception:
                pass

    def search_messages(
        self,
        *,
        sender: str | None = None,
        subject: str | None = None,
        unread: bool = False,
        since: str | None = None,
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        imap = self._connect_imap()
        try:
            imap.select("INBOX")
            criteria = []
            if unread:
                criteria.append("UNSEEN")
            if since:
                criteria.append(f'SINCE {since}')
            if sender:
                criteria.append(f'FROM "{sender}"')
            if subject:
                criteria.append(f'SUBJECT "{subject}"')
            query = " ".join(criteria) if criteria else "ALL"
            _typ, data = imap.search(None, query)
            ids = (data[0] or b"").split()
            ids = ids[-limit:] if len(ids) > limit else ids
            out: list[dict[str, Any]] = []
            for mid in reversed(ids):
                _t, msg_data = imap.fetch(mid, "(RFC822.HEADER)")
                if not msg_data or not msg_data[0]:
                    continue
                raw = msg_data[0][1]
                msg = email.message_from_bytes(raw)
                subj = str(email.header.make_header(email.header.decode_header(msg.get("Subject", ""))))
                frm = msg.get("From", "")
                out.append({"uid": mid.decode(), "from": frm, "subject": subj})
            self.last_ok_at = __import__("time").time()
            return out
        finally:
            imap.logout()

    @staticmethod
    def _plain_body(msg: email.message.Message, limit: int = 4000) -> str:
        if msg.is_multipart():
            for part in msg.walk():
                if part.get_content_type() == "text/plain":
                    payload = part.get_payload(decode=True) or b""
                    return payload.decode(errors="replace")[:limit]
            return ""
        payload = msg.get_payload(decode=True) or b""
        return payload.decode(errors="replace")[:limit]

    def read_message(self, uid: str) -> dict[str, Any]:
        imap = self._connect_imap()
        try:
            imap.select("INBOX")
            _t, msg_data = imap.fetch(uid.encode(), "(RFC822)")
            if not msg_data or not msg_data[0]:
                raise EmailError("Message not found")
            msg = email.message_from_bytes(msg_data[0][1])
            subj = str(email.header.make_header(email.header.decode_header(msg.get("Subject", ""))))
            body = self._plain_body(msg)
            return {"uid": uid, "from": msg.get("From", ""), "subject": subj, "body": body}
        finally:
            imap.logout()

    def save_draft(
        self,
        *,
        to_addrs: list[str],
        subject: str,
        body: str,
        cc_addrs: list[str] | None = None,
        reply_to_uid: str | None = None,
    ) -> dict[str, Any]:
        if not self.store:
            raise EmailError("Draft storage unavailable")
        draft_id = self.store.save_email_draft(
            to_addrs=to_addrs,
            cc_addrs=cc_addrs or [],
            subject=subject,
            body=body,
            reply_to_uid=reply_to_uid,
        )
        if getattr(self.settings, "email_imap_drafts_folder", ""):
            try:
                self._append_imap_draft(to_addrs, cc_addrs or [], subject, body)
            except EmailError as exc:
                logger.warning("IMAP Drafts append skipped: %s", exc)
        return {"draft_id": draft_id, "to": to_addrs, "subject": subject}

    def _append_imap_draft(self, to_addrs, cc_addrs, subject, body) -> None:
        folder = self.settings.email_imap_drafts_folder.strip()
        if not folder:
            return
        msg = EmailMessage()
        msg["From"] = self.settings.email_from or self.settings.email_username
        msg["To"] = ", ".join(to_addrs)
        if cc_addrs:
            msg["Cc"] = ", ".join(cc_addrs)
        msg["Subject"] = subject
        msg.set_content(body)
        imap = self._connect_imap()
        try:
            imap.append(folder, None, None, msg.as_bytes())
        finally:
            imap.logout()

    def _check_allowlist(self, recipients: list[str]) -> None:
        allow = getattr(self.settings, "email_send_allowlist", []) or []
        if not allow:
            return
        normalized = {a.lower() for a in allow}
        for raw in recipients:
            _name, addr = parseaddr(raw)
            if not addr:
                addr = raw
            if addr.lower() not in normalized:
                raise EmailError(f"Recipient {addr} is not in EMAIL_SEND_ALLOWLIST")

    def send_draft(self, draft_id: int) -> dict[str, Any]:
        if not self.store:
            raise EmailError("Draft storage unavailable")
        draft = self.store.get_email_draft(draft_id)
        if not draft:
            raise EmailError(f"Draft {draft_id} not found")
        to_addrs = json.loads(draft["to_addrs"])
        cc_addrs = json.loads(draft.get("cc_addrs") or "[]")
        self._check_allowlist(to_addrs + cc_addrs)
        self._smtp_send(to_addrs, cc_addrs, draft["subject"], draft["body"])
        self.store.delete_email_draft(draft_id)
        summary = f"Sent email to {', '.join(to_addrs)} — subject “{draft['subject']}”"
        return {"action": "send_draft", "draft_id": draft_id, "summary": summary, "reply": summary}

    def _smtp_send(self, to_addrs: list[str], cc_addrs: list[str], subject: str, body: str) -> None:
        host = self.settings.email_smtp_host
        port = int(getattr(self.settings, "email_smtp_port", 587) or 587)
        use_ssl = getattr(self.settings, "email_smtp_ssl", False)
        msg = EmailMessage()
        msg["From"] = self.settings.email_from or self.settings.email_username
        msg["To"] = ", ".join(to_addrs)
        if cc_addrs:
            msg["Cc"] = ", ".join(cc_addrs)
        msg["Subject"] = subject
        msg.set_content(body)
        recipients = to_addrs + cc_addrs
        factory = self._smtp_factory
        try:
            if factory:
                smtp = factory(host, port)
            elif use_ssl:
                smtp = smtplib.SMTP_SSL(host, port, timeout=20)
            else:
                smtp = smtplib.SMTP(host, port, timeout=20)
                smtp.starttls(context=ssl.create_default_context())
            smtp.login(self.settings.email_username, self.password)
            smtp.send_message(msg, from_addr=msg["From"], to_addrs=recipients)
            smtp.quit()
            self.last_ok_at = __import__("time").time()
            self.last_error = None
        except Exception as exc:
            msg_err = redact(str(exc), self.password)
            self.last_error = f"SMTP send failed: {msg_err}"
            raise EmailError(self.last_error) from exc

    def summarize_recent(self, *, unread_only: bool = True, limit: int = 8) -> dict[str, Any]:
        if not self.llm:
            raise EmailError("LLM not available for summarise")
        msgs = self.search_messages(unread=unread_only, limit=limit)
        if not msgs:
            return {"summary": "No matching messages.", "messages": []}
        bodies = []
        for m in msgs[:limit]:
            try:
                full = self.read_message(m["uid"])
                bodies.append(f"From: {full['from']}\nSubject: {full['subject']}\n{full['body'][:1200]}")
            except EmailError:
                continue
        prompt = "Summarise these emails in a short numbered list (who, subject, one line each):\n\n" + "\n---\n".join(bodies)
        resp = self.llm.chat(
            [{"role": "user", "content": prompt}],
            options={"temperature": 0.2, "num_predict": 400},
        )
        summary = (resp.get("content") or "").strip() or "Could not summarise."
        return {"summary": summary, "messages": msgs, "reply": summary}


def execute_email_action(service: EmailService, action: dict[str, Any], *, llm: Any | None = None) -> dict[str, Any]:
    if not service.configured and action.get("action") != "status":
        raise EmailError(CONFIGURED_HINT)
    kind = str(action.get("action") or "search")
    if kind == "send_draft":
        did = int(action.get("draft_id") or 0)
        return service.send_draft(did)
    if kind == "search":
        hits = service.search_messages(
            sender=action.get("sender"),
            subject=action.get("subject"),
            unread=bool(action.get("unread")),
            since=action.get("since"),
            limit=int(action.get("limit") or 10),
        )
        lines = [f"{h['uid']}: {h['subject']}" for h in hits]
        reply = "Mail search:\n" + "\n".join(lines) if lines else "No messages matched."
        return {"action": kind, "results": hits, "reply": reply, "summary": reply}
    if kind == "read":
        uid = str(action.get("uid") or action.get("message_id") or "")
        msg = service.read_message(uid)
        reply = f"From: {msg['from']}\nSubject: {msg['subject']}\n\n{msg['body'][:2000]}"
        return {"action": kind, **msg, "reply": reply, "summary": reply[:500]}
    if kind == "summarize":
        svc = service
        if llm:
            svc = EmailService(service.settings, store=service.store, llm=llm,
                               imap_factory=service._imap_factory, smtp_factory=service._smtp_factory)
        return svc.summarize_recent(unread_only=bool(action.get("unread", True)))
    if kind in {"draft_reply", "draft_new"}:
        to_addrs = list(action.get("to") or action.get("to_addrs") or [])
        if not to_addrs and kind == "draft_reply":
            to_addrs = [str(action.get("reply_to") or "")]
        subj = str(action.get("subject") or "Re:")
        body = str(action.get("body") or action.get("text") or "")
        draft = service.save_draft(
            to_addrs=[a for a in to_addrs if a],
            subject=subj,
            body=body,
            cc_addrs=list(action.get("cc") or []),
            reply_to_uid=action.get("reply_to_uid"),
        )
        reply = f"Draft saved (id {draft['draft_id']}) to {', '.join(draft['to'])} — subject “{subj}”"
        return {
            "action": kind,
            **draft,
            "reply": reply,
            "summary": reply,
            "numbered_actions": [{
                "kind": "email",
                "action": "send_draft",
                "draft_id": draft["draft_id"],
                "to": draft["to"],
                "subject": subj,
            }],
        }
    raise EmailError(f"Unknown email action {kind!r}")


class EmailWorker:
    type = "email"
    description = "Read, search, summarise and draft email (IMAP/SMTP)."
    timeout_s = 90.0
    max_retries = 1

    def __init__(self, service: EmailService, *, llm: Any | None = None) -> None:
        self.service = service
        self.llm = llm

    def run(self, task: Task) -> dict[str, Any]:
        if not self.service.configured:
            raise WorkerError(CONFIGURED_HINT)
        ctx = task.context
        action = ctx.get("action") or self._infer_action(task.description)
        act = {
            "action": action,
            "sender": ctx.get("sender"),
            "subject": ctx.get("subject"),
            "unread": ctx.get("unread", "unread" in task.description.lower()),
            "uid": ctx.get("uid") or ctx.get("message_id"),
            "to": ctx.get("to"),
            "body": ctx.get("body") or ctx.get("text"),
            "limit": ctx.get("limit") or 10,
        }
        if action == "send_draft":
            raise WorkerError("Sending email requires confirmation — use the send draft action chip or voice confirm.")
        return execute_email_action(self.service, act, llm=self.llm)

    @staticmethod
    def _infer_action(text: str) -> str:
        low = text.lower()
        if "draft" in low or "compose" in low or "write" in low:
            return "draft_new" if "new" in low or "compose" in low else "draft_reply"
        if "summar" in low or "unread" in low:
            return "summarize"
        if "read" in low and ("message" in low or "email" in low):
            return "read"
        return "search"