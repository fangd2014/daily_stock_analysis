#!/bin/sh
set -eu

PROJECT_ROOT=$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)
if [ -n "${QUANT_BACKFILL_PYTHON:-}" ]; then
    PYTHON_BIN=$QUANT_BACKFILL_PYTHON
elif command -v python >/dev/null 2>&1; then
    PYTHON_BIN=$(command -v python)
else
    PYTHON_BIN=$(command -v python3)
fi
CONFIG_PATH=${QUANT_BACKFILL_CONFIG:-configs/quant/quant_research_36m.json}
LOG_PATH=${QUANT_BACKFILL_LOG:-logs/quant_backfill/cron.log}
TASK_ID=${QUANT_BACKFILL_TASK_ID:-quant-history-backfill}
START_MARKER="# daily-stock-analysis ${TASK_ID} start"
END_MARKER="# daily-stock-analysis ${TASK_ID} end"
CURRENT_CRON=$(mktemp)
UPDATED_CRON=$(mktemp)

cleanup() {
    rm -f "$CURRENT_CRON" "$UPDATED_CRON"
}
trap cleanup EXIT HUP INT TERM

mkdir -p "$PROJECT_ROOT/logs/quant_backfill"
crontab -l >"$CURRENT_CRON" 2>/dev/null || :
awk -v start="$START_MARKER" -v end="$END_MARKER" '
    $0 == start { skip = 1; next }
    $0 == end { skip = 0; next }
    !skip { print }
' "$CURRENT_CRON" >"$UPDATED_CRON"

{
    printf '%s\n' "$START_MARKER"
    printf '17 0,12 * * * cd "%s" && "%s" -m src.quant.cli --config "%s" fetch >> "%s" 2>&1\n' \
        "$PROJECT_ROOT" "$PYTHON_BIN" "$CONFIG_PATH" "$LOG_PATH"
    printf '%s\n' "$END_MARKER"
} >>"$UPDATED_CRON"

crontab "$UPDATED_CRON"
printf 'Installed scheduled quant history backfill with Python: %s\n' "$PYTHON_BIN"
