#!/usr/bin/env bash
# First-time Docker setup and start.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

bash setup.sh docker
