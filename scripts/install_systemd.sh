#!/usr/bin/env bash
# Install the systemd *user* units with this checkout's real path.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
UNIT_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
ENABLE=0
DISABLE=0
SHOW_STATUS=0
UNITS=(voice-assistant.service voice-assistant-web.service)
WEB_UNIT=voice-assistant-web.service
PID_FILE="$ROOT_DIR/data/voice-assistant.pid"
ENV_FILE="$ROOT_DIR/.env"

usage() {
  echo "Usage: $(basename "$0") [--enable|--disable|--status]"
  echo "  Renders systemd/*.service with ROOT=$ROOT_DIR into $UNIT_DIR"
  echo "  --enable   install, daemon-reload, enable + start $WEB_UNIT (refuses if script server is up)"
  echo "  --disable  disable + stop user units"
  echo "  --status   show systemctl --user status for web + desktop units"
  exit 0
}

for arg in "$@"; do
  case "$arg" in
    --enable) ENABLE=1 ;;
    --disable) DISABLE=1 ;;
    --status) SHOW_STATUS=1 ;;
    -h|--help) usage ;;
    *) echo "Unknown option: $arg" >&2; exit 1 ;;
  esac
done

load_web_port() {
  WEB_PORT="${WEB_PORT:-8765}"
  if [[ -f "$ENV_FILE" ]]; then
    local from_env
    from_env="$(grep -E '^WEB_PORT=' "$ENV_FILE" | tail -1 | sed 's/\r$//' | cut -d= -f2- || true)"
    if [[ -n "$from_env" ]]; then
      WEB_PORT="$from_env"
    fi
  fi
}

port_holders() {
  ss -tlnp 2>/dev/null | grep -E ":${WEB_PORT} " || true
}

script_managed_running() {
  if [[ ! -f "$PID_FILE" ]]; then
    return 1
  fi
  local pid
  pid="$(tr -d '[:space:]' <"$PID_FILE")"
  [[ -n "$pid" ]] || return 1
  kill -0 "$pid" 2>/dev/null || return 1
  tr '\0' ' ' <"/proc/$pid/cmdline" 2>/dev/null | grep -q "bot.py"
}

refuse_if_script_server_on_port() {
  load_web_port
  if script_managed_running; then
    echo "Script-managed Sahir is running (pid $(tr -d '[:space:]' <"$PID_FILE"))." >&2
    echo "Stop it first: bash scripts/voice-assistant.sh stop" >&2
    echo "(or: kill $(tr -d '[:space:]' <"$PID_FILE") if the pid file is yours)" >&2
    exit 1
  fi
  if ss -tln 2>/dev/null | grep -qE ":${WEB_PORT} "; then
    echo "Port ${WEB_PORT} (from .env WEB_PORT) is already in use." >&2
    port_holders >&2
    echo "Stop the listener before enabling systemd $WEB_UNIT." >&2
    exit 1
  fi
}

install_units() {
  mkdir -p "$UNIT_DIR"
  for unit in "${UNITS[@]}"; do
    sed "s#@ROOT@#${ROOT_DIR}#g" "$ROOT_DIR/systemd/$unit" >"$UNIT_DIR/$unit"
    echo "Installed $UNIT_DIR/$unit"
  done
  systemctl --user daemon-reload
  if command -v systemd-analyze >/dev/null 2>&1; then
    systemd-analyze --user verify "${UNITS[@]/#/$UNIT_DIR/}" || true
  fi
}

if [[ "$SHOW_STATUS" == "1" ]]; then
  systemctl --user --no-pager status "${UNITS[@]}" || true
  exit 0
fi

if [[ "$DISABLE" == "1" ]]; then
  install_units
  systemctl --user disable --now "$WEB_UNIT" 2>/dev/null || true
  systemctl --user disable --now voice-assistant.service 2>/dev/null || true
  echo "Disabled user units."
  exit 0
fi

install_units

if [[ "$ENABLE" == "1" ]]; then
  refuse_if_script_server_on_port
  systemctl --user enable --now "$WEB_UNIT"
  systemctl --user --no-pager status "$WEB_UNIT" | head -8
else
  echo "Not enabled. Enable with: bash scripts/install_systemd.sh --enable"
  echo "Or start once: systemctl --user start $WEB_UNIT"
fi