# Sahir (ساهر)

**Sahir** (Arabic ساهر, said *sah-HEER*) means "the wakeful one, who keeps watch": a local-first,
private voice assistant that stays listening on your own machine and hands off coding, research and
tasks to agents. Repository: `Sahir-AI`.

> [!WARNING]
> The web UI binds to all interfaces (`WEB_HOST=0.0.0.0`) by default for LAN phones. **Web login is
> on by default** (`WEB_AUTH_ENABLED=true`) using a shared token file — see [Logging in](#logging-in).
> Without auth, anyone on the LAN could read activity, chat history, and trigger Cursor/PC tasks.
> Use HTTPS on LAN, rotate the token if needed, or set `WEB_AUTH_ENABLED=false` only on a trusted network.

Custom local voice assistant using trusted open-source components:

- **STT:** faster-whisper
- **TTS:** Piper
- **Router:** Ollama (local)
- **Dev tasks:** Cursor CLI (`cursor-agent -p`)
- **Activation (desktop app only, `python bot.py`):** push-to-talk (Right Ctrl) + wake word
  (`hey_jarvis` for now; a custom "Hey Sahir" wake word is planned). **Not available in the browser.**
- **Local-first agent:** persistent chat history, long-term memory with review, document RAG
  (SQLite + sqlite-vec, all under `data/`), an Ollama orchestrator that delegates to workers
  (research summarizer, Cursor code agent, ClickUp), realtime voice over WebSocket with Silero VAD
  and barge-in, optional Kokoro-82M TTS

## Local-first agent (web UI)

Open **`https://<host>:<port>/`** — one unified screen (old `/agent` bookmarks redirect to `/?view=chat`).

| Area | What you can do |
|------|-----------------|
| **Projects rail** (left) | Browse projects (General first), open the supervisor ★ or any chat, search, **+ New chat** / **+ New project** |
| **Chat** (centre) | Voice + text conversation with live captions, per-chat mode, memory/RAG context, streaming replies |
| **Rail footer** | **Memory**, **Documents**, **Tasks**, **Hands-free** (shortcut), **Models**, **Settings**, **Logs** |
| **Routine / WIP** (right) | Live activity stream + header (project, active chat, router, supervisor) |

The centre chat uses the same store and orchestrator as the secondary pages (`AGENT_PERSIST_WEB=true`).
Hands-free uses server-side Silero VAD on `/ws/voice`; hold-to-listen uses HTTP transcribe only after you release the mic button.

### Chat bar, composer, and the Routine / WIP panel

- **Chat bar:** **Project** · **Chat** (quick switcher) · **Mode** (Agent / Tools / Chat / Cursor, saved per chat) · **Model** · mic state.
- **Composer:** **Send**; **Enable microphone** / **Hold to listen**; one **Hands-free** toggle (`/ws/voice`); **Stop** (interrupt); one **Speak replies** toggle. **Ctrl+Enter** (Cmd+Enter on Mac) sends to Cursor — there is no separate Send to Cursor button.
- **Default model:** smallest installed Ollama chat model (by size) unless you pick one in the chat bar, Models page, or `POST /api/models/select` (`data/model_state.json`).
- **Routine / WIP panel** (right on desktop; sticky bottom bar below 980px): terminal-style feed from `GET /api/activity/stream`, context strip on top, Clear and Collapse.
- **Mobile:** **☰** opens the projects rail as a drawer; the activity panel stays the sticky bottom bar.

ClickUp: put `CLICKUP_API_TOKEN=...` in `.env` and `CLICKUP_LIST_ID=your-clickup-list-id`. Without a token the
ClickUp worker/hook report "token needed" and nothing is sent.

Optional Kokoro TTS: `bash scripts/setup_kokoro.sh` then `TTS_BACKEND=kokoro` (slower than Piper on CPU; see docs).

Tests:

```bash
venv/bin/python -m pytest                                   # offline unit + API + WebSocket tests
# WEB_PORT is whatever the server runs on (default 8765; set in .env)
venv/bin/python scripts/e2e_agent.py --base https://127.0.0.1:${WEB_PORT:-8765}   # live milestone checks (Ollama etc.)
WEB_PORT=${WEB_PORT:-8765} venv/bin/python scripts/test_web.py   # existing web integration tests
```

### Model selector

The ◈ **Models** view switches the chat model at runtime (Select / Load / Eject), shows each
model's size and GPU/RAM split, and keeps a big fallback model (`FALLBACK_MODEL`, default
`llama3.1:8b`) for invalid actions or when you say "think harder". Benchmark all installed models
with the **Run benchmark** button or `venv/bin/python scripts/bench_models.py`. See
[docs/models.md](docs/models.md).

Docs: [docs/architecture.md](docs/architecture.md) · [docs/upgrade-research.md](docs/upgrade-research.md) · [docs/models.md](docs/models.md)

## Quick start

### New machine (recommended)

```bash
git clone git@github.com:mhaiprojects/Sahir-AI.git
cd Sahir-AI
bash setup.sh native    # Arch/Manjaro: venv + Ollama + models + TLS certs
# or
bash setup.sh docker    # Docker Compose stack (Ollama + web UI)
```

Certs and `.env` are generated locally (never committed). Regenerate TLS: `FORCE=1 bash scripts/generate_web_cert.sh`

### Manual install (Arch/Manjaro)

```bash
cd Sahir-AI           # your clone directory
bash scripts/install.sh          # system deps, Ollama, venv, models
source venv/bin/activate
cp .env.example .env             # if install.sh did not create it
python bot.py
```

**Desktop app only (`python bot.py`), not in the browser:** hold **Right Ctrl** while speaking, then release. Or say **hey jarvis** and speak after the prompt (a custom "Hey Sahir" wake word is planned).

## Web UI (local network)

Start the web control panel (binds to all interfaces by default):

```bash
python bot.py --web
# or
python web_server.py
```

Open from any device on your LAN:

```
http://<this-pc-ip>:8765
```

Features: unified chat screen with projects rail, text chat with full tool routing, hold-to-talk, Hands-free mode, two-phase web transcription, Routine/WIP activity panel, optional TTS, and a request log in SQLite.

Configure `WEB_HOST`, `WEB_PORT`, and `WEB_DB_PATH` in `.env`. Web access uses a **shared login token** (see [Logging in](#logging-in) below). Set `WEB_AUTH_ENABLED=false` only on a fully trusted LAN.

### Logging in

1. Create the token file (once per machine): `bash scripts/generate_web_token.sh`
2. Default path: `~/.config/jahash-secrets/sahir-web-token.txt` (mode `600`). Show it with `bash scripts/generate_web_token.sh --show` or `cat ~/.config/jahash-secrets/sahir-web-token.txt` on the server.
3. **Desktop browser:** open the web UI → you are redirected to `/login` → paste the token once → session cookie lasts ~30 days.
4. **Phone (HTTPS):** on the server, build a one-time link (do not share in chat logs): `https://<this-pc-ip>:8765/login?token=<paste-token>` — opens Sahir and drops the token from the URL.
5. **Scripts / `test_web.py`:** `Authorization: Bearer <token>` or set `WEB_AUTH_TOKEN` in the environment (not in committed `.env`).
6. **Rotate:** `bash scripts/generate_web_token.sh --rotate` — invalidates old cookies; log in again on each device. Use **Settings → Log out** to end a session on one browser.

Do **not** put the token in `.env`, git, or activity logs.

### Web voice paths

- **Hands-free** (`#hands-free-toggle`) — PCM via AudioWorklet → **`/ws/voice`**: server Silero VAD, faster-whisper, utterance router (tiers 0–2), streaming TTS with barge-in. Partials and finals show in the captions strip; **Stop** sends `interrupt`.
- **Hold to listen** — browser `MediaRecorder` + **`/api/transcribe/live`** (draft captions) → **`/api/transcribe?mode=ptt`** (final Whisper + `dispatch_routed`). Optional reply TTS via `/api/speak` when **Speak replies** is on.

Hold-to-listen tuning in `.env`:

```
WEB_LIVE_TRANSCRIBE_INTERVAL_SECONDS=2
WHISPER_LIVE_MODEL=tiny
WHISPER_FINAL_MODEL=base
```

### Microphone on LAN

Browsers only allow microphone access on **HTTPS** (or `localhost`). For phones/tablets on your LAN:

```bash
bash scripts/generate_web_cert.sh
# add WEB_SSL_CERT and WEB_SSL_KEY to .env, then restart
python bot.py --web
```

Open `https://<this-pc-ip>:8765` and accept the self-signed certificate.

## Text mode (no microphone)

```bash
python bot.py --text "What is Docker?"
python bot.py --text --route "Open Firefox"
```

## Tests

```bash
python scripts/smoke_test.py
python bot.py --web          # in another terminal
python scripts/test_web.py
```

## Cursor CLI

Ensure you are logged in:

```bash
cursor-agent login
cursor-agent status
```

Coding and workspace tasks are routed to:

```bash
cursor-agent -p --trust --workspace "$CURSOR_WORKSPACE" --model composer-2.5 "<prompt>"
```

### Persistent Cursor CLI chat

This repository uses one long-running Cursor CLI session for coding work (JW-12, created 2026-11-23):

- **Chat ID:** `1374f8df-2e7e-48a1-a739-6820ebe9d905`

Resume that chat instead of starting a new one:

```bash
cursor-agent --resume 1374f8df-2e7e-48a1-a739-6820ebe9d905 -p --trust --workspace "$(pwd)"
```

Use the repository root as `--workspace` (or your configured `CURSOR_WORKSPACE`). Do **not** open a second parallel coding chat for this repo—keep context in this session so prior work stays connected.

## Systemd user service

```bash
bash scripts/install_systemd.sh            # render units into ~/.config/systemd/user
bash scripts/install_systemd.sh --enable   # after: bash scripts/voice-assistant.sh stop
bash scripts/install_systemd.sh --status
systemctl --user enable --now voice-assistant.service  # optional desktop PTT/wake
```

The unit files in `systemd/` use an `@ROOT@` placeholder, so the checkout can live anywhere. With the web unit **enabled**, `voice-assistant.sh` delegates to systemd. See [docs/operations.md](docs/operations.md).

Run in your graphical user session so push-to-talk hotkeys work.

## Documentation

Detailed internal docs for developers and AI agents: **[docs/README.md](docs/README.md)** (architecture, operations, troubleshooting, deployment, UI layout).

## Configuration

See `.env.example` for all settings: Ollama model, Whisper size, hotkey, Cursor workspace, and PC control whitelists.

## Project layout

```
Sahir-AI/
├── bot.py                 # Main entry point
├── audio_pipeline.py      # STT/TTS/recording
├── config.py
├── triggers/              # PTT + wake word
├── tools/                 # Router, Cursor CLI, PC control
├── models/piper/          # Downloaded voice model
├── docs/                  # Internal docs (architecture, ops, AI handoff)
└── scripts/install.sh
```

## License

Copyright (C) 2026 the Sahir contributors.

This project is licensed under the **GNU General Public License v3.0 only** (SPDX: `GPL-3.0-only`).
See [LICENSE](LICENSE) for the full text. Third-party components (models, vendored JS such as
`marked` and `DOMPurify`, and Python dependencies) keep their own licenses.
