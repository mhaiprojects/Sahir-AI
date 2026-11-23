#!/usr/bin/env bash
# Download Moonshine tiny int8 for live captions (sherpa-onnx).
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
exec "$ROOT/venv/bin/python" "$ROOT/scripts/fetch_moonshine.py" "$@"