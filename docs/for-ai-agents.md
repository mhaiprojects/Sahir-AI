# Guide for AI agents

Quick orientation when continuing work on this project.

## Project identity

- **Name:** Sahir
- **Path (dev):** the repository root (any directory) (`CURSOR_WORKSPACE` falls back to the parent folder if the configured path is missing)
- **Remote:** `git@github.com:mhaiprojects/Sahir-AI.git`
- **Primary mode for LAN/Docker:** `python bot.py --web` (FastAPI + static SPA)

## Before changing code

1. Read [architecture.md](./architecture.md) and [troubleshooting.md](./troubleshooting.md)
2. Check `.env.example` — never commit `.env` or `data/certs/`
3. Web mic requires HTTPS on LAN — cert SAN must include host IP
4. After JS changes: bump `app.js?v=` in `index.html`; tell user to hard refresh

## Safe change patterns

- **Minimal diffs** — match existing style in `app.js`, `web_server.py`, `router.py`
- **Errors vs warnings** — partial WebM skips are WARNING + graceful response; only `[ERROR]` triggers monitoring alerts
- **Tests:** `python scripts/test_web.py` with server running (`WEB_PORT=8766` if 8765 is taken); also `smoke_test.py`, `test_audio_decode.py`, `test_markdown_speech.py`, `test_router_delegation.py`
- **venv:** call `venv/bin/python` directly — console-script shebangs and `activate` still point at the old pre-move path
- **Restart:** `bash scripts/voice-assistant.sh restart` or `docker compose restart voice-assistant`

## Common user requests → files

| Request | Where to look |
|---------|----------------|
| Live transcription broken | `web/static/app.js` (MediaRecorder, `requestLiveTranscript`), `web_server.py` live endpoint |
| UI layout | `web/static/index.html`, `style.css`, `app.js` |
| Ollama / routing | `tools/router.py`, `config.py` |
| Cursor delegation | `tools/cursor_cli.py`, `tools/router.py`, `/api/cursor` |
| Voice commands | `web/commands.py`, `app.js` command watcher |
| Docker | `Dockerfile`, `docker-compose*.yml`, `DOCKER.md` |
| New PC setup | `setup.sh`, `docs/deployment.md` |
| Log monitoring | `scripts/error_check.sh`, loop checkpoint in `data/logs/` |

## Known fragile areas

1. **Continuous MediaRecorder + live WebM chunks** — easy to break; test always-listening end-to-end
2. **Cached browser JS** — users see old behavior after server fix until hard refresh
3. **Cursor CLI subprocess** — long-running, no logs until complete; not in Docker by default
4. **Whisper first load** — slow CPU download; use `PRELOAD_WHISPER=1` for Docker images

## Monitoring loop convention

Silent loop: only notify user when `bash scripts/error_check.sh` exits `2`. On error: investigate log, fix, `voice-assistant.sh restart`, `test_web.py`, notify fix.

## Do not

- Commit secrets, `.env`, private keys, or `data/`
- Force-push `main` without user approval
- Add auth bypass or disable HTTPS guidance for LAN mic use
- Treat WARNING partial-audio skips as fatal errors in monitoring

## Related chat session topics (historical)

Features built: voice commands, markdown chat, chat history, Cursor auto-delegate, noise gate, service script, error monitoring, Docker packaging, GitHub publish, live transcript panel, nav layout (Home/Logs/Config). Open items (all done Nov 2026): ~~WebM robustness~~ (`PartialAudioError`, truncated salvage), ~~settings persistence~~ (`localStorage`), ~~auto UI refresh~~ (`build_id`), ~~Cursor completion reporting~~ (`reportCursorCompletion`), ~~optional systemd unit~~ (`scripts/install_systemd.sh`, not enabled by default).
