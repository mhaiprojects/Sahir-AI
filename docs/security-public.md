# Security (public deployments)

Sahir is designed for **your LAN**, not the open internet. Treat the web UI like an admin console.

## Network exposure

- Default bind: `WEB_HOST=0.0.0.0` — reachable from your LAN.
- **Do not port-forward** the web UI to the public internet without a proper reverse proxy, firewall, and strong auth.
- Wyoming server (Home Assistant): **loopback only** by default (`127.0.0.1:10300`); set `WYOMING_ALLOW_LAN=1` only if you understand the risk.

## Authentication (web UI)

- **On by default:** `WEB_AUTH_ENABLED=true`
- Shared secret in a **token file** (not `.env`): default `~/.config/jahash-secrets/sahir-web-token.txt` (mode `600`)
- Generate: `bash scripts/generate_web_token.sh`
- **Login page** `/login` sets an HTTP-only session cookie (~30 days)
- **API / scripts:** `Authorization: Bearer <token>` or env `WEB_AUTH_TOKEN` for automation
- **Rotate:** `bash scripts/generate_web_token.sh --rotate` — re-login every device
- **Phone one-time links:** `https://<host>:8765/login?token=…` still work but land in **browser history** and may hit **access logs** (values are redacted at log time; scrub old files with `scripts/scrub_log_secrets.py`). Prefer pasting the token on the login page (POST).

Never commit tokens, `.env`, TLS private keys, or files under `data/`.

## HTTPS and microphones

Browsers require **HTTPS** (or `localhost`) for microphone access.

- Run `bash scripts/generate_web_cert.sh` and set `WEB_SSL_CERT` / `WEB_SSL_KEY` in `.env`
- Certs are **self-signed** — trust once per device or add SANs for your LAN IP (`WEB_CERT_SAN`, `EXTRA_SANS`)
- Optional **Tailscale** serve: docs in [deployment.md](deployment.md) — agent never runs `tailscale` for you

## Integrations (credentials)

| Integration | Config | Notes |
|-------------|--------|--------|
| ClickUp | `CLICKUP_API_TOKEN`, `CLICKUP_LIST_ID` | Optional worker |
| Email (SMTP/IMAP) | `EMAIL_*` in `.env` | Optional |
| Calendar (CalDAV) | `CALENDAR_CALDAV_*` | Optional |
| OpenAI-compatible LLM | `OPENAI_API_KEY` / file | Optional backend |
| Cursor CLI | `cursor-agent login`, workspace path | Local coding tasks |

Use file-based secrets (`*_API_KEY_FILE`) when possible.

## What Sahir can do on your PC

Router and agent tools may run shell commands, open apps, or delegate to **Cursor CLI** when you ask.
Use voice/text carefully; prefer confirm mode for side effects (`side_effects=confirm` on Wyoming handle).

## Before each public push

```bash
bash scripts/secret_scan.sh
```

See [publishing.md](publishing.md) for GitHub Pages and the public repo checklist.