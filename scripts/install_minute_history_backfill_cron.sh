#!/bin/sh
set -eu

PROJECT_ROOT=$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)
PYTHON_BIN=${MINUTE_HISTORY_PYTHON:-$(command -v python3)}
CONFIG_PATH=${MINUTE_HISTORY_CONFIG:-configs/quant/minute_history_3y.json}
LOG_PATH=${MINUTE_HISTORY_LOG:-logs/quant_backfill/minute_history_3y.log}
RUNNER_PATH=$PROJECT_ROOT/scripts/run_minute_history_backfill.zsh
TASK_ID=${MINUTE_HISTORY_TASK_ID:-minute-history-3y}
START_MARKER="# daily-stock-analysis ${TASK_ID} start"
END_MARKER="# daily-stock-analysis ${TASK_ID} end"
CURRENT_CRON=$(mktemp)
UPDATED_CRON=$(mktemp)

cleanup() {
    rm -f "$CURRENT_CRON" "$UPDATED_CRON"
}
trap cleanup EXIT HUP INT TERM

mkdir -p "$PROJECT_ROOT/logs/quant_backfill" "$PROJECT_ROOT/reports/quant/minute_history_3y"
crontab -l >"$CURRENT_CRON" 2>/dev/null || :
awk -v start="$START_MARKER" -v end="$END_MARKER" '
    $0 == start { skip = 1; next }
    $0 == end { skip = 0; next }
    !skip { print }
' "$CURRENT_CRON" >"$UPDATED_CRON"

{
    printf '%s\n' "$START_MARKER"
    printf '11 * * * * /bin/zsh "%s" "%s" "%s" >> "%s" 2>&1\n' \
        "$RUNNER_PATH" "$PYTHON_BIN" "$CONFIG_PATH" "$LOG_PATH"
    printf '%s\n' "$END_MARKER"
} >>"$UPDATED_CRON"

crontab "$UPDATED_CRON"
printf 'Installed hourly minute-history backfill with Python: %s\n' "$PYTHON_BIN"
