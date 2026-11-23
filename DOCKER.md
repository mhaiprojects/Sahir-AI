# Docker deployment

Run the voice assistant web UI on another PC with Docker Compose. Includes:

- **voice-assistant** — FastAPI web UI, faster-whisper STT, Piper TTS
- **ollama** — local LLM (`llama3.1:8b` by default)

Browser microphone capture is client-side; the container serves HTTPS so LAN devices can use the mic.

## Requirements

- Docker Engine 24+ and Docker Compose v2
- ~8 GB free disk (Ollama model + Whisper + Piper)
- Optional: NVIDIA GPU + [Container Toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/install-guide.html) for faster inference

## Quick start (another PC)

1. Copy the entire repository folder (e.g. `Sahir-AI/`) to the target machine.

2. Create config:

   ```bash
   cd Sahir-AI
   cp .env.docker.example .env
   ```

3. (Recommended) Add your LAN IP to the TLS certificate so phones can use the mic. Edit `.env`:

   ```bash
   WEB_CERT_SAN=DNS:localhost,DNS:voice-assistant,IP:127.0.0.1,IP:192.0.2.10
   ```

   Replace `192.0.2.10` (documentation placeholder) with the host's IP. Delete existing certs if regenerating:

   ```bash
   rm -f data/certs/web.crt data/certs/web.key
   ```

4. Build and start:

   ```bash
   docker compose up -d --build
   ```

5. Watch first-start progress (Ollama model pull + Whisper download):

   ```bash
   docker compose logs -f voice-assistant
   docker compose logs -f ollama
   ```

6. Open in a browser:

   ```
   https://<host-ip>:8765
   ```

   Accept the self-signed certificate. Hard-refresh after updates: **Ctrl+Shift+R**.

## Ports

| Port | Service |
|------|---------|
| 8765 | Web UI (HTTPS when certs present) |
| 11434 | Ollama API (optional; exposed for debugging) |

Change host ports in `.env`: `WEB_PUBLISH_PORT`, `OLLAMA_PUBLISH_PORT`.

## Volumes (persisted on host)

| Path | Purpose |
|------|---------|
| `./data/` | SQLite logs, TLS certs, Whisper HF cache |
| `./models/piper/` | Piper voice ONNX |
| Docker volume `ollama_data` | Ollama models |

## Web login token (required by default)

The image runs `python bot.py --web` with **`WEB_AUTH_ENABLED=true`** by default. Before the container will accept traffic you must provide a token:

1. On the host (or in the image build context), run `bash scripts/generate_web_token.sh` so  
   `~/.config/jahash-secrets/sahir-web-token.txt` exists (or set `WEB_AUTH_TOKEN_FILE` to another path).
2. Mount that file into the container and set the env var, for example in `docker-compose.yml`:

   ```yaml
   environment:
     WEB_AUTH_TOKEN_FILE: /run/secrets/sahir-web-token
   volumes:
     - ${HOME}/.config/jahash-secrets/sahir-web-token.txt:/run/secrets/sahir-web-token:ro
   ```

   Or pass **`WEB_AUTH_TOKEN`** via a Docker secret / env (never commit it).

3. Open `https://<host-ip>:8765/login` and paste the token once (or use `/login?token=…` on a phone).

If the token is missing, the app **fails closed**: only `/login`, `/api/health`, and `/api/auth/status` respond; everything else returns **503** until a token is configured. `bot.py --web` still **exits at startup** with the same message (preferred for native/systemd).

For a trusted isolated lab only, set `WEB_AUTH_ENABLED=false` in `.env` (not recommended on LAN).

Entrypoints unchanged: **`Dockerfile` CMD** `python bot.py --web`, **`systemd`/`voice-assistant.sh`** — all require the token file or env when auth is enabled.

## Commands

```bash
docker compose ps
docker compose logs -f voice-assistant
docker compose restart voice-assistant
docker compose down
docker compose down -v   # also removes Ollama volume
```

Health check: `curl -k https://localhost:8765/api/health`

## GPU (optional)

```bash
docker compose -f docker-compose.yml -f docker-compose.gpu.yml up -d --build
```

Sets `WHISPER_DEVICE=cuda` and passes GPUs to Ollama.

## Cursor CLI (optional)

The container does **not** include `cursor-agent`. To enable Cursor routing, mount from the host:

```bash
# Edit paths in docker-compose.cursor.yml or use env vars:
export CURSOR_WORKSPACE_HOST=/path/to/your/project
export CURSOR_AGENT_BIN_HOST=/usr/local/bin/cursor-agent

docker compose -f docker-compose.yml -f docker-compose.cursor.yml up -d
```

## Bake models into the image (offline / air-gapped)

Larger image, no download on first run:

```bash
PRELOAD_WHISPER=1 docker compose build --no-cache
```

Or set `PRELOAD_WHISPER=1` in `.env` before `docker compose up --build`.

## What works in Docker

- Web UI, live + final transcription, chat routing via Ollama
- Piper TTS playback in browser
- SQLite request logs and voice commands
- HTTPS for LAN microphone access

## What does not (without host mounts)

- Push-to-talk hotkey / wake word (desktop-only)
- PC control (open apps, `pactl` volume)
- Cursor CLI (unless mounted via `docker-compose.cursor.yml`)

## Troubleshooting

| Issue | Fix |
|-------|-----|
| Mic blocked on phone/tablet | Use `https://` not `http://`; add LAN IP to `WEB_CERT_SAN`; regenerate certs |
| Ollama errors | `docker compose logs ollama`; ensure model pulled: `docker exec voice-assistant-ollama ollama pull llama3.1:8b` |
| Slow first transcription | Set `PRELOAD_WHISPER=1` and rebuild, or wait for HF download |
| Old UI in browser | Hard refresh (Ctrl+Shift+R) |

## Image targets

| Target | Tag (PoC) | Contents |
|--------|-----------|----------|
| **minimal** | `sahir-poc:minimal` | Agent stack, auth, router tiers, activity UI; `WEB_VOICE_ENABLED=false` (voice APIs return 503) |
| **full** | `sahir-poc:full` | minimal + `requirements-docker-voice.txt` (Whisper, Piper) |

```bash
docker build --target minimal -t sahir-poc:minimal .
docker build --target full -t sahir-poc:full .
```

`requirements-docker.txt` includes `sqlite-vec`, `pypdf`, and `requests` for the local-first agent layer.

## VPS proof of concept (local trial only)

`docker-compose.vps.yml` runs a **CPU-only** stack for GitHub #23 prep. It does **not** publish host ports **8765** or **11434** (native Sahir / host Ollama stay untouched). The app listens on **`127.0.0.1:8768`**.

| Piece | PoC choice |
|-------|------------|
| App | `minimal` image, non-root user `sahir` (uid 10001) |
| LLM | `ollama/ollama` on the compose network only (no host port) |
| Models | `ollama-init` pulls `qwen3:0.6b` + `nomic-embed-text` inside the trial volume (&lt;1 GB) |
| Auth | `WEB_AUTH_TOKEN_FILE=/run/secrets/web_token` — host file via `SAHIR_POC_SECRET_FILE` (never bake or commit) |
| TLS | Self-signed certs in the `sahir_vps_data` volume; production: **Caddy** (commented service in compose) or mount real certs read-only |

**Sizing (CPU VPS, indicative):** 2 vCPU, **4–8 GB RAM** for minimal + small models; allow **~6–10 GB disk** for Ollama volume + app image (minimal ≈ hundreds of MB; full adds ~1–2 GB for voice libs). Whisper/Piper add RAM at runtime — use **full** only when voice is required.

**Local smoke (from repo root):**

```bash
TOKEN_DIR=$(mktemp -d)
head -c 32 /dev/urandom | base64 | tr -d '/+=' | head -c 32 >"$TOKEN_DIR/web_token.txt"
export SAHIR_POC_SECRET_FILE="$TOKEN_DIR/web_token.txt"
timeout 900 docker compose -f docker-compose.vps.yml up -d --build
# wait for healthy app + completed ollama-init
curl -sk https://127.0.0.1:8768/api/health | head
curl -sk https://127.0.0.1:8768/api/projects   # expect 401
TOKEN=$(tr -d '\n' <"$TOKEN_DIR/web_token.txt")
curl -sk -H "Authorization: Bearer $TOKEN" https://127.0.0.1:8768/api/projects
curl -sk -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"message":"Say hi in one short sentence.","mode":"chat","route":true}' \
  https://127.0.0.1:8768/api/agent/chat
timeout 120 docker compose -f docker-compose.vps.yml down -v --rmi local
rm -rf "$TOKEN_DIR"
```

All trial resources are labelled `com.sahir.trial=jw12` and use project name **`sahir-vps-poc`**. Tear down with `docker compose -f docker-compose.vps.yml down -v --rmi local` and `docker image rm sahir-poc:minimal` if the tag remains. **Do not** run `docker system prune` on shared hosts.

Optional overrides: copy `.env.docker.vps.example` → `.env.docker.vps`.

### Still needed for a real VPS (pending — no spending in this task)

- [ ] Rent a VPS and static IP
- [ ] DNS A/AAAA record + firewall (443/22 only)
- [ ] Production TLS (Let's Encrypt via Caddy or cert mount)
- [ ] Off-site backups for `data/` and Ollama volume
- [ ] Monitoring, log rotation, upgrade process

## Security

The web UI uses **shared-token auth** when `WEB_AUTH_ENABLED=true` (default). Mount the token file or set `WEB_AUTH_TOKEN` via secrets — never commit tokens. For untrusted networks, keep auth on and terminate TLS at a reverse proxy.
