#!/usr/bin/env bash
# Container startup: TLS certs, wait for Ollama, optional model pull, Piper voice.
set -euo pipefail

cd /app

if [[ "$(id -u)" -eq 0 ]]; then
  mkdir -p data/logs data/certs models/piper
  chown -R sahir:sahir data models/piper 2>/dev/null || chown -R 10001:10001 data models/piper
else
  mkdir -p data/logs data/certs models/piper
fi

# HuggingFace / faster-whisper cache (persisted volume)
export HF_HOME="${HF_HOME:-/app/data/huggingface}"
mkdir -p "${HF_HOME}"

CERT_DIR="/app/data/certs"
if [[ ! -f "${CERT_DIR}/web.crt" || ! -f "${CERT_DIR}/web.key" ]]; then
  echo "Generating self-signed TLS certificate in ${CERT_DIR}..."
  SAN="DNS:localhost,DNS:voice-assistant,IP:127.0.0.1"
  if [[ -n "${WEB_CERT_SAN:-}" ]]; then
    SAN="${WEB_CERT_SAN}"
  fi
  openssl req -x509 -newkey rsa:4096 \
    -keyout "${CERT_DIR}/web.key" \
    -out "${CERT_DIR}/web.crt" \
    -days "${WEB_CERT_DAYS:-825}" -nodes \
    -subj "/CN=${WEB_CERT_CN:-voice-assistant}/O=Sahir/C=AU" \
    -addext "subjectAltName=${SAN}"
fi

OLLAMA_HOST="${OLLAMA_HOST:-http://ollama:11434}"
echo "Waiting for Ollama at ${OLLAMA_HOST}..."
ready=0
for _ in $(seq 1 90); do
  if curl -sf "${OLLAMA_HOST}/api/tags" >/dev/null 2>&1; then
    ready=1
    break
  fi
  sleep 2
done
if [[ "${ready}" -ne 1 ]]; then
  echo "WARNING: Ollama not reachable at ${OLLAMA_HOST}; assistant may fail until it is up."
else
  echo "Ollama is ready."
fi

if [[ -n "${OLLAMA_PULL_MODEL:-}" && "${ready}" -eq 1 ]]; then
  echo "Pulling Ollama model: ${OLLAMA_PULL_MODEL} (first run may take several minutes)..."
  curl -sf -X POST "${OLLAMA_HOST}/api/pull" \
    -H "Content-Type: application/json" \
    -d "{\"name\":\"${OLLAMA_PULL_MODEL}\"}" || echo "WARNING: model pull failed or still in progress"
fi

if [[ -n "${OLLAMA_PULL_MODELS:-}" && "${ready}" -eq 1 ]]; then
  IFS=',' read -ra _models <<< "${OLLAMA_PULL_MODELS}"
  for _m in "${_models[@]}"; do
    _m="${_m#"${_m%%[![:space:]]*}"}"
    _m="${_m%"${_m##*[![:space:]]}"}"
    [[ -z "${_m}" ]] && continue
    echo "Pulling Ollama model: ${_m}..."
    curl -sf -X POST "${OLLAMA_HOST}/api/pull" \
      -H "Content-Type: application/json" \
      -d "{\"name\":\"${_m}\"}" || echo "WARNING: pull failed for ${_m}"
  done
fi

_voice_on="${WEB_VOICE_ENABLED:-true}"
if [[ "${_voice_on}" == "1" || "${_voice_on}" == "true" || "${_voice_on}" == "yes" ]]; then
  VOICE="${PIPER_VOICE:-en_US-lessac-medium}"
  if [[ ! -f "models/piper/${VOICE}.onnx" ]]; then
    echo "Downloading Piper voice ${VOICE}..."
    bash scripts/setup_models.sh
  fi
fi

if [[ "${PRELOAD_WHISPER:-0}" == "1" ]]; then
  echo "Preloading Whisper models (live=${WHISPER_LIVE_MODEL:-tiny}, final=${WHISPER_FINAL_MODEL:-base})..."
  python scripts/docker_preload_whisper.py || echo "WARNING: Whisper preload failed; models download on first use."
fi

if [[ "$(id -u)" -eq 0 ]]; then
  exec gosu sahir "$@"
fi
exec "$@"
