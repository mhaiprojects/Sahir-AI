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
| **Models** | Chat model selector (Select / Load / Eject), fallback toggles, benchmark |
| **Config** | Settings, voice commands CRUD, tunables |
| **Agent** | Chats, memory, RAG documents, live voice, delegation tasks (single SPA view) |

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

## Why Agent is one section (not separate apps)

**Home** is the quick voice + chat control panel. **Agent** is the operator console for the same runtime:
persistent chats in `data/agent.db`, long-term **Memory**, **Documents** (RAG), **Live voice** (WebSocket orchestrator), and **Tasks** (worker delegation log). Those surfaces all read/write the agent store and orchestrator, so they belong together rather than as unrelated top-level nav items.

## SPA navigation

The shell is a single page (`index.html`). Views switch via the left rail and `?view=` query (e.g. `/?view=agent`). `/agent` redirects to `/?view=agent` for old bookmarks.

## Chat UX (Nov 2026)

- **Enter** sends; **Shift+Enter** inserts a newline; **Ctrl+Enter** (or **Cmd+Enter**) sends to Cursor (Home and Agent composer).
- Chat textareas are focused on load and refocused after each send (Home and Agent when that view is active).
- **Activity feed** on Home and Agent Chats shows streaming progress: context retrieval, model/fallback switches, tool calls, worker delegation, Cursor CLI handoff.
- **Chat history** is stored server-side; Home and Agent open the latest conversation by default (with `localStorage` preference when still valid).
- **New chat** button (top right of Agent) starts a fresh conversation.

Current cache-bust: `app.js?v=20261116c`, `agent.js?v=20261116c`, `models.js?v=20261116b`, `style.css?v=20261116b`, `agent.css?v=20261116b`.
