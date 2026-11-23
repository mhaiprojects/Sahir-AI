"""Deterministic natural-language time parsing (Australia/Melbourne style)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo


@dataclass
class TimeParseResult:
    start: datetime | None
    end: datetime | None
    ambiguous: bool = False
    clarify: str | None = None


_HOUR_RE = re.compile(
    r"(?is)(?:at\s+)?(\d{1,2})(?::(\d{2}))?\s*(am|pm)?\b"
)
_DURATION_RE = re.compile(r"(?is)for\s+(\d+)\s*(hour|hours|hr|hrs|minute|minutes|min|mins)\b")
_REL_DAY_RE = re.compile(r"(?is)\b(today|tomorrow|tonight)\b")


def parse_event_window(text: str, *, tz_name: str = "Australia/Melbourne", now: datetime | None = None) -> TimeParseResult:
    raw = (text or "").strip()
    if not raw:
        return TimeParseResult(None, None, ambiguous=True, clarify="What time should the event start?")
    tz = ZoneInfo(tz_name)
    now = now or datetime.now(tz)
    if now.tzinfo is None:
        now = now.replace(tzinfo=tz)
    day = now.date()
    low = raw.lower()
    if "tomorrow" in low:
        day = day + timedelta(days=1)
    elif "tonight" in low and "today" not in low:
        day = day
    m_hour = _HOUR_RE.search(raw)
    if not m_hour:
        if _REL_DAY_RE.search(raw):
            return TimeParseResult(None, None, ambiguous=True, clarify="What time should it start?")
        return TimeParseResult(None, None, ambiguous=True, clarify="I could not tell the time — please say something like tomorrow at 3pm for an hour.")
    hour = int(m_hour.group(1))
    minute = int(m_hour.group(2) or 0)
    ampm = (m_hour.group(3) or "").lower()
    if ampm == "pm" and hour < 12:
        hour += 12
    if ampm == "am" and hour == 12:
        hour = 0
    if not ampm and hour <= 7:
        # ambiguous morning vs evening
        return TimeParseResult(None, None, ambiguous=True, clarify=f"Did you mean {hour}:{minute:02d} am or pm?")
    start = datetime(day.year, day.month, day.day, hour, minute, tzinfo=tz)
    duration = timedelta(hours=1)
    m_dur = _DURATION_RE.search(raw)
    if m_dur:
        n = int(m_dur.group(1))
        unit = m_dur.group(2).lower()
        duration = timedelta(hours=n) if "hour" in unit or unit.startswith("hr") else timedelta(minutes=n)
    end = start + duration
    if end <= start:
        end = start + timedelta(hours=1)
    return TimeParseResult(start, end, ambiguous=False)