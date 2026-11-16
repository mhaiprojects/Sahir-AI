#!/usr/bin/env bash
# Download the Kokoro-82M (Apache-2.0) sherpa-onnx model for TTS_BACKEND=kokoro.
# Default: int8 multi-lang v1.1 (~140 MB, includes the English lexicon sherpa-onnx 2.x needs). Override with KOKORO_PACKAGE=kokoro-int8-multi-lang-v1_1 etc.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PKG="${KOKORO_PACKAGE:-kokoro-int8-multi-lang-v1_1}"
DEST="$ROOT/models/kokoro"
URL="https://github.com/k2-fsa/sherpa-onnx/releases/download/tts-models/${PKG}.tar.bz2"
mkdir -p "$DEST"
if [[ -f "$DEST/$PKG/model.int8.onnx" || -f "$DEST/$PKG/model.onnx" ]]; then
  echo "Kokoro model already present: $DEST/$PKG"; exit 0
fi
echo "Downloading $URL"
curl -fL --retry 3 -o "$DEST/$PKG.tar.bz2" "$URL"
tar -xjf "$DEST/$PKG.tar.bz2" -C "$DEST"
rm -f "$DEST/$PKG.tar.bz2"
echo "Installed: $DEST/$PKG  (set TTS_BACKEND=kokoro and KOKORO_MODEL_DIR=$DEST/$PKG)"
