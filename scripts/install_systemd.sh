#!/usr/bin/env bash
# Install the systemd *user* units with this checkout's real path.
# Does not enable anything unless --enable is passed.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
UNIT_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
ENABLE=0
UNITS=(voice-assistant.service voice-assistant-web.service)

for arg in "$@"; do
  case "$arg" in
    --enable) ENABLE=1 ;;
    -h|--help)
      echo "Usage: $(basename "$0") [--enable]"
      echo "  Renders systemd/*.service with ROOT=$ROOT_DIR into $UNIT_DIR"
      echo "  --enable  also enable --now voice-assistant-web.service"
      exit 0
      ;;
    *) echo "Unknown option: $arg" >&2; exit 1 ;;
  esac
done

mkdir -p "$UNIT_DIR"
for unit in "${UNITS[@]}"; do
  sed "s#@ROOT@#${ROOT_DIR}#g" "$ROOT_DIR/systemd/$unit" >"$UNIT_DIR/$unit"
  echo "Installed $UNIT_DIR/$unit"
done

systemctl --user daemon-reload
if command -v systemd-analyze >/dev/null 2>&1; then
  systemd-analyze --user verify "${UNITS[@]/#/$UNIT_DIR/}" || true
fi

if [[ "$ENABLE" == "1" ]]; then
  systemctl --user enable --now voice-assistant-web.service
  systemctl --user --no-pager status voice-assistant-web.service | head -5
else
  echo "Not enabled. Start manually: systemctl --user start voice-assistant-web.service"
fi
