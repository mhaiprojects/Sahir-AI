# ClickUp integration

Sahir can create tasks, add comments, update status, and list recent tasks in a ClickUp list when you mention ClickUp or delegate to the `clickup` worker.

## Credentials

1. In ClickUp, open **Settings → Apps → API Token** and create a personal token.
2. Find your **list id**: open the list in the browser; the URL often contains `list_id` or use ClickUp's API/docs for the list UUID.
3. Configure Sahir (pick one token source):
   - **`.env`** on the server (not committed): `CLICKUP_API_TOKEN=pk_...` and `CLICKUP_LIST_ID=...`
   - **Token file** (recommended on a shared host):  
     `~/.config/jahash-secrets/clickup-token.txt` with mode **600**  
     Set `CLICKUP_TOKEN_FILE` if you use a different path.
4. Optional: `CLICKUP_TEAM_ID` if auto-discovery is not desired.
5. Restart the Sahir user service after changing env:
   ```bash
   systemctl --user restart voice-assistant.service
   ```

## Verify in the UI

Open **Settings → Integrations → ClickUp**. When configured, use **Verify** (read-only `GET /user` and `GET /list/{id}`).

API:

- `GET /api/integrations/clickup/status` — no secrets; shows configured/token source/last error.
- `POST /api/integrations/clickup/verify` — read-only connectivity check.

## End-to-end script (real API)

When credentials are set:

```bash
timeout 120 venv/bin/python scripts/clickup_e2e.py --yes
```

Without `--yes` or without credentials the script does not create tasks and prints `SKIP: not configured`.

## Voice and numbered actions

Creating or updating ClickUp resources is **side-effecting**. Numbered-list chips and voice **“do action N”** use the same confirmation flow as `run_command` (UI confirm dialog; voice yes/no).

Delegated turns that include the ClickUp worker should reply with the created **task name and URL** from the API response.