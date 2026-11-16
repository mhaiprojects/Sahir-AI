#!/usr/bin/env bash
# Check logs since last checkpoint; print errors and optionally notify.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
LOG_FILE="$ROOT_DIR/data/logs/voice-assistant.log"
STARTUP_LOG="$ROOT_DIR/data/logs/startup.log"
CHECKPOINT="$ROOT_DIR/data/logs/loop-checkpoint.line"
NOTIFY="${1:-}"

mkdir -p "$ROOT_DIR/data/logs"

notify_user() {
  local title="$1"
  local body="$2"
  echo "NOTIFY|$title|$body"
  if command -v notify-send >/dev/null 2>&1; then
    notify-send "$title" "$body" 2>/dev/null || true
  fi
}

if [[ ! -f "$LOG_FILE" ]]; then
  echo "No log file yet."
  exit 0
fi

last_line=0
if [[ -f "$CHECKPOINT" ]]; then
  last_line="$(cat "$CHECKPOINT")"
fi

total_lines="$(wc -l < "$LOG_FILE")"
new_section=""
if (( total_lines > last_line )); then
  new_section="$(tail -n +"$((last_line + 1))" "$LOG_FILE")"
fi

startup_tail=""
errors="$(printf '%s\n' "$new_section" | grep -E '\[ERROR\]|Traceback \(most recent call last\)' || true)"

if (( total_lines > last_line )); then
  echo "$new_section"
  echo "$total_lines" > "$CHECKPOINT"
fi

if [[ -n "$errors" ]]; then
  echo "=== ERRORS FOUND ==="
  echo "$errors"
  if [[ "$NOTIFY" == "--notify" ]]; then
    notify_user "Voice Assistant Error" "$(echo "$errors" | head -3 | tr '\n' ' ' | cut -c1-240)"
  fi
  exit 2
fi

if (( total_lines <= last_line )); then
  echo "No new log lines (total=$total_lines)."
else
  echo "New activity, no errors (checkpoint=$total_lines)."
fi
exit 0
