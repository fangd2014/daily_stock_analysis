#!/bin/sh
set -eu

PROJECT_ROOT=$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)
PYTHON_BIN=${HOT_SECTOR_SCREEN_PYTHON:-$(command -v python3)}
CONFIG_PATH=${HOT_SECTOR_SCREEN_CONFIG:-configs/quant/hot_sector_screen.json}
LOG_PATH=${HOT_SECTOR_SCREEN_LOG:-logs/quant_screen/hot_sector_screen.log}
RUNNER_PATH=$PROJECT_ROOT/scripts/run_hot_sector_screen.zsh
TASK_ID=${HOT_SECTOR_SCREEN_TASK_ID:-hot-sector-screen}
START_MARKER="# daily-stock-analysis ${TASK_ID} start"
END_MARKER="# daily-stock-analysis ${TASK_ID} end"
CURRENT_CRON=$(mktemp)
UPDATED_CRON=$(mktemp)

cleanup() {
    rm -f "$CURRENT_CRON" "$UPDATED_CRON"
}
trap cleanup EXIT HUP INT TERM

mkdir -p "$PROJECT_ROOT/logs/quant_screen" "$PROJECT_ROOT/reports/quant/hot_sector_screen"
crontab -l >"$CURRENT_CRON" 2>/dev/null || :
awk -v start="$START_MARKER" -v end="$END_MARKER" '
    $0 == start { skip = 1; next }
    $0 == end { skip = 0; next }
    !skip { print }
' "$CURRENT_CRON" >"$UPDATED_CRON"

{
    printf '%s\n' "$START_MARKER"
    printf '5 20 * * 1-5 /bin/zsh "%s" "%s" "%s" >> "%s" 2>&1\n' \
        "$RUNNER_PATH" "$PYTHON_BIN" "$CONFIG_PATH" "$LOG_PATH"
    printf '%s\n' "$END_MARKER"
} >>"$UPDATED_CRON"

crontab "$UPDATED_CRON"
printf 'Installed hot-sector screen cron with Python: %s\n' "$PYTHON_BIN"
