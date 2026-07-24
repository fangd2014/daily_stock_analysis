#!/bin/sh
set -eu

PROJECT_ROOT=$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)
PYTHON_BIN=${DEEPSEEK_REVIEW_PYTHON:-$(command -v python3)}
CONFIG_PATH=${DEEPSEEK_REVIEW_CONFIG:-configs/quant/tech_chip_portfolio_paper.json}
LOG_PATH=${DEEPSEEK_REVIEW_LOG:-logs/quant_paper/deepseek_review.log}
TASK_ID=${DEEPSEEK_REVIEW_TASK_ID:-deepseek-paper-review}
RUNNER_PATH=$PROJECT_ROOT/scripts/run_deepseek_review.zsh
START_MARKER="# daily-stock-analysis ${TASK_ID} start"
END_MARKER="# daily-stock-analysis ${TASK_ID} end"
CURRENT_CRON=$(mktemp)
UPDATED_CRON=$(mktemp)

cleanup() {
    rm -f "$CURRENT_CRON" "$UPDATED_CRON"
}
trap cleanup EXIT HUP INT TERM

mkdir -p "$PROJECT_ROOT/logs/quant_paper" "$PROJECT_ROOT/reports/quant_paper/all_a_chip_portfolio/deepseek_reviews"
crontab -l >"$CURRENT_CRON" 2>/dev/null || :
awk -v start="$START_MARKER" -v end="$END_MARKER" '
    $0 == start { skip = 1; next }
    $0 == end { skip = 0; next }
    !skip { print }
' "$CURRENT_CRON" >"$UPDATED_CRON"

{
    printf '%s\n' "$START_MARKER"
    printf '0 20 * * 1-5 /bin/zsh "%s" "%s" "%s" daily >> "%s" 2>&1\n' \
        "$RUNNER_PATH" "$PYTHON_BIN" "$CONFIG_PATH" "$LOG_PATH"
    printf '0 21 * * 5 /bin/zsh "%s" "%s" "%s" weekly >> "%s" 2>&1\n' \
        "$RUNNER_PATH" "$PYTHON_BIN" "$CONFIG_PATH" "$LOG_PATH"
    printf '%s\n' "$END_MARKER"
} >>"$UPDATED_CRON"

crontab "$UPDATED_CRON"
printf 'Installed DeepSeek paper review cron with Python: %s\n' "$PYTHON_BIN"
