#!/usr/bin/env bash
# Generate a self-signed TLS certificate for LAN microphone access over HTTPS.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CERT_DIR="${ROOT}/data/certs"
mkdir -p "${CERT_DIR}"

HOST="${1:-${WEB_CERT_CN:-voice-assistant.local}}"
DAYS="${2:-${WEB_CERT_DAYS:-825}}"
FORCE="${FORCE:-0}"

if [[ -f "${CERT_DIR}/web.crt" && -f "${CERT_DIR}/web.key" && "${FORCE}" != "1" ]]; then
  echo "Certificates already exist in ${CERT_DIR} (set FORCE=1 to regenerate)."
  exit 0
fi

LAN_IP="$(ip -4 route get 1.1.1.1 2>/dev/null | awk '{for(i=1;i<=NF;i++) if($i=="src") print $(i+1)}' || true)"

if [[ -n "${WEB_CERT_SAN:-}" ]]; then
  SAN="${WEB_CERT_SAN}"
else
  SAN="DNS:${HOST},DNS:localhost,DNS:voice-assistant,IP:127.0.0.1"
  if [[ -n "${LAN_IP}" ]]; then
    SAN="${SAN},IP:${LAN_IP}"
  fi
fi

openssl req -x509 -newkey rsa:4096 \
  -keyout "${CERT_DIR}/web.key" \
  -out "${CERT_DIR}/web.crt" \
  -days "${DAYS}" -nodes \
  -subj "/CN=${HOST}/O=Voice Assistant/C=AU" \
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
echo "  SAN: ${SAN}"
echo
echo "Open on LAN: https://${LAN_IP:-<this-pc-ip>}:8765"
echo "Browsers require HTTPS for microphone access from non-localhost IPs."
