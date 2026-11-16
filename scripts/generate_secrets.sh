#!/usr/bin/env bash
# Create .env from template; never commit the result (.env is gitignored).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

MODE="${1:-native}"   # native | docker

pick_template() {
  if [[ "$MODE" == "docker" ]]; then
    echo ".env.docker.example"
  else
    echo ".env.example"
  fi
}

TEMPLATE="$(pick_template)"
TARGET=".env"

if [[ -f "$TARGET" && "${FORCE:-0}" != "1" ]]; then
  echo ".env already exists — skipping (set FORCE=1 to overwrite template fields)."
  exit 0
fi

cp "$TEMPLATE" "$TARGET"

# Relative cert paths (work in repo root and Docker /app)
if grep -q '^WEB_SSL_CERT=' "$TARGET"; then
  sed -i 's|^WEB_SSL_CERT=.*|WEB_SSL_CERT=data/certs/web.crt|' "$TARGET" 2>/dev/null || \
    sed -i '' 's|^WEB_SSL_CERT=.*|WEB_SSL_CERT=data/certs/web.crt|' "$TARGET"
fi
if grep -q '^WEB_SSL_KEY=' "$TARGET"; then
  sed -i 's|^WEB_SSL_KEY=.*|WEB_SSL_KEY=data/certs/web.key|' "$TARGET" 2>/dev/null || \
    sed -i '' 's|^WEB_SSL_KEY=.*|WEB_SSL_KEY=data/certs/web.key|' "$TARGET"
fi

# Uncomment SSL lines if commented in native template
if [[ "$MODE" == "native" ]]; then
  sed -i 's|^# WEB_SSL_CERT=|WEB_SSL_CERT=|' "$TARGET" 2>/dev/null || \
    sed -i '' 's|^# WEB_SSL_CERT=|WEB_SSL_CERT=|' "$TARGET"
  sed -i 's|^# WEB_SSL_KEY=|WEB_SSL_KEY=|' "$TARGET" 2>/dev/null || \
    sed -i '' 's|^# WEB_SSL_KEY=|WEB_SSL_KEY=|' "$TARGET"
fi

mkdir -p data/logs data/certs models/piper

echo "Created ${TARGET} from ${TEMPLATE}"
echo "Edit ${TARGET} to set CURSOR_API_KEY, OLLAMA_MODEL, and ALLOWED_* paths for your machine."
