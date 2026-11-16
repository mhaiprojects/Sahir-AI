# Public release prep (Nov 2026)

How this repository was prepared for public release on GitHub, and what is still open. Open items are tracked as issues on the [project board](https://github.com/users/mhaiprojects/projects/1).

## Secret and privacy audit

- A regex scan of every tracked file and of the full git history (`git log -p --all`) found **no real secrets**: no API keys, access tokens, passwords or private keys.
- `.env`, `data/` (TLS certs, SQLite databases, logs, pid files), `venv/` and `models/` were never committed.
- Identifying (non-secret) values were removed from the tree: a hard-coded ClickUp list ID, absolute home-directory paths, the development machine's name, a personal first name in sample notes, example LAN IPs and specific hardware details.
- Matches that remain on purpose: the repository's own GitHub URL, a generic `/home/you/projects` example path, variable names such as `CLICKUP_API_TOKEN` / `CURSOR_API_KEY` that are read from the environment, and dummy tokens in test fixtures.

## Changes made

- **`.gitignore` / `.dockerignore` hardened:** `.env.*` (templates excepted), key and certificate files, `data/` (certs, logs, DBs), `*.db`/`*.sqlite*`, `models/`, editor folders. `COPY . .` in Docker can no longer bake certs or databases into an image.
- **ClickUp list ID** is no longer hard-coded: ClickUp counts as configured only when both `CLICKUP_API_TOKEN` and `CLICKUP_LIST_ID` are set in `.env`.
- **Placeholders** replace personal paths, machine names and LAN IPs (RFC 5737 addresses such as `192.0.2.10` or `<your-lan-ip>`).
- **History squashed** into a single public commit (`0012576`) authored by the GitHub no-reply identity, then force-pushed with lease. Old commits may stay reachable by SHA in GitHub caches for a while; a full purge needs a GitHub Support request.
- **License:** GPL-3.0-only (`LICENSE`, full official text).
- **README:** a prominent warning that the web UI has **no authentication** and binds `0.0.0.0` by default; clone directory and port examples fixed.

## Still open

- README and docs polish for public readers (non-Arch install, internal-flavoured wording): [#26](https://github.com/mhaiprojects/MH-VoiceAssistant-A/issues/26)
- Authentication in front of the web UI before any internet-facing deployment: [#23](https://github.com/mhaiprojects/MH-VoiceAssistant-A/issues/23)
- Re-run the secret scan on every new contribution before pushing (manual for now).
