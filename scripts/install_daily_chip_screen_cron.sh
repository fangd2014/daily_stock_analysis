#!/bin/sh
set -eu

PROJECT_ROOT=$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)
PYTHON_BIN=${DAILY_CHIP_SCREEN_PYTHON:-$(command -v python3)}
CONFIG_PATH=${DAILY_CHIP_SCREEN_CONFIG:-configs/quant/daily_chip_screen.json}
LOG_PATH=${DAILY_CHIP_SCREEN_LOG:-logs/quant_screen/daily_chip_screen.log}
TASK_ID=${DAILY_CHIP_SCREEN_TASK_ID:-daily-chip-screen}
START_MARKER="# daily-stock-analysis ${TASK_ID} start"
END_MARKER="# daily-stock-analysis ${TASK_ID} end"
CURRENT_CRON=$(mktemp)
UPDATED_CRON=$(mktemp)

cleanup() {
    rm -f "$CURRENT_CRON" "$UPDATED_CRON"
}
trap cleanup EXIT HUP INT TERM

mkdir -p "$PROJECT_ROOT/logs/quant_screen" "$PROJECT_ROOT/reports/quant/daily_chip_screen"
crontab -l >"$CURRENT_CRON" 2>/dev/null || :
awk -v start="$START_MARKER" -v end="$END_MARKER" '
    $0 == start { skip = 1; next }
    $0 == end { skip = 0; next }
    !skip { print }
' "$CURRENT_CRON" >"$UPDATED_CRON"

{
    printf '%s\n' "$START_MARKER"
    printf '0 20 * * 1-5 cd "%s" && "%s" -m src.quant.daily_chip_screener --config "%s" run >> "%s" 2>&1\n' \
        "$PROJECT_ROOT" "$PYTHON_BIN" "$CONFIG_PATH" "$LOG_PATH"
    printf '%s\n' "$END_MARKER"
} >>"$UPDATED_CRON"

crontab "$UPDATED_CRON"
printf 'Installed daily chip screen cron with Python: %s\n' "$PYTHON_BIN"
