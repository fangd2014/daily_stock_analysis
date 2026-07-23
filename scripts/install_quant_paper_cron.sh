#!/bin/sh
set -eu

PROJECT_ROOT=$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)
PYTHON_BIN=${QUANT_PAPER_PYTHON:-$(command -v python3)}
CONFIG_PATH=${QUANT_PAPER_CONFIG:-configs/quant/688008_paper.json}
LOG_PATH=${QUANT_PAPER_LOG:-logs/quant_paper/cron.log}
TASK_ID=${QUANT_PAPER_TASK_ID:-quant-paper}
RUN_MODULE=${QUANT_PAPER_MODULE:-src.quant.paper}
DAILY_REVIEW_MODULE=${QUANT_DAILY_REVIEW_MODULE:-}
START_MARKER="# daily-stock-analysis ${TASK_ID} start"
END_MARKER="# daily-stock-analysis ${TASK_ID} end"
CURRENT_CRON=$(mktemp)
UPDATED_CRON=$(mktemp)

cleanup() {
    rm -f "$CURRENT_CRON" "$UPDATED_CRON"
}
trap cleanup EXIT HUP INT TERM

mkdir -p "$PROJECT_ROOT/logs/quant_paper" "$PROJECT_ROOT/data/quant_paper/688008" \
    "$PROJECT_ROOT/reports/quant_paper/688008"
crontab -l >"$CURRENT_CRON" 2>/dev/null || :
awk -v start="$START_MARKER" -v end="$END_MARKER" '
    $0 == start { skip = 1; next }
    $0 == end { skip = 0; next }
    !skip { print }
' "$CURRENT_CRON" >"$UPDATED_CRON"

{
    printf '%s\n' "$START_MARKER"
    printf '*/5 9-15 * * 1-5 cd "%s" && "%s" -m "%s" --config "%s" tick >> "%s" 2>&1\n' \
        "$PROJECT_ROOT" "$PYTHON_BIN" "$RUN_MODULE" "$CONFIG_PATH" "$LOG_PATH"
    printf '20 15 * * 5 cd "%s" && "%s" -m "%s" --config "%s" report >> "%s" 2>&1\n' \
        "$PROJECT_ROOT" "$PYTHON_BIN" "$RUN_MODULE" "$CONFIG_PATH" "$LOG_PATH"
    if [ -n "$DAILY_REVIEW_MODULE" ]; then
        printf '25 15 * * 1-5 cd "%s" && "%s" -m "%s" --config "%s" >> "%s" 2>&1\n' \
            "$PROJECT_ROOT" "$PYTHON_BIN" "$DAILY_REVIEW_MODULE" "$CONFIG_PATH" "$LOG_PATH"
    fi
    printf '%s\n' "$END_MARKER"
} >>"$UPDATED_CRON"

crontab "$UPDATED_CRON"
printf 'Installed quant paper cron with Python: %s\n' "$PYTHON_BIN"
