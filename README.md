# MH Voice Assistant

> [!WARNING]
> **No authentication.** The web UI has no login and binds to all network interfaces
> (`WEB_HOST=0.0.0.0`) by default. Anyone who can reach the port can chat with the agent, read
> chat history/memory/documents, and trigger PC-control and Cursor tasks. Run it only on trusted
> networks, set `WEB_HOST=127.0.0.1`, or put it behind a reverse proxy that enforces authentication.

Custom local voice assistant using trusted open-source components:

- **STT:** faster-whisper
- **TTS:** Piper
- **Router:** Ollama (local)
- **Dev tasks:** Cursor CLI (`cursor-agent -p`)
- **Activation:** push-to-talk (Right Ctrl) + wake word (`hey_jarvis`)
- **Local-first agent:** persistent chat history, long-term memory with review, document RAG
  (SQLite + sqlite-vec, all under `data/`), an Ollama orchestrator that delegates to workers
  (research summarizer, Cursor code agent, ClickUp), realtime voice over WebSocket with Silero VAD
  and barge-in, optional Kokoro-82M TTS

## Local-first agent (new)

Open **`https://<host>:<port>/agent`** (or the ✦ Agent item in the nav rail):

| Tab | What you can do |
|-----|-----------------|
| Chats | List/search (full-text) conversations, view, rename, delete, export Markdown/JSON, chat with streaming or the orchestrator |
| Memory | Add/edit/delete long-term facts; approve/reject memories auto-extracted from chats; recall test |
| Documents | Drag-and-drop `.txt/.md/.pdf` (and other text) files → chunked, embedded with `nomic-embed-text`, stored in SQLite; view chunks; retrieval test |
| Live voice | Streaming mic → Silero VAD → Whisper → orchestrator → sentence-streamed TTS; talk over it to barge in |
| Tasks | Delegated task log (type, status, retries, duration, output, ClickUp actions) |

The original voice UI (`/`) now also uses memory + document context and saves its turns into the
same chat history (`AGENT_PERSIST_WEB=true`). Its always-listening and live-caption endpoints skip
Whisper when Silero VAD hears no speech (`VAD_GATE_ENABLED`).

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
git clone git@github.com:mhaiprojects/MH-VoiceAssistant-A.git
cd MH-VoiceAssistant-A
bash setup.sh native    # Arch/Manjaro: venv + Ollama + models + TLS certs
# or
bash setup.sh docker    # Docker Compose stack (Ollama + web UI)
```

Certs and `.env` are generated locally (never committed). Regenerate TLS: `FORCE=1 bash scripts/generate_web_cert.sh`

### Manual install (Arch/Manjaro)

```bash
cd MH-VoiceAssistant-A           # your clone directory
bash scripts/install.sh          # system deps, Ollama, venv, models
source venv/bin/activate
cp .env.example .env             # if install.sh did not create it
python bot.py
```

Hold **Right Ctrl** while speaking, then release. Or say **hey jarvis** and speak after the prompt.

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

Features: text chat with full tool routing, hold-to-talk, always-listening mode, two-phase web transcription, service health badges, optional TTS playback, and a sidebar request log stored in SQLite.

Configure `WEB_HOST`, `WEB_PORT`, and `WEB_DB_PATH` in `.env`. **No authentication** — see the warning at the top; use only on trusted networks or behind an authenticating reverse proxy.

### Two-phase web transcription

While **Always listening** is enabled:

1. **Live phase** — audio streams to `/api/transcribe/live` every few seconds using the fast `WHISPER_LIVE_MODEL` (default `tiny`). Captions update in the UI while you speak.
2. **Final phase** — after **End of Conversation Silence** (default `3` seconds, `WEB_CONVERSATION_SILENCE_SECONDS`), the full recording is sent to `/api/transcribe?phase=final` using `WHISPER_FINAL_MODEL` for higher accuracy before routing to tools.

Tune in `.env`:

```
WEB_CONVERSATION_SILENCE_SECONDS=3
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

## Systemd user service

```bash
bash scripts/install_systemd.sh            # render units with this checkout's path
systemctl --user start voice-assistant-web.service   # web UI only
systemctl --user enable --now voice-assistant.service  # desktop PTT/wake word
# or: bash scripts/install_systemd.sh --enable  (enables the web unit)
```

The unit files in `systemd/` use an `@ROOT@` placeholder, so the checkout can live anywhere.

Run in your graphical user session so push-to-talk hotkeys work.

## Documentation

Detailed internal docs for developers and AI agents: **[docs/README.md](docs/README.md)** (architecture, operations, troubleshooting, deployment, UI layout).

## Configuration

See `.env.example` for all settings: Ollama model, Whisper size, hotkey, Cursor workspace, and PC control whitelists.

## Project layout

```
MH-VoiceAssistant-A/
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

Copyright (C) 2026 the MH Voice Assistant contributors.

This project is licensed under the **GNU General Public License v3.0 only** (SPDX: `GPL-3.0-only`).
See [LICENSE](LICENSE) for the full text. Third-party components (models, vendored JS such as
`marked` and `DOMPurify`, and Python dependencies) keep their own licenses.
