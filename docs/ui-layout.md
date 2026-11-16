# Web UI layout

## Target layout (product spec from sessions)

Top-to-bottom on **Home** view:

1. **Header** — app title / branding
2. **Live transcription box** — always visible; primary feedback while speaking
3. **Primary buttons** — Toggle Mic, Hold to listen / Listening (state-aware)
4. **Options row** — Route via Tools, Always Listening, Route all via advanced AI (Cursor)
5. **User input** — text chat box with send and related actions (e.g. Send to Cursor)

**Left sidebar nav** (icon rail):

| View | Content |
|------|---------|
| **Home** | Layout above + chat transcript |
| **Logs** | Request / activity log |
| **Config** | Settings, voice commands CRUD, tunables |

Secondary status panels and detailed logs should be collapsible or on Logs/Config views, not competing with live transcription on Home.

## Current implementation (as of repo publish)

Implemented in `web/static/index.html` + `style.css` + `app.js`:

- `.nav-sidebar` with Home / Logs / Config tabs
- `#live-transcript.live-transcript-top` — first content block on Home
- Status badge: Mic off / Ready / Waiting / Recording / Processing
- Live draft + final correction areas
- `hideLiveTranscript()` resets to idle (panel stays visible; no `display: none`)

## Live transcription behavior

- **Idle:** placeholder text when mic off or waiting
- **Live:** updates from `/api/transcribe/live` during hold-to-talk and always-listening
- **Final:** shows corrected text after silence timeout; persists through TTS then resets

Cache-bust JS in `index.html`: `app.js?v=...` — bump when changing frontend logic.

## UX goals (done Nov 2026)

- [x] Mic permission prompt when enabling microphone — `promptForMicPermission()` queries the Permissions API before `getUserMedia` and explains prompt/denied states; `watchMicPermission()` reports revocation
- [x] Remember UI toggles and mic preference in `localStorage` — keys `voice-assistant-route-tools`, `voice-assistant-always-listen`, `voice-assistant-route-cursor`, `voice-assistant-mic-enabled`; `restoreMicPreference()` re-enables the mic on load if permission is `granted`
- [x] Auto-refresh UI after server deploy / error recovery — `refreshHealth()` compares `/api/health` `build_id`, reloads via `reloadForNewBuild()` (deferred while busy/recording/speaking), retries every 5 s when the server is down and announces recovery; `index.html` is served `Cache-Control: no-cache`
- [x] Cursor task completion always surfaces a message in chat — `reportCursorCompletion()` after Send to Cursor, the Cursor toggle, and routed `run_cursor_agent` replies

Current cache-bust: `app.js?v=20261114a`.
