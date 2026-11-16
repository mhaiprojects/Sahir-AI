# Resume notes: picking the project back up (Nov 2026)

What was found and fixed when development resumed after the repository had been moved to a new folder. Useful as a checklist when moving or cloning the project elsewhere.

## Problems found

- **Stale workspace path:** `CURSOR_WORKSPACE` (and a hard-coded default) pointed at the old folder, so the Cursor worker's `subprocess` call failed with `FileNotFoundError` for the missing working directory, reported as "cursor-agent was not found".
- **systemd units** hard-coded the old absolute path and were not installed.
- **venv console scripts** (`venv/bin/piper`, `activate`) keep the old path in their shebangs after a move; call `venv/bin/python` directly or recreate the venv.
- **Port clash:** the default port 8765 was taken by an unrelated process, so the app was run on 8766 ([#21](https://github.com/mhaiprojects/MH-VoiceAssistant-A/issues/21)).
- **TLS certificate SANs** listed an old LAN address ([#20](https://github.com/mhaiprojects/MH-VoiceAssistant-A/issues/20)).

## Fixes made

- A missing or stale `CURSOR_WORKSPACE` now falls back to the project's parent folder with a clear error, and `cursor-agent` is also found in `~/.local/bin`.
- WebM robustness: truncated WebM is salvaged; header-only, headerless and tiny chunks raise `PartialAudioError` with one warning and a friendly 400 for push-to-talk.
- `/api/health` reports a `build_id`; `index.html` is served no-cache; the UI auto-reloads when the build changes and handles lost/restored connections.
- Mic permission prompt via the Permissions API; the mic preference and "Route via Tools" are remembered.
- systemd units use an `@ROOT@` placeholder filled in by `scripts/install_systemd.sh`, which enables them only with `--enable` ([#22](https://github.com/mhaiprojects/MH-VoiceAssistant-A/issues/22)).
- `voice-assistant.sh` honours a pre-set `WEB_PORT` and guards against PID reuse.
- `scripts/test_web.py` covers a Piper → Whisper round trip and malformed WebM in every mode.

## Moving or cloning checklist

1. Update `CURSOR_WORKSPACE` and `ALLOWED_DIRECTORIES` in `.env`.
2. Recreate the venv if you need its console scripts.
3. Re-run `scripts/install_systemd.sh` so the units get the new path.
4. Regenerate the TLS cert if the host name or address changed.
5. Run the smoke test and `scripts/test_web.py`.
