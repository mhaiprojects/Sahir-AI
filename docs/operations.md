# Operations

## Service management (native)

```bash
cd Sahir-AI   # your clone directory
bash scripts/voice-assistant.sh start    # binds WEB_HOST=0.0.0.0
bash scripts/voice-assistant.sh stop
bash scripts/voice-assistant.sh restart
bash scripts/voice-assistant.sh status
bash scripts/voice-assistant.sh urls
```

PID file: `data/voice-assistant.pid`  
Logs: `data/logs/voice-assistant.log`, `data/logs/startup.log`

## Docker

```bash
docker compose up -d --build
docker compose logs -f voice-assistant
docker compose restart voice-assistant
docker compose down
```

Health: `curl -k https://localhost:8765/api/health`

## Tests

```bash
source venv/bin/activate   # native
python scripts/test_web.py
python scripts/smoke_test.py
python scripts/test_audio_decode.py
```

Run `test_web.py` with server up (native or Docker).

## Log monitoring

### Checkpoint-based error check

`scripts/error_check.sh`:

- Reads new lines since `data/logs/loop-checkpoint.line`
- **Errors:** lines matching `[ERROR]` or `Traceback (most recent call last)`
- **Not errors:** `WARNING` lines (e.g. partial WebM skip) — intentionally excluded after false-positive fixes
- Exit `0` = OK, `2` = errors found
- `--notify` sends `notify-send` if available

```bash
bash scripts/error_check.sh
bash scripts/error_check.sh --notify
```

### Silent 10-second loop (Cursor `/loop`)

Background shell pattern:

```bash
while true; do
  sleep 10
  echo 'AGENT_LOOP_TICK_error_silent {"prompt":"..."}'
done
```

Agent runs `error_check.sh` on tick; **only reports in chat when exit code is 2**.

Stop: `kill $(cat data/logs/loop.pid)` (if PID saved there).

### Log watch (manual)

`scripts/log_watch.sh` — tail checkpoint watcher for human use.

## Environment reload

After `.env` changes:

```bash
bash scripts/voice-assistant.sh restart
# or
docker compose restart voice-assistant
```

After **frontend JS/CSS** changes, users must **hard refresh** (`Ctrl+Shift+R`) or bump cache-bust query in `index.html` (e.g. `app.js?v=YYYYMMDD`).

## Access URLs

- Local: `https://127.0.0.1:8765` or `http://127.0.0.1:8765` (mic on localhost works over HTTP)
- LAN: `https://<host-ip>:8765` — **HTTPS required** for microphone from non-localhost clients
