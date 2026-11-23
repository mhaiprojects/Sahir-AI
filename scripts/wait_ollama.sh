#!/usr/bin/env bash
# Wait for Ollama HTTP API before starting Sahir (non-fatal for systemd ExecStartPre).
set -euo pipefail

host="${OLLAMA_HOST:-http://127.0.0.1:11434}"
host="${host%/}"
url="${host}/api/tags"

for _ in $(seq 1 45); do
  if curl -fsS --max-time 2 "$url" >/dev/null 2>&1; then
    exit 0
  fi
  sleep 1
done
# Non-fatal: Sahir can still start and report Ollama down in /api/health.
exit 0