# Email integration

Sahir can search and read mail over IMAP, summarise unread messages, save draft replies in the local agent database, and send drafts over SMTP after explicit confirmation (UI chip or voice yes/no).

## Gmail / Google Workspace

Google blocks normal passwords on IMAP/SMTP. Create an **App Password** (Google Account → Security → 2-Step Verification → App passwords) and use it as `EMAIL_PASSWORD` or in a mode-600 file. Typical hosts:

- `EMAIL_IMAP_HOST=imap.gmail.com` / `EMAIL_IMAP_PORT=993`
- `EMAIL_SMTP_HOST=smtp.gmail.com` / `EMAIL_SMTP_PORT=587` (STARTTLS) or port 465 with `EMAIL_SMTP_SSL=true`

## Configuration

| Variable | Purpose |
|----------|---------|
| `EMAIL_IMAP_HOST`, `EMAIL_IMAP_PORT` | Read/search (SSL) |
| `EMAIL_SMTP_HOST`, `EMAIL_SMTP_PORT` | Send (`EMAIL_SMTP_SSL=true` for SMTPS) |
| `EMAIL_USERNAME` | Login user |
| `EMAIL_FROM` | From header (defaults to username) |
| `EMAIL_PASSWORD` or `EMAIL_PASSWORD_FILE` | Password (file must be mode **600**) |
| `EMAIL_IMAP_DRAFTS_FOLDER` | Optional: also append draft to this IMAP folder |
| `EMAIL_SEND_ALLOWLIST` | Optional comma-separated recipients; others are refused |

## Settings UI

**Settings → Integrations → Email** shows configured vs needs-credentials env names. **Verify** runs a read-only IMAP login, `SELECT INBOX`, logout.

## HTTP API

- `GET /api/integrations/email/status` — no secrets returned
- `POST /api/integrations/email/verify` — read-only mailbox check

## Safety

Sending is **always side-effecting**: numbered-list **Send draft** chips and voice **do action N** require confirmation showing recipients and subject. The email worker rejects direct `send_draft` without that path.

## Worker

Delegate to the `email` worker (or mention inbox / unread mail). Actions: `search`, `read`, `summarize`, `draft_reply`, `draft_new`, and `send_draft` (via confirmation only).