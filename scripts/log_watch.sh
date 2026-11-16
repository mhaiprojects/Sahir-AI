#!/usr/bin/env bash
# Print new log lines since the last checkpoint (errors/warnings focus).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
LOG_FILE="$ROOT_DIR/data/logs/voice-assistant.log"
STARTUP_LOG="$ROOT_DIR/data/logs/startup.log"
CHECKPOINT="$ROOT_DIR/data/logs/loop-checkpoint.line"

mkdir -p "$ROOT_DIR/data/logs"

if [[ ! -f "$LOG_FILE" ]]; then
  echo "No log file yet: $LOG_FILE"
  exit 0
fi

last_line=0
if [[ -f "$CHECKPOINT" ]]; then
  last_line="$(cat "$CHECKPOINT")"
fi

total_lines="$(wc -l < "$LOG_FILE")"
if (( total_lines <= last_line )); then
  echo "No new log lines since last check (total=$total_lines)."
  exit 0
fi

echo "=== New lines since checkpoint ($last_line -> $total_lines) ==="
tail -n +"$((last_line + 1))" "$LOG_FILE"

echo
echo "=== Errors / warnings in new activity ==="
tail -n +"$((last_line + 1))" "$LOG_FILE" | grep -Ei 'ERROR|WARNING|Exception|Traceback|failed|Could not' || echo "(none)"

if [[ -f "$STARTUP_LOG" ]]; then
  echo
  echo "=== Recent startup.log (last 15 lines) ==="
  tail -n 15 "$STARTUP_LOG"
fi

echo "$total_lines" > "$CHECKPOINT"
echo
echo "Checkpoint updated to line $total_lines"
