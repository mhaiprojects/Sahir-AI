#!/usr/bin/env bash
# Initial setup on a new machine (native install or Docker).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

usage() {
  cat <<'EOF'
Usage: bash setup.sh [native|docker|help]

  native  — Python venv, system deps (Arch/Manjaro), Ollama, models, certs (default)
  docker  — .env for compose, TLS certs, docker compose up --build
  help    — this message

Environment:
  FORCE=1           Overwrite existing .env and regenerate TLS certs
  WEB_CERT_SAN=...  Extra Subject Alt Names (e.g. IP:192.0.2.10)
  SKIP_OLLAMA=1     Skip Ollama install/pull (native mode)
  SKIP_DOCKER_UP=1  Only prepare .env/certs (docker mode)

Examples:
  bash setup.sh native
  bash setup.sh docker
  WEB_CERT_SAN='DNS:localhost,IP:127.0.0.1,IP:192.0.2.10' bash setup.sh docker
EOF
}

MODE="${1:-native}"

case "$MODE" in
  help|-h|--help)
    usage
    exit 0
    ;;
  native|docker)
    ;;
  *)
    echo "Unknown mode: $MODE" >&2
    usage
    exit 1
    ;;
esac

echo "==> Sahir setup (${MODE})"
mkdir -p data/logs data/certs models/piper

echo "==> Generating secrets (.env)"
bash scripts/generate_secrets.sh "$MODE"

echo "==> Generating TLS certificate (LAN microphone access)"
bash scripts/generate_web_cert.sh

if [[ "$MODE" == "docker" ]]; then
  if ! command -v docker >/dev/null 2>&1; then
    echo "ERROR: Docker not found. Install Docker Engine + Compose plugin first." >&2
    exit 1
  fi
  if [[ "${SKIP_DOCKER_UP:-0}" == "1" ]]; then
    echo "==> Skipping docker compose (SKIP_DOCKER_UP=1)"
    echo "Run: docker compose up -d --build"
    exit 0
  fi
  echo "==> Building and starting Docker stack..."
  docker compose up -d --build
  echo
  echo "Done. Logs: docker compose logs -f voice-assistant"
  echo "Web UI:  https://$(hostname -I 2>/dev/null | awk '{print $1}' || echo '<host-ip>'):8765"
  echo "See DOCKER.md for GPU, Cursor CLI mounts, and troubleshooting."
  exit 0
fi

# --- Native mode ---
if [[ "$(uname -s)" == "Linux" ]] && command -v pacman >/dev/null 2>&1; then
  if [[ "${SKIP_INSTALL:-0}" != "1" ]]; then
    echo "==> Running native install (Arch/Manjaro packages + venv)"
    bash scripts/install.sh
  fi
else
  echo "==> Non-Arch Linux or macOS: install ffmpeg, portaudio, Python 3.12+ manually."
  echo "    Then: python3 -m venv venv && source venv/bin/activate && pip install -r requirements.txt"
  if [[ ! -d venv ]]; then
    python3 -m venv venv
  fi
  # shellcheck disable=SC1091
  source venv/bin/activate
  pip install -U pip wheel
  pip install -r requirements.txt
  bash scripts/setup_models.sh
fi

if [[ "${SKIP_OLLAMA:-0}" != "1" ]] && command -v ollama >/dev/null 2>&1; then
  echo "==> Pulling Ollama model (from .env OLLAMA_MODEL)"
  # shellcheck disable=SC1091
  set -a && source .env 2>/dev/null || true && set +a
  ollama pull "${OLLAMA_MODEL:-llama3.1:8b}" || echo "WARNING: ollama pull failed — start Ollama and pull manually."
fi

echo
echo "Setup complete (native)."
echo "  source venv/bin/activate"
echo "  python bot.py --web"
echo "  or: bash scripts/voice-assistant.sh start"
echo "Web UI: https://$(hostname -I 2>/dev/null | awk '{print $1}' || echo '127.0.0.1'):8765"
