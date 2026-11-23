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

## Security

No authentication on the web UI. Use only on trusted home networks or put a reverse proxy with auth in front.
