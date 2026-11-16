#!/usr/bin/env bash
# Start/stop/restart the voice assistant web UI (LAN accessible).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
PID_FILE="$ROOT_DIR/data/voice-assistant.pid"
VENV_PYTHON="$ROOT_DIR/venv/bin/python"
ENV_FILE="$ROOT_DIR/.env"

WEB_HOST="${WEB_HOST:-0.0.0.0}"
WEB_PORT="${WEB_PORT:-8765}"
WEB_SCHEME="http"

load_env() {
  # A WEB_PORT already set in the environment wins over .env
  # (e.g. WEB_PORT=8766 bash scripts/voice-assistant.sh start).
  local preset_port="${WEB_PORT:-}"
  if [[ -f "$ENV_FILE" ]]; then
    # shellcheck disable=SC1090
    set -a
    source <(grep -E '^(WEB_HOST|WEB_PORT|WEB_SSL_CERT|WEB_SSL_KEY)=' "$ENV_FILE" | sed 's/\r$//')
    set +a
  fi
  if [[ -n "$preset_port" ]]; then
    export WEB_PORT="$preset_port"
  fi

  WEB_HOST="${WEB_HOST:-0.0.0.0}"
  WEB_PORT="${WEB_PORT:-8765}"

  if [[ -n "${WEB_SSL_CERT:-}" && -n "${WEB_SSL_KEY:-}" ]]; then
    if [[ -f "$ROOT_DIR/$WEB_SSL_CERT" || -f "$WEB_SSL_CERT" ]] \
      && [[ -f "$ROOT_DIR/$WEB_SSL_KEY" || -f "$WEB_SSL_KEY" ]]; then
      WEB_SCHEME="https"
    fi
  fi
}

is_running() {
  if [[ ! -f "$PID_FILE" ]]; then
    return 1
  fi

  local pid
  pid="$(cat "$PID_FILE")"
  if [[ -z "$pid" ]]; then
    return 1
  fi

  # Guard against PID reuse after a reboot: the process must be bot.py.
  if kill -0 "$pid" 2>/dev/null && tr '\0' ' ' <"/proc/$pid/cmdline" 2>/dev/null | grep -q "bot.py"; then
    return 0
  fi

  rm -f "$PID_FILE"
  return 1
}

pid_of() {
  if is_running; then
    cat "$PID_FILE"
  fi
}

lan_ip() {
  ip -4 route get 1.1.1.1 2>/dev/null | awk '{for (i = 1; i <= NF; i++) if ($i == "src") print $(i + 1); exit}'
}

print_urls() {
  local ip
  ip="$(lan_ip || true)"

  echo "Local:  ${WEB_SCHEME}://127.0.0.1:${WEB_PORT}"
  if [[ -n "$ip" ]]; then
    echo "LAN:    ${WEB_SCHEME}://${ip}:${WEB_PORT}"
  else
    echo "LAN:    ${WEB_SCHEME}://<this-pc-ip>:${WEB_PORT}"
  fi

  if [[ "$WEB_SCHEME" == "https" ]]; then
    echo "Note:   Accept the self-signed certificate on first visit."
  else
    echo "Note:   Microphone from other devices requires HTTPS (see scripts/generate_web_cert.sh)."
  fi

  echo "Logs:   $ROOT_DIR/data/logs/voice-assistant.log"
}

require_setup() {
  if [[ ! -x "$VENV_PYTHON" ]]; then
    echo "Virtualenv missing. Run: bash scripts/install.sh" >&2
    exit 1
  fi

  mkdir -p "$ROOT_DIR/data/logs"
}

wait_for_health() {
  local attempts=40
  local url="${WEB_SCHEME}://127.0.0.1:${WEB_PORT}/api/health"
  local curl_args=(-fsS "$url")

  if [[ "$WEB_SCHEME" == "https" ]]; then
    curl_args=(-k "${curl_args[@]}")
  fi

  for ((i = 1; i <= attempts; i++)); do
    if curl "${curl_args[@]}" >/dev/null 2>&1; then
      return 0
    fi
    sleep 0.25
  done

  echo "Server process started but health check did not pass yet." >&2
  echo "Check logs: $ROOT_DIR/data/logs/voice-assistant.log" >&2
  return 1
}

cmd_start() {
  load_env
  require_setup

  if is_running; then
    echo "Voice assistant already running (pid $(pid_of))."
    print_urls
    exit 0
  fi

  # Always bind on all interfaces so phones/tablets on the LAN can connect.
  export WEB_HOST="0.0.0.0"

  cd "$ROOT_DIR"
  nohup "$VENV_PYTHON" bot.py --web >>"$ROOT_DIR/data/logs/startup.log" 2>&1 &
  echo $! >"$PID_FILE"

  if wait_for_health; then
    echo "Voice assistant started (pid $(pid_of))."
    print_urls
  else
    exit 1
  fi
}

cmd_stop() {
  if ! is_running; then
    echo "Voice assistant is not running."
    rm -f "$PID_FILE"
    exit 0
  fi

  local pid
  pid="$(pid_of)"
  echo "Stopping voice assistant (pid $pid)..."

  kill "$pid" 2>/dev/null || true

  for _ in {1..40}; do
    if ! kill -0 "$pid" 2>/dev/null; then
      rm -f "$PID_FILE"
      echo "Stopped."
      return 0
    fi
    sleep 0.25
  done

  echo "Force stopping pid $pid..."
  kill -9 "$pid" 2>/dev/null || true
  rm -f "$PID_FILE"
  echo "Stopped."
}

cmd_restart() {
  cmd_stop
  sleep 1
  cmd_start
}

cmd_status() {
  load_env

  if is_running; then
    echo "running (pid $(pid_of))"
    print_urls
    exit 0
  fi

  if ss -tln 2>/dev/null | grep -q ":${WEB_PORT} "; then
    echo "not running (pid file missing, but port ${WEB_PORT} is in use)"
    exit 1
  fi

  echo "stopped"
  exit 1
}

cmd_urls() {
  load_env
  print_urls
}

usage() {
  cat <<EOF
Usage: $(basename "$0") {start|stop|restart|status|urls}

  start    Start web UI in background (binds 0.0.0.0 for LAN access)
  stop     Stop background server
  restart  Stop then start
  status   Show running state and URLs
  urls     Print local/LAN URLs only

Environment (.env):
  WEB_PORT, WEB_SSL_CERT, WEB_SSL_KEY

Examples:
  bash scripts/voice-assistant.sh start
  bash scripts/voice-assistant.sh status
EOF
}

main() {
  local command="${1:-}"

  case "$command" in
    start) cmd_start ;;
    stop) cmd_stop ;;
    restart) cmd_restart ;;
    status) cmd_status ;;
    urls) cmd_urls ;;
    -h|--help|help|"") usage ;;
    *)
      echo "Unknown command: $command" >&2
      usage
      exit 1
      ;;
  esac
}

main "$@"
