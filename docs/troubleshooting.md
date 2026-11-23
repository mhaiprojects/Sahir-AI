# Troubleshooting

## WebM decode / "Could not decode audio"

### Symptom

Logs show:

```
WARNING audio_decode: Audio decode attempt failed ...
WARNING web_server: Live transcribe skipped partial audio ...
```

Or invalid magic bytes (`C\xb6u\x01`, `C\xc3\x81\x00`) instead of WebM header `\x1aE\xdf\xa3`.

### Cause

Always-listening uses one continuous `MediaRecorder`. Partial blobs sent to `/api/transcribe/live` may lack a valid WebM container header. Slicing without prepending header chunks produces undecodable uploads.

### Server behavior (current)

- Live transcribe: partial decode → **WARNING** + `skipped: partial_audio` (not HTTP 400)
- Always-listening final failure → graceful `noise_discard` (not ERROR traceback)

### Frontend fixes applied

- Single continuous recorder for always-listening (removed broken preroll merge)
- `conversationChunkStart` for pre-roll slicing
- `chunksForUpload()` prepends header chunks
- Skip live transcribe when `conversationChunkStart >= 0` in some paths
- `finishAlwaysConversationRecording()` uses full blob on stop

### If issues persist

1. **Hard refresh browser** (`Ctrl+Shift+R`) — cached old `app.js` still sends bad live requests
2. Verify `index.html` loads current `app.js?v=...` in DevTools → Network
3. Consider: Web Audio PCM capture, or stop/restart recorder per utterance instead of continuous MediaRecorder

## error_check.sh false positives

Do **not** grep for plain text `Could not decode audio` — WARNING lines contain that string but are handled gracefully. Only match `[ERROR]` and `Traceback`.

## Ollama unreachable

- Native: `systemctl status ollama`, `ollama pull llama3.1:8b`
- Docker: `docker compose logs ollama`, check `OLLAMA_HOST=http://ollama:11434` inside container

## Web login / 401 on every request

- Ensure `bash scripts/generate_web_token.sh` has been run and `WEB_AUTH_TOKEN_FILE` points at the file (default `~/.config/jahash-secrets/sahir-web-token.txt`).
- Open `/login` and paste the token, or use a one-time `/login?token=…` link on your phone (HTTPS).
- Scripts: `Authorization: Bearer <token>` or `WEB_AUTH_TOKEN` in the environment (not in committed `.env`).
- `WEB_AUTH_ENABLED=false` disables auth entirely (trusted LAN only).
- Server refuses to start when auth is enabled but the token file is missing — run `generate_web_token.sh` first.

## Cursor CLI delegation hangs

- Long tasks run with `CURSOR_TIMEOUT_SECONDS` (default 300)
- No log lines while agent runs — normal
- Requires `cursor-agent` on PATH and valid login; optional in Docker via compose override

## Cursor CLI rejects shell commands (Cursor mode)

### Symptom

Activity panel shows **`error`** lines like `Cursor declined to run: echo … — …` while the spoken reply may still describe what the agent *would* have run. `stream-json` tool results use `result.rejected` with an empty `reason`.

### Cause

In non-interactive **`cursor-agent -p`** mode, shell tools follow **`~/.cursor/cli-config.json`** `permissions.allow` / `permissions.deny` and Auto-review. A narrow allowlist (for example only `Shell(ls)`) rejects other commands (`echo`, `git`, …) with no interactive approval prompt.

Sahir already passes **`--trust`** for the workspace. That does not override shell allowlists.

### Fix in Sahir (defaults)

- **`CURSOR_AGENT_FORCE=true`** (default): adds **`--force`** so print-mode agents can run shell tools unless explicitly denied.
- Optional **`CURSOR_AGENT_SANDBOX=enabled|disabled`** → `cursor-agent --sandbox …`
- Optional **`CURSOR_AGENT_AUTO_REVIEW=true`** → server Auto-review classifier (still pair with `--force` for unattended use)

Set **`CURSOR_AGENT_FORCE=false`** only if you want strict allowlist behavior; widen **`permissions.allow`** (e.g. `Shell(echo)` or broader patterns) instead.

### Verify

```bash
TMP=$(mktemp -d)
venv/bin/python -c "from pathlib import Path; from tools.cursor_cli import run_cursor_agent; print(run_cursor_agent('Run: echo ok && ls', workspace=Path('$TMP'), model='composer-2.5', timeout_seconds=120).text)"
rm -rf "$TMP"
```

Check the activity panel / stream-json for `shell rejected` vs `shell ok`.

## Mic blocked on phone/tablet

- Must use **HTTPS** with cert SAN including server LAN IP
- Regenerate: `WEB_CERT_SAN='DNS:localhost,IP:127.0.0.1,IP:<your-lan-ip>' FORCE=1 bash scripts/generate_web_cert.sh`
- Accept self-signed cert in browser

## Empty transcription

- VAD may remove all audio (silence/noise) — log shows `VAD filter removed ... of audio`
- Not an error; user may need to speak louder or adjust silence thresholds

## First-word capture (always-listening)

Addressed by continuous recorder + chunk slicing with header preservation; if clipped, check `conversationChunkStart` logic in `app.js`.

## Recommended follow-ups (from sessions) — done Nov 2026

1. ~~Cache-bust static assets automatically on server restart~~ — `build_id` in `/api/health`, `index.html` no-cache
2. ~~Auto browser UI refresh after server-side code update~~ — UI reloads on `build_id` change
3. ~~Persist browser settings (mic enabled, route toggles) in `localStorage`~~
4. ~~Cursor completion message always returned to UI~~
5. ~~systemd user unit for auto-start on boot~~ — `bash scripts/install_systemd.sh [--enable]`

## "cursor-agent was not found" but it is installed

Python raises `FileNotFoundError` when the subprocess `cwd` (`CURSOR_WORKSPACE`) is missing, e.g. after moving the repo. The workspace now falls back to the project's parent folder and a missing workspace reports a clear message.
