# Operations

## Service management (native)

### Script mode (default until systemd is enabled)

```bash
cd Sahir-AI   # your clone directory
bash scripts/voice-assistant.sh start    # binds WEB_HOST=0.0.0.0
bash scripts/voice-assistant.sh stop
bash scripts/voice-assistant.sh restart
bash scripts/voice-assistant.sh status
bash scripts/voice-assistant.sh urls
```

- PID file: `data/voice-assistant.pid`
- Logs: `data/logs/voice-assistant.log`, `data/logs/startup.log`
- **Port:** uses `WEB_PORT` from `.env` only — if that port is busy the script **exits with an error** and prints what is listening (`ss -tlnp`). Use an explicit override only when you mean it: `WEB_PORT=8767 bash scripts/voice-assistant.sh start`.
- **Web auth:** `bash scripts/generate_web_token.sh` before first start when auth is enabled.

### systemd user mode (recommended after switch-over)

```bash
bash scripts/install_systemd.sh           # render units to ~/.config/systemd/user
bash scripts/install_systemd.sh --enable  # after stopping script-managed server
bash scripts/install_systemd.sh --status
```

When `voice-assistant-web.service` is **enabled**, `voice-assistant.sh start|stop|restart|status` calls **`systemctl --user`** — one server, no double start.

Logs:

```bash
journalctl --user -u voice-assistant-web.service -f
journalctl --user -u voice-assistant.service -f   # desktop PTT/wake, if enabled
```

**Linger** (start at boot without a graphical login): should be `yes` for your user:

```bash
loginctl show-user "$USER" -p Linger
# enable once: loginctl enable-linger "$USER"
```

**Reboot test procedure**

1. `bash scripts/voice-assistant.sh stop` (or disable script mode).
2. `bash scripts/install_systemd.sh --enable` (only when port free and token file exists).
3. `systemctl --user is-enabled voice-assistant-web.service` → `enabled`.
4. Reboot → `loginctl show-user "$USER" -p Linger` still `yes` → `curl -k https://127.0.0.1:8765/api/health` → log in at `/login`.
5. `journalctl --user -u voice-assistant-web.service -b` for first-boot errors.

### Tailscale Serve (tailnet :8443)

Broken config often proxies **:8443** → `127.0.0.1:8766` while Sahir listens on **8765**. Fix (tailnet only; do not alter public Funnel on **:443** / port **8091**):

```bash
tailscale serve --bg --https=8443 https+insecure://127.0.0.1:8765
```

Sahir requires web login after Item 16; use the same token as LAN HTTPS.

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
