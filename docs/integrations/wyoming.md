# Wyoming protocol (Home Assistant / satellites)

Sahir can expose **STT**, **TTS**, and optionally **agent handle** on the [Wyoming protocol](https://github.com/OHF-Voice/wyoming) (MIT). This is a **separate process** from the web UI (`8765`); it does not touch Ollama’s port or the main Sahir server unless you enable handle (agent LLM).

## Security

Wyoming has **no shared-secret auth**. Treat it like an unauthenticated RPC:

- Default bind: `tcp://127.0.0.1:10300` (loopback only).
- Binding to `0.0.0.0` or a LAN address requires `WYOMING_ALLOW_LAN=true` in `.env`, or the server refuses to start.
- Use a firewall/VLAN or HA host-only networking if you expose STT/TTS on the LAN.
- **Handle** (`WYOMING_HANDLE_ENABLED=true`) runs the Sahir agent with `side_effects=confirm`. Side-effecting tools are **not executed** (no shell, Cursor/code worker, ClickUp create, calendar create, supervisor spawn/move, etc.); they are stored as **pending confirmations** in the Wyoming chat for approval in the Sahir app. The satellite hears *“Please confirm in the Sahir app.”* Read-only paths (answers, research/RAG, calc, inbox/calendar **list/availability**) still run.

## Run manually

```bash
# Loopback STT+TTS only (default)
venv/bin/python -m wyoming_server

# Custom port
venv/bin/python -m wyoming_server --uri tcp://127.0.0.1:10300

# LAN (explicit)
export WYOMING_URI=tcp://0.0.0.0:10300
export WYOMING_ALLOW_LAN=true
venv/bin/python -m wyoming_server
```

## Environment

| Variable | Default | Meaning |
|----------|---------|---------|
| `WYOMING_URI` | `tcp://127.0.0.1:10300` | TCP listen address |
| `WYOMING_ALLOW_LAN` | `false` | Allow non-loopback bind |
| `WYOMING_HANDLE_ENABLED` | `false` | Advertise and run Sahir **handle** (agent) |
| `WYOMING_MAX_AUDIO_SECONDS` | `30` | Max ASR audio per request |
| `WYOMING_PROJECT_NAME` | `Wyoming` | Agent DB project for handle turns |
| `WYOMING_CONVERSATION_TITLE` | `Home Assistant` | Chat title under that project |

STT/TTS reuse the same env as the web app (`WHISPER_*`, `PIPER_*`, `TTS_BACKEND`, etc.).

## Systemd (user unit template)

`systemd/voice-assistant-wyoming.service` is a **template only**—copy/install with `scripts/install_systemd.sh` if you use user units. Do **not** enable it unless you intend to run Wyoming alongside Sahir.

```bash
# Example (after install replaces @ROOT@)
systemctl --user daemon-reload
systemctl --user start voice-assistant-wyoming   # optional
```

## Home Assistant

1. **Settings → Devices & services → Add integration → Wyoming Protocol**
2. Host: machine running Sahir (often `127.0.0.1` if HA is on the same box).
3. Port: `10300` (or your `WYOMING_URI` port).
4. Add **Wyoming Speech-to-text** and **Wyoming Text-to-speech** services from the discovered device.
5. Optional conversation agent: only if `WYOMING_HANDLE_ENABLED=true` and you accept the side-effect policy above.

## wyoming-satellite

Point a [wyoming-satellite](https://github.com/rhasspy/wyoming-satellite) instance at the same host/port for mic/speaker endpoints; HA chains satellite → Sahir Wyoming for STT/TTS.

## Verify

```bash
timeout 600 venv/bin/python scripts/wyoming_e2e.py
```

E2E starts the server on a random loopback port, runs `describe`, TTS→STT overlap, and handle off/on with a temp agent DB.