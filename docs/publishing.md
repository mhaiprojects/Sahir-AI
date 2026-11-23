# Publishing docs and the public repo

Repository: `https://github.com/mhaiprojects/Sahir-AI` (public).

## Secret scan (required)

```bash
bash scripts/secret_scan.sh
```

Uses `detect-secrets` (baseline `.secrets.baseline`) and optional `gitleaks` (working tree + full history).  
Findings are printed **redacted** — never paste tokens into issues or docs.

After rotating a web token, run `scripts/scrub_log_secrets.py` on `data/logs/` if legacy access lines may contain `?token=` query values.

If history contains a real leaked secret, **do not force-push** without maintainer approval; remove from the tree and rotate the credential.

## Link check

```bash
venv/bin/python scripts/check_doc_links.py --strict
```

## Local documentation site (MkDocs)

```bash
venv/bin/pip install mkdocs-material
venv/bin/mkdocs build --strict
```

Output: `site/` (gitignored).

## GitHub Pages (manual enable — Michael)

The workflow **does not** enable Pages automatically.

1. Maintainer: **Settings → Pages → Build and deployment → Source: GitHub Actions**
2. Run workflow: **Actions → Pages docs → Run workflow** (`workflow_dispatch` only)
3. Site URL (expected): `https://mhaiprojects.github.io/Sahir-AI/`

Until Pages is enabled, only local `site/` builds exist.

## Public doc hygiene

- No home paths (`/home/user/…`), real LAN IPs, or tailnet hostnames — use `192.0.2.10`, `<host-ip>`, `<tailnet-host>`
- No tokens, API keys, or live screenshots with account data
- Copy `docs/examples/` for sample config; keep machine-specific files in gitignored `data/` and `models/`