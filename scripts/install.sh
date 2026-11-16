#!/usr/bin/env bash
# Install system dependencies and Ollama on Manjaro/Arch.
set -euo pipefail

echo "==> Installing audio/system packages (requires sudo)"
sudo pacman -S --needed --noconfirm \
  portaudio ffmpeg pipewire-pulse alsa-utils git python-pip base-devel

if ! command -v ollama >/dev/null 2>&1; then
  echo "==> Installing Ollama"
  curl -fsSL https://ollama.com/install.sh | sh
  sudo systemctl enable --now ollama || true
else
  echo "==> Ollama already installed"
fi

echo "==> Pulling Ollama model"
ollama pull llama3.1:8b

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR/.."

if [[ ! -d venv ]]; then
  python3 -m venv venv
fi
source venv/bin/activate
pip install -U pip wheel
pip install -r requirements.txt

bash "$SCRIPT_DIR/setup_models.sh"

if [[ ! -f .env ]]; then
  cp .env.example .env
  echo "Created .env from .env.example"
fi

echo "Done. Activate with: source venv/bin/activate && python bot.py"
