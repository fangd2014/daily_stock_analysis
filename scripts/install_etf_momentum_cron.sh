#!/bin/sh
set -eu

PROJECT_ROOT=$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)
PYTHON_BIN=${ETF_MOMENTUM_PYTHON:-$(command -v python3)}
CONFIG_PATH=${ETF_MOMENTUM_CONFIG:-configs/quant/etf_momentum_rotation.json}
LOG_PATH=${ETF_MOMENTUM_LOG:-logs/quant_etf/weekly.log}
RUNNER_PATH=$PROJECT_ROOT/scripts/run_etf_momentum_weekly.zsh
TASK_ID=${ETF_MOMENTUM_TASK_ID:-etf-momentum-weekly}
START_MARKER="# daily-stock-analysis ${TASK_ID} start"
END_MARKER="# daily-stock-analysis ${TASK_ID} end"
LEGACY_START_MARKER="# daily-stock-analysis etf-momentum-daily start"
LEGACY_END_MARKER="# daily-stock-analysis etf-momentum-daily end"
CURRENT_CRON=$(mktemp)
UPDATED_CRON=$(mktemp)

cleanup() {
    rm -f "$CURRENT_CRON" "$UPDATED_CRON"
}
trap cleanup EXIT HUP INT TERM

mkdir -p "$PROJECT_ROOT/logs/quant_etf" "$PROJECT_ROOT/reports/quant/etf_momentum"
crontab -l >"$CURRENT_CRON" 2>/dev/null || :
awk -v start="$START_MARKER" -v end="$END_MARKER" \
    -v legacy_start="$LEGACY_START_MARKER" -v legacy_end="$LEGACY_END_MARKER" '
    $0 == start || $0 == legacy_start { skip = 1; next }
    $0 == end || $0 == legacy_end { skip = 0; next }
    !skip { print }
' "$CURRENT_CRON" >"$UPDATED_CRON"

{
    printf '%s\n' "$START_MARKER"
    printf '0 20 * * 0 /bin/zsh "%s" "%s" "%s" >> "%s" 2>&1\n' \
        "$RUNNER_PATH" "$PYTHON_BIN" "$CONFIG_PATH" "$LOG_PATH"
    printf '%s\n' "$END_MARKER"
} >>"$UPDATED_CRON"

crontab "$UPDATED_CRON"
printf 'Installed Sunday 20:00 ETF weekly review cron with Python: %s\n' "$PYTHON_BIN"
