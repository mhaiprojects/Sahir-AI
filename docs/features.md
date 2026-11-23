# Features

## Web UI

- **HTTPS web control panel** on `WEB_HOST`:`WEB_PORT` (default `0.0.0.0:8765`) — **one unified screen** at `/` (`web/static/index.html`, `app.js`, `rail.js`, `chatbar.js`, `agent.js`, `models.js`, `wip.js`)
- **Projects rail** (left) — tree of projects and chats (supervisor ★), search, + New chat / + New project; mobile drawer via ☰
- **Centre chat** — Project · Chat · Mode · Model · mic state; live captions; composer with Send, Hold to listen, Hands-free, Speak replies; Ctrl/Cmd+Enter sends to Cursor (no separate Send to Cursor button)
- **Routine / WIP panel** (right / sticky bottom on mobile) — SSE activity log with project/chat/router/supervisor header ([ui-layout.md](ui-layout.md))
- **Rail footer pages** — Memory, Documents, Tasks, Hands-free shortcut, Models, Settings (voice commands + service/mic status), Logs
- **Models page** — pick/load/eject chat models, fallback toggles, benchmark ([models.md](models.md))
- **Model fallback** — escalates to `FALLBACK_MODEL` on invalid tool/action JSON or when you say "think harder" / "use the big model"; each reply is tagged with the model that answered
- **Live transcription** — visible on the chat view; idle / live draft / final correction
- **Hold-to-talk** (HTTP final transcribe + router) and **Hands-free** (`/ws/voice`, server Silero VAD, barge-in, router tiers 0–2)
- **Text chat** with markdown rendering (marked + DOMPurify in `web/static/vendor/`)
- **Chat history** — server-side conversations in `data/agent.db`; streaming via `/api/agent/chat/stream`
- **Voice commands** — SQLite-backed keywords; CRUD in Settings; watcher during busy state
- **Service health** — `/api/health` (Ollama, Piper, Whisper, ffmpeg, Cursor CLI); badges on Settings
- **Request log** — SQLite-backed Logs view
- **Mic permission prompt** — Permissions API check before `getUserMedia`
- **Saved settings** — per-chat mode (`sahir-chat-mode`), Hands-free (`voice-assistant-hands-free`, legacy `voice-assistant-always-listen`), Speak replies (`voice-assistant-speak-replies`), mic on/off in `localStorage`
- **Auto UI refresh** — `/api/health` `build_id`; reload when assets change (deferred while busy)
- **Cursor completion message** — Cursor tasks report completion in chat
- **WebM robustness** — truncated chunks salvaged; `PartialAudioError` on unusable uploads

## Mode and voice (chat bar / composer)

- **Mode** select per conversation: Agent, Tools, Chat, or Cursor (stored on the conversation row)
- **Hands-free** — streams PCM to `/ws/voice` (no browser wake word; desktop wake word remains in `bot.py`)
- **Speak replies** — one toggle: streaming TTS on Hands-free, `/api/speak` on hold-to-listen

## Desktop mode (`python bot.py`)

- Push-to-talk (`PTT_HOTKEY`, default Right Ctrl)
- Wake word (`WAKEWORD_MODEL`, default `hey_jarvis`; a custom "Hey Sahir" model is planned)
- Local mic/speaker via sounddevice
- Not used in Docker web-only deployment

## API endpoints (main)

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/api/health` | Component health + `build_id` for auto-refresh |
| POST | `/api/chat` | Text chat + optional routing |
| POST | `/api/cursor` | Explicit Cursor delegation |
| GET/POST | `/api/projects` | List / create projects (with supervisor chat) |
| POST | `/api/transcribe` | Hold-to-listen final utterance (`mode=ptt`, `dispatch_routed`) |
| POST | `/api/transcribe/live` | PTT live captions while recording |
| WS | `/ws/voice` | Hands-free streaming (VAD, router, TTS) |
| POST | `/api/speak` | TTS WAV response |
| GET/POST | `/api/commands` | Voice command CRUD |
| GET | `/api/logs` | Request history |
| GET | `/api/models` | Chat models: installed, loaded (VRAM/RAM), active, fallback |
| POST | `/api/models/select` · `/load` · `/eject` · `/options` | Switch, preload, unload models; fallback policy |
| GET/POST | `/api/models/benchmark` | Run / read the model benchmark (`scripts/bench_models.py`) |

## Cursor CLI integration

- Triggered by router when reply looks incomplete or code/task-like
- Manual via **Ctrl/Cmd+Enter** in the composer, Cursor mode, or voice routing
- Config: `CURSOR_WORKSPACE`, `CURSOR_MODEL`, `CURSOR_AGENT_BIN`, `CURSOR_API_KEY`, `CURSOR_TIMEOUT_SECONDS`
- In Docker: mount host binary via `docker-compose.cursor.yml` (not included in image)

## Systemd user units

- `bash scripts/install_systemd.sh` renders `systemd/*.service` (`@ROOT@` placeholder) into `~/.config/systemd/user/` and runs `daemon-reload`
- Nothing is enabled unless `--enable` is passed (enables the web unit)
- Units add `~/.local/bin` to `PATH` so `cursor-agent` resolves

## Tests

- `python scripts/smoke_test.py` — Piper model, PC control, Cursor CLI
- `python scripts/test_audio_decode.py` — WAV/WebM decode, truncated/headerless WebM
- `python scripts/test_web.py` — full API suite incl. Piper → Whisper round trip (server must be running; `WEB_PORT=...` overrides)

## Security notes

- **No authentication** on web UI — trusted LAN only
- PC control whitelists: `ALLOWED_APPS`, `ALLOWED_COMMANDS`, `ALLOWED_DIRECTORIES`
- `.env` and certs never committed; generated by `setup.sh` / `scripts/generate_*.sh`

## Session history (features added during initial build)

Voice commands during transcribe/speak, markdown in chat, TTS markdown stripping, Hands-free noise gate, chat history, Cursor auto-delegate, LAN service script, error log monitoring loop, Docker Compose packaging, GitHub publish, live transcript panel, unified UI shell (projects rail + chat + WIP, Nov 2026).

Nov 2026 resume: mic permission prompt, localStorage settings, auto UI refresh via `build_id`, Cursor completion messages, WebM partial-chunk hardening, path-independent systemd units, stale `CURSOR_WORKSPACE` fallback, extended `test_web.py`.
