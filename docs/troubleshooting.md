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

## Cursor CLI delegation hangs

- Long tasks run with `CURSOR_TIMEOUT_SECONDS` (default 300)
- No log lines while agent runs — normal
- Requires `cursor-agent` on PATH and valid login; optional in Docker via compose override

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
