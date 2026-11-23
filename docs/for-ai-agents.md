# Guide for AI agents

Orientation for contributors and automation working on this repository.

## Project identity

- **Name:** Sahir
- **Remote:** `git@github.com:mhaiprojects/Sahir-AI.git` (public — never commit secrets)
- **Primary mode for LAN/Docker:** `python bot.py --web` (FastAPI + static SPA)

## Before changing code

1. Read [architecture.md](./architecture.md) and [troubleshooting.md](./troubleshooting.md)
2. Check `.env.example` — never commit `.env`, `data/certs/`, or token files
3. Web mic requires HTTPS on LAN — cert SAN must include the host IP you use on phones
4. After JS/CSS changes: bump `?v=` query params in `index.html`; users may need a hard refresh
5. Before push: `bash scripts/secret_scan.sh`

## Safe change patterns

- **Minimal diffs** — match style in `app.js`, `web_server.py`, `tools/router.py`
- **Tests:** `venv/bin/python -m pytest -q`, `timeout 600 venv/bin/python scripts/test_web.py` (throwaway server)
- **venv:** prefer `venv/bin/python` in scripts and docs

## Common requests → files

| Request | Where to look |
|---------|----------------|
| Live transcription | `web/static/app.js`, `web_server.py` live endpoints |
| UI layout | `index.html`, `unified.css`, `panels.css`, `chatbar.js` |
| Router / tiers | `tools/router.py`, `agent/dispatcher.py`, `config.py` |
| Projects / supervisor | `agent/store.py`, `agent/supervisor.py`, `web/` APIs |
| Voice / Wyoming | `web/voice_ws.py`, `wyoming_server/` |
| Docker | `DOCKER.md`, `docker-compose.yml`, `setup.sh docker` |

## Do not

- Commit secrets, real LAN IPs, or machine-specific paths in docs
- Force-push `main` without maintainer approval
- Disable auth guidance for LAN deployments
- Enable GitHub Pages or publish docs without the checklist in [publishing.md](./publishing.md)

## Related docs

- [security-public.md](./security-public.md) — tokens, HTTPS, integrations
- [publishing.md](./publishing.md) — secret scan, MkDocs, Pages workflow