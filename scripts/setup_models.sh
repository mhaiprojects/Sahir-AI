#!/usr/bin/env bash
# Download Piper voice model into models/piper/
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MODEL_DIR="$ROOT/models/piper"
VOICE="${PIPER_VOICE:-en_US-lessac-medium}"
BASE="https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/lessac/medium"

mkdir -p "$MODEL_DIR"

if [[ ! -f "$MODEL_DIR/${VOICE}.onnx" ]]; then
  echo "Downloading Piper voice ${VOICE}..."
  wget -q -O "$MODEL_DIR/${VOICE}.onnx" "$BASE/${VOICE}.onnx"
  wget -q -O "$MODEL_DIR/${VOICE}.onnx.json" "$BASE/${VOICE}.onnx.json"
else
  echo "Piper model already present"
fi

ls -lh "$MODEL_DIR/${VOICE}.onnx"*
