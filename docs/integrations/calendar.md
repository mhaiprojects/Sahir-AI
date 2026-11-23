# Calendar integration

Sahir lists upcoming events, checks availability for a time range, and (with CalDAV) can create events after confirmation.

## Google Calendar

Google’s CalDAV endpoint expects **OAuth**, not a simple app password, so Sahir does not target Google CalDAV out of the box. For **read-only** Google Calendar, use Google’s **secret ICS URL** (Calendar → Settings → Integrate calendar → Secret address in iCal format) as `CALENDAR_ICS_URL`. Event creation requires a CalDAV provider that accepts username/password (Fastmail, Nextcloud, iCloud app-specific password, etc.).

## Configuration

| Variable | Purpose |
|----------|---------|
| `CALENDAR_CALDAV_URL` | CalDAV base URL (read/write when creds set) |
| `CALENDAR_ICS_URL` | Read-only ICS feed (no password) |
| `CALENDAR_USERNAME` | CalDAV user |
| `CALENDAR_PASSWORD` or `CALENDAR_PASSWORD_FILE` | Password (file mode **600**) |
| `CALENDAR_NAME` | Optional calendar name filter |
| `CALENDAR_TIMEZONE` | Default `Australia/Melbourne` for spoken times |

## Settings UI

**Settings → Integrations → Calendar** — needs-credentials hint or **Verify** (CalDAV principal/calendars or ICS fetch).

## HTTP API

- `GET /api/integrations/calendar/status`
- `POST /api/integrations/calendar/verify`

## Natural-language times

Phrases like “tomorrow at 3pm for an hour” are parsed in the configured timezone. Ambiguous times (e.g. “3” without am/pm) return a clarifying question instead of guessing.

## Safety

`create_event` and `update_event` require the same confirmation flow as email send (chip + dialog + voice yes/no), showing title and local start/end times.

## Worker

Delegate to the `calendar` worker. Read-only mode works with ICS only; writes need CalDAV credentials.