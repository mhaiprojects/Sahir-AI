"""Calendar: CalDAV (optional) + read-only ICS URL."""

from __future__ import annotations

import logging
import re
import time
import urllib.request
from datetime import datetime, timedelta
from typing import Any, Callable
from zoneinfo import ZoneInfo

from agent.calendar_time import TimeParseResult, parse_event_window
from agent.delegation import Task, WorkerError
from agent.integration_secrets import load_secret, redact

logger = logging.getLogger(__name__)

CONFIGURED_HINT = (
    "Calendar is not configured. Set CALENDAR_CALDAV_URL + credentials, or CALENDAR_ICS_URL "
    "(see docs/integrations/calendar.md)."
)

UrlOpener = Callable[..., Any]

try:
    import caldav  # type: ignore

    _HAS_CALDAV = True
except ImportError:
    caldav = None  # type: ignore
    _HAS_CALDAV = False


class CalendarError(RuntimeError):
    pass


def _parse_ics_events(text: str, *, tz: ZoneInfo, horizon_days: int = 14) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    now = datetime.now(tz)
    end_horizon = now + timedelta(days=horizon_days)
    for block in re.split(r"BEGIN:VEVENT", text)[1:]:
        chunk = block.split("END:VEVENT", 1)[0]
        summary_m = re.search(r"^SUMMARY:(.+)$", chunk, re.M)
        dtstart_m = re.search(r"^DTSTART[^:]*:(.+)$", chunk, re.M)
        dtend_m = re.search(r"^DTEND[^:]*:(.+)$", chunk, re.M)
        if not summary_m or not dtstart_m:
            continue
        title = summary_m.group(1).strip()
        start = _ics_dt(dtstart_m.group(1).strip(), tz)
        end = _ics_dt(dtend_m.group(1).strip(), tz) if dtend_m else start + timedelta(hours=1)
        if start and now <= start <= end_horizon:
            events.append({"title": title, "start": start.isoformat(), "end": end.isoformat()})
    events.sort(key=lambda e: e["start"])
    return events


def _ics_dt(raw: str, tz: ZoneInfo) -> datetime | None:
    raw = raw.strip().replace("Z", "")
    if "T" in raw:
        token = raw.split("T", 1)[0] + "T" + raw.split("T", 1)[1][:6]
        for fmt in ("%Y%m%dT%H%M%S",):
            try:
                dt = datetime.strptime(token, fmt)
                return dt.replace(tzinfo=tz)
            except ValueError:
                break
    for fmt in ("%Y%m%d",):
        try:
            dt = datetime.strptime(raw[:8], fmt)
            return dt.replace(tzinfo=tz)
        except ValueError:
            pass
    return None


class CalendarService:
    def __init__(self, settings: Any, *, url_opener: UrlOpener | None = None) -> None:
        self.settings = settings
        self._url_opener = url_opener or urllib.request.urlopen
        self.last_error: str | None = getattr(settings, "calendar_password_load_error", None)
        self.last_ok_at: float | None = None

    @property
    def password(self) -> str:
        return getattr(self.settings, "calendar_password", "") or ""

    @property
    def tz_name(self) -> str:
        return getattr(self.settings, "calendar_timezone", "Australia/Melbourne") or "Australia/Melbourne"

    @property
    def caldav_configured(self) -> bool:
        return bool(
            _HAS_CALDAV
            and getattr(self.settings, "calendar_caldav_url", "")
            and getattr(self.settings, "calendar_username", "")
            and self.password
            and not getattr(self.settings, "calendar_password_load_error", None)
        )

    @property
    def ics_configured(self) -> bool:
        return bool(getattr(self.settings, "calendar_ics_url", ""))

    @property
    def configured(self) -> bool:
        return self.caldav_configured or self.ics_configured

    @property
    def write_enabled(self) -> bool:
        return self.caldav_configured

    def status_payload(self) -> dict[str, Any]:
        return {
            "configured": self.configured,
            "caldav_available": _HAS_CALDAV,
            "caldav_configured": self.caldav_configured,
            "ics_configured": self.ics_configured,
            "write_enabled": self.write_enabled,
            "calendar_name": getattr(self.settings, "calendar_name", "") or "",
            "timezone": self.tz_name,
            "password_source": getattr(self.settings, "calendar_password_source", "none"),
            "last_error": self.last_error,
            "last_ok_at": self.last_ok_at,
            "needs_credentials_message": (
                None
                if self.configured
                else "Needs credentials: CALENDAR_CALDAV_URL + CALENDAR_USERNAME + password, or CALENDAR_ICS_URL (see docs/integrations/calendar.md)"
            ),
        }

    def _caldav_client(self) -> Any:
        if not self.caldav_configured:
            raise CalendarError(CONFIGURED_HINT if not _HAS_CALDAV else "CalDAV credentials incomplete")
        try:
            client = caldav.DAVClient(
                url=self.settings.calendar_caldav_url,
                username=self.settings.calendar_username,
                password=self.password,
            )
            return client
        except Exception as exc:
            msg = redact(str(exc), self.password)
            self.last_error = msg
            raise CalendarError(msg) from exc

    def verify(self) -> dict[str, Any]:
        if self.caldav_configured:
            client = self._caldav_client()
            principal = client.principal()
            _ = principal.calendars()
            self.last_ok_at = time.time()
            self.last_error = None
            return {"ok": True, "mode": "caldav", "calendars": len(_)}
        if self.ics_configured:
            self._fetch_ics()
            self.last_ok_at = time.time()
            return {"ok": True, "mode": "ics"}
        raise CalendarError(CONFIGURED_HINT)

    def _fetch_ics(self) -> str:
        url = self.settings.calendar_ics_url
        try:
            with self._url_opener(url, timeout=15) as resp:
                return resp.read().decode(errors="replace")
        except Exception as exc:
            self.last_error = str(exc)
            raise CalendarError(f"Could not fetch ICS: {exc}") from exc

    def list_upcoming(self, *, days: int = 7) -> list[dict[str, Any]]:
        tz = ZoneInfo(self.tz_name)
        if self.ics_configured and not self.caldav_configured:
            events = _parse_ics_events(self._fetch_ics(), tz=tz, horizon_days=days)
        else:
            client = self._caldav_client()
            cal_name = (getattr(self.settings, "calendar_name", "") or "").strip()
            cals = client.principal().calendars()
            target = cals[0]
            if cal_name:
                for c in cals:
                    if cal_name.lower() in (getattr(c, "name", "") or "").lower():
                        target = c
                        break
            now = datetime.now(tz)
            end = now + timedelta(days=days)
            found = target.date_search(start=now, end=end)
            events = []
            for ev in found:
                comp = ev.vobject_instance.vevent
                title = str(comp.summary.value)
                start = comp.dtstart.value
                end = comp.dtend.value if hasattr(comp, "dtend") else start
                if hasattr(start, "tzinfo") and start.tzinfo is None:
                    start = start.replace(tzinfo=tz)
                if hasattr(end, "tzinfo") and end.tzinfo is None:
                    end = end.replace(tzinfo=tz)
                events.append({"title": title, "start": start.isoformat(), "end": end.isoformat()})
            events.sort(key=lambda e: e["start"])
        self.last_ok_at = time.time()
        return events

    def availability(self, start: datetime, end: datetime) -> dict[str, Any]:
        events = self.list_upcoming(days=max(1, (end - start).days + 1))
        conflicts = []
        for ev in events:
            es = datetime.fromisoformat(ev["start"])
            ee = datetime.fromisoformat(ev["end"])
            if es < end and ee > start:
                conflicts.append(ev)
        free = not conflicts
        return {"free": free, "conflicts": conflicts, "start": start.isoformat(), "end": end.isoformat()}

    def create_event(self, *, title: str, start: datetime, end: datetime, description: str = "") -> dict[str, Any]:
        if not self.write_enabled:
            raise CalendarError("Calendar is read-only (ICS). Use a CalDAV provider to create events.")
        client = self._caldav_client()
        cal_name = (getattr(self.settings, "calendar_name", "") or "").strip()
        cals = client.principal().calendars()
        target = cals[0]
        if cal_name:
            for c in cals:
                if cal_name.lower() in (getattr(c, "name", "") or "").lower():
                    target = c
                    break
        target.add_event(
            dtstart=start,
            dtend=end,
            summary=title,
            description=description,
        )
        local = f"{start.astimezone(ZoneInfo(self.tz_name)).strftime('%a %d %b %H:%M')}–{end.astimezone(ZoneInfo(self.tz_name)).strftime('%H:%M %Z')}"
        summary = f"Created calendar event “{title}” ({local})"
        return {"action": "create_event", "title": title, "start": start.isoformat(), "end": end.isoformat(),
                "summary": summary, "reply": summary}

    def update_event(self, *, title: str, start: datetime, end: datetime, uid: str | None = None) -> dict[str, Any]:
        if not self.write_enabled:
            raise CalendarError("Calendar is read-only (ICS).")
        # Minimal: create-style replacement when uid unknown
        return self.create_event(title=title, start=start, end=end, description=f"Update (uid={uid or 'n/a'})")


def execute_calendar_action(service: CalendarService, action: dict[str, Any]) -> dict[str, Any]:
    kind = str(action.get("action") or "list")
    if not service.configured and kind != "status":
        raise CalendarError(CONFIGURED_HINT)
    if kind == "list":
        days = int(action.get("days") or action.get("range_days") or 7)
        if "tomorrow" in str(action.get("when") or "").lower():
            days = 2
        if "today" in str(action.get("when") or "").lower():
            days = 1
        events = service.list_upcoming(days=days)
        if not events:
            reply = "No upcoming events in that range."
        else:
            reply = "Upcoming events:\n" + "\n".join(
                f"• {e['title']} ({e['start'][:16]})" for e in events[:15]
            )
        return {"action": kind, "events": events, "reply": reply, "summary": reply}
    if kind == "availability":
        when = str(action.get("when") or action.get("text") or "")
        parsed = parse_event_window(when, tz_name=service.tz_name)
        if parsed.ambiguous or not parsed.start or not parsed.end:
            return {"action": kind, "ambiguous": True, "reply": parsed.clarify or "When should I check?", "clarify": parsed.clarify}
        avail = service.availability(parsed.start, parsed.end)
        if avail["free"]:
            reply = f"You are free {parsed.start.strftime('%a %H:%M')}–{parsed.end.strftime('%H:%M')}."
        else:
            reply = "Conflicts:\n" + "\n".join(f"• {c['title']}" for c in avail["conflicts"])
        return {"action": kind, **avail, "reply": reply, "summary": reply}
    if kind in {"create_event", "update_event"}:
        when = str(action.get("when") or action.get("text") or "")
        parsed = parse_event_window(when, tz_name=service.tz_name)
        if parsed.ambiguous or not parsed.start or not parsed.end:
            return {"action": kind, "ambiguous": True, "reply": parsed.clarify or "When is the event?", "clarify": parsed.clarify}
        title = str(action.get("title") or action.get("name") or "Meeting")
        if kind == "create_event":
            return service.create_event(title=title, start=parsed.start, end=parsed.end, description=str(action.get("description") or ""))
        return service.update_event(title=title, start=parsed.start, end=parsed.end, uid=action.get("uid"))
    raise CalendarError(f"Unknown calendar action {kind!r}")


class CalendarWorker:
    type = "calendar"
    description = "List events, check availability, propose/create calendar entries."
    timeout_s = 90.0
    max_retries = 1

    def __init__(self, service: CalendarService) -> None:
        self.service = service

    def run(self, task: Task) -> dict[str, Any]:
        if not self.service.configured:
            raise WorkerError(CONFIGURED_HINT)
        ctx = task.context
        action = ctx.get("action") or self._infer(task.description)
        if action in {"create_event", "update_event"}:
            when = str(ctx.get("when") or task.description)
            parsed = parse_event_window(when, tz_name=self.service.tz_name)
            if parsed.ambiguous or not parsed.start or not parsed.end:
                return {"action": action, "ambiguous": True, "reply": parsed.clarify or "When is the event?", "clarify": parsed.clarify}
            if not ctx.get("confirmed"):
                tz = ZoneInfo(self.service.tz_name)
                title = str(ctx.get("title") or task.description[:80] or "Meeting")
                local = parsed.start.astimezone(tz).strftime("%a %d %b %H:%M")
                local_end = parsed.end.astimezone(tz).strftime("%H:%M %Z")
                reply = f"Proposed: “{title}” {local}–{local_end}. Use the create event action to confirm."
                return {
                    "action": "propose_create_event",
                    "title": title,
                    "when": when,
                    "start": parsed.start.isoformat(),
                    "end": parsed.end.isoformat(),
                    "reply": reply,
                    "summary": reply,
                    "numbered_actions": [{
                        "kind": "calendar",
                        "action": "create_event",
                        "title": title,
                        "when": when,
                    }],
                }
            return execute_calendar_action(
                self.service,
                {**ctx, "action": action, "when": when, "title": ctx.get("title"), "confirmed": True},
            )
        return execute_calendar_action(self.service, {**ctx, "action": action, "when": ctx.get("when") or task.description})

    @staticmethod
    def _infer(text: str) -> str:
        low = text.lower()
        if "free" in low or "available" in low or "busy" in low:
            return "availability"
        if "create" in low or "schedule" in low or "book" in low or "add" in low:
            return "create_event"
        return "list"