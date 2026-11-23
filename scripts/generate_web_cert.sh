#!/usr/bin/env bash
# Generate a self-signed TLS certificate for LAN microphone access over HTTPS.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CERT_DIR="${ROOT}/data/certs"
VENV_PY="${ROOT}/venv/bin/python"
PY="${VENV_PY}"
[[ -x "$PY" ]] || PY="$(command -v python3)"

KEEP_PREV=0
CHECK_ONLY=0
FORCE="${FORCE:-0}"

usage() {
  echo "Usage: $(basename "$0") [--check] [--keep-prev] [cn] [days]"
  echo "  --check      Compare web.crt SANs to this machine; exit 1 if stale (no writes)"
  echo "  --keep-prev  Save current web.crt/key as web.crt.prev / web.key.prev before replace"
  echo "  cn           Common Name (default: WEB_CERT_CN or voice-assistant.local)"
  echo "  days         Validity (default 825, max for iOS trust)"
  echo "  EXTRA_SANS   Comma-separated extra DNS/IP entries (env)"
  echo "  FORCE=1      Regenerate even if web.crt exists"
  exit 0
}

ARGS=()
for arg in "$@"; do
  case "$arg" in
    --check) CHECK_ONLY=1 ;;
    --keep-prev) KEEP_PREV=1 ;;
    -h|--help) usage ;;
    *) ARGS+=("$arg") ;;
  esac
done

HOST="${ARGS[0]:-${WEB_CERT_CN:-voice-assistant.local}}"
DAYS="${ARGS[1]:-${WEB_CERT_DAYS:-825}}"
if [[ "$DAYS" -gt 825 ]]; then
  echo "Capping validity at 825 days (iOS trusts self-signed certs up to ~825 days)." >&2
  DAYS=825
fi

mkdir -p "${CERT_DIR}"

if [[ "$CHECK_ONLY" == "1" ]]; then
  exec "$PY" "${ROOT}/scripts/web_cert_sans.py" --check --cn "$HOST"
fi

if [[ -f "${CERT_DIR}/web.crt" && -f "${CERT_DIR}/web.key" && "${FORCE}" != "1" ]]; then
  echo "Certificates already exist in ${CERT_DIR} (set FORCE=1 to regenerate)."
  "$PY" "${ROOT}/scripts/web_cert_sans.py" --check --cn "$HOST" || true
  exit 0
fi

SAN_CMD=("$PY" "${ROOT}/scripts/web_cert_sans.py" --print-sans --cn "$HOST")
if [[ -n "${EXTRA_SANS:-}" ]]; then
  SAN_CMD+=(--extra "$EXTRA_SANS")
fi
SAN="$("${SAN_CMD[@]}")"
if [[ -z "$SAN" ]]; then
  echo "Could not compute SAN list." >&2
  exit 1
fi

if [[ "$KEEP_PREV" == "1" ]]; then
  if [[ -f "${CERT_DIR}/web.crt" ]]; then
    cp -f "${CERT_DIR}/web.crt" "${CERT_DIR}/web.crt.prev"
  fi
  if [[ -f "${CERT_DIR}/web.key" ]]; then
    cp -f "${CERT_DIR}/web.key" "${CERT_DIR}/web.key.prev"
  fi
fi

openssl req -x509 -newkey rsa:4096 \
  -keyout "${CERT_DIR}/web.key" \
  -out "${CERT_DIR}/web.crt" \
  -days "${DAYS}" -nodes \
  -subj "/CN=${HOST}/O=Sahir/C=AU" \
  -addext "subjectAltName=${SAN}"

chmod 600 "${CERT_DIR}/web.key"
chmod 644 "${CERT_DIR}/web.crt"

ENV_FILE="${ROOT}/.env"
if [[ -f "${ENV_FILE}" ]]; then
  if grep -q '^WEB_SSL_CERT=' "${ENV_FILE}"; then
    sed -i "s|^WEB_SSL_CERT=.*|WEB_SSL_CERT=data/certs/web.crt|" "${ENV_FILE}" 2>/dev/null || \
      sed -i '' "s|^WEB_SSL_CERT=.*|WEB_SSL_CERT=data/certs/web.crt|" "${ENV_FILE}"
  else
    echo "WEB_SSL_CERT=data/certs/web.crt" >> "${ENV_FILE}"
  fi
  if grep -q '^WEB_SSL_KEY=' "${ENV_FILE}"; then
    sed -i "s|^WEB_SSL_KEY=.*|WEB_SSL_KEY=data/certs/web.key|" "${ENV_FILE}" 2>/dev/null || \
      sed -i '' "s|^WEB_SSL_KEY=.*|WEB_SSL_KEY=data/certs/web.key|" "${ENV_FILE}"
  else
    echo "WEB_SSL_KEY=data/certs/web.key" >> "${ENV_FILE}"
  fi
fi

echo "Created:"
echo "  ${CERT_DIR}/web.crt"
echo "  ${CERT_DIR}/web.key"
echo "  Valid for: ${DAYS} days"
EXPIRY="$(openssl x509 -in "${CERT_DIR}/web.crt" -noout -enddate 2>/dev/null | sed 's/^notAfter=//' || true)"
echo "  Expires: ${EXPIRY:-unknown}"
echo "  SAN: ${SAN}"
echo
echo "Verify: bash scripts/generate_web_cert.sh --check"
echo "Browsers require HTTPS for microphone access from non-localhost IPs."