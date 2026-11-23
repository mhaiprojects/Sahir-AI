# Sahir AI documentation

**Sahir** is a local-first, private voice assistant: Whisper STT, Piper (or optional Kokoro) TTS,
Ollama routing, optional agent orchestration with projects and supervisors, and LAN HTTPS web UI.

## Quick links

- [Install and run on GitHub](https://github.com/mhaiprojects/Sahir-AI/blob/main/README.md) — clone, Docker or native setup, login token
- [Docker guide](docker-quickstart.md) — compose stack on another machine
- [Security](security-public.md) — LAN exposure, tokens, TLS, integrations
- [Features](features.md) — voice paths, router tiers, workers, integrations
- [Screenshot gallery](gallery.md) — static UI captures (no live data)

## Public vs operator docs

| Audience | Location |
|----------|----------|
| End users & contributors | This site, `README.md`, `docs/*.md` |
| AI coding agents (internal) | [for-ai-agents.md](for-ai-agents.md) — minimal, no secrets |

Before pushing to the public GitHub repo, run:

```bash
bash scripts/secret_scan.sh
venv/bin/python scripts/check_doc_links.py --strict
```