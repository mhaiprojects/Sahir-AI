#!/usr/bin/env bash
# Create the shared web UI auth token file (not stored in .env or the repo).
set -euo pipefail

SHOW=0
ROTATE=0
for arg in "$@"; do
  case "$arg" in
    --show) SHOW=1 ;;
    --rotate) ROTATE=1 ;;
    -h|--help)
      echo "Usage: $0 [--show] [--rotate]"
      echo "  Creates WEB_AUTH_TOKEN_FILE if missing (default ~/.config/jahash-secrets/sahir-web-token.txt)"
      echo "  --show    Print the token (otherwise only the path is printed)"
      echo "  --rotate  Replace an existing token (invalidates browser sessions)"
      exit 0
      ;;
    *) echo "Unknown option: $arg" >&2; exit 2 ;;
  esac
done

TOKEN_FILE="${WEB_AUTH_TOKEN_FILE:-$HOME/.config/jahash-secrets/sahir-web-token.txt}"
mkdir -p "$(dirname "$TOKEN_FILE")"

if [[ -f "$TOKEN_FILE" && "$ROTATE" != "1" && "$SHOW" != "1" ]]; then
  echo "Token file already exists: $TOKEN_FILE"
  echo "Use --show to display it, or --rotate to replace it."
  exit 0
fi

if [[ "$SHOW" == "1" ]]; then
  if [[ ! -f "$TOKEN_FILE" ]]; then
    echo "No token file at $TOKEN_FILE — run $0 without --show first." >&2
    exit 1
  fi
  cat "$TOKEN_FILE"
  exit 0
fi

umask 077
# 48 bytes → ~64 url-safe chars; never echo to stdout unless --show
(tr -dc 'A-Za-z0-9' </dev/urandom | head -c 64; echo) > "$TOKEN_FILE"
chmod 600 "$TOKEN_FILE"
echo "Wrote web auth token to $TOKEN_FILE"
echo "Display with: $0 --show   (or: cat \"$TOKEN_FILE\")"