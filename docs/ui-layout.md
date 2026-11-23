# Web UI layout

## Unified screen (item 6, Nov 2026)

Single-page app in `web/static/index.html` with `style.css`, `panels.css`, `unified.css`, `app.js`, `rail.js`, `chatbar.js`, `agent.js`, `wip.js`, and `models.js`.

Desktop layout:

```
┌ Projects rail ──────┐┌ Centre (view-chat) ─────────────────────────────┐┌ Routine / WIP ─┐
│ Search                ││ Chat bar: Project · Chat · Mode · Model · mic    ││ Context strip  │
│ ▸ General             ││ Breadcrumb · Supervisor badge · chat actions     ││ Live log …     │
│   ★ Supervisor        ││ Live captions                                  ││ Clear · fold   │
│   • chats…            ││ Empty-state “Ready…” / conversation            │└────────────────┘
│ + New chat            ││ Composer: Send · mic · Hold · Hands-free ·     │
│ + New project         ││           Speak replies                        │
│ [Memory][Docs][Tasks]  │└────────────────────────────────────────────────┘
│ [Voice][Models][…]    │
└───────────────────────┘
```

Narrow screens (`max-width: 980px`): the **☰** bar opens the projects rail as a drawer; the main column stacks above the sticky WIP panel (`panels.css`).

## Left rail (`#project-rail`, `rail.js`)

- Loads **`GET /api/projects`** (General first) and groups chats from **`GET /api/conversations`**.
- Each project expands to show the **supervisor** (★) and sub-chats; click to open in the centre view.
- **+ New chat** → `POST /api/conversations` (current project). **+ New project** → `POST /api/projects` (creates project + supervisor conversation).
- **Footer buttons** switch secondary views (`data-view`); default view is **`chat`**.

| Footer link | View id | Content |
|-------------|---------|---------|
| Chat | `view-chat` | Main conversation + voice (default) |
| Memory | `view-memory` | Long-term memory CRUD + review (`agent.js`) |
| Documents | `view-docs` | RAG upload, retrieval test (`agent.js`) |
| Tasks | `view-tasks` | Delegation log (`agent.js`) |
| Live voice | `view-voice` | WebSocket streaming session (`agent.js`) |
| Models | `view-models` | Model pick/load/benchmark (`models.js`) |
| Settings | `view-settings` | Voice commands, service + mic status |
| Logs | `view-logs` | Request log (`app.js`) |

## Centre chat (`view-chat`)

Top to bottom:

1. **Header** — branding, **Take tour**
2. **Chat bar** (`#chat-bar`) — project, chat dropdown, single **Mode** select (`#chat-mode`), model, `#chat-mic-state`
3. **Context** — `#supervisor-badge`, breadcrumb (`#breadcrumb-project` › `#breadcrumb-chat`), `#conv-actions` (rename, export, extract, delete)
4. **Live transcription** (`#live-transcript`) — draft + corrected captions while speaking
5. **Empty state** (`#chat-empty-state`) — “Ready…” when the conversation has no user/assistant messages (not wiped when restoring an empty chat)
6. **Chat log** (`#chat-log`) — markdown messages
7. **Composer** (`#chat-form`) — message box, Send, mic controls, Hands-free (`#hands-free-toggle`), Speak replies (`#speak-replies-toggle`)

Voice behaviour matches the previous Home view: hold-to-talk, Hands-free (stored under `voice-assistant-always-listen` in `localStorage`), speak replies (`voice-assistant-speak-replies`), live `/api/transcribe/live` + final transcribe.

## Routine / WIP panel (`#wip-panel`, `wip.js`)

- Right column on desktop; sticky bottom bar on mobile.
- **Context strip:** Project · Active chat · Router · Supervisor (from activity bus events).
- **Log:** SSE `GET /api/activity/stream`; progress from orchestrator is mirrored server-side (no duplicate “Working…” boxes).

## SPA navigation

- Views: `?view=chat|memory|docs|tasks|voice|models|settings|logs` (stored in `localStorage`).
- `/agent` → redirect to `/?view=chat` for old bookmarks.
- `rail-footer-btn` and `window.sahirActivateView()` switch views; opening a chat from the rail calls `window.sahirOpenConversation()`.

## UX notes (still accurate)

- **Enter** sends; **Shift+Enter** newline; **Ctrl/Cmd+Enter** → Cursor mode send.
- **Mode** per chat via `PATCH /api/conversations/{id}`; UI key `sahir-chat-mode`.
- Mic permission prompt, `localStorage` preferences, `build_id` auto-reload, Cursor completion messages — see checklist in git history / `app.js`.
- Guided tour (`tour.js`) targets the unified layout (rail, chat bar, composer, WIP, Settings).

## Implementation files

| Concern | Files |
|---------|--------|
| Layout / rail CSS | `unified.css`, `panels.css`, `style.css` |
| Projects tree | `rail.js` |
| Project/chat/mode bar | `chatbar.js` |
| Voice + text chat | `app.js` |
| Secondary pages | `agent.js` |
| Activity panel | `activity.py`, `wip.js` |

Bump `?v=` cache-bust query strings in `index.html` when changing static assets.