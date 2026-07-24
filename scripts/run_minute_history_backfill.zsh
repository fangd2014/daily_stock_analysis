#!/bin/zsh
set -eu

PROJECT_ROOT=$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)
PYTHON_BIN=${1:-${MINUTE_HISTORY_PYTHON:-$(command -v python3)}}
CONFIG_PATH=${2:-${MINUTE_HISTORY_CONFIG:-configs/quant/minute_history_3y.json}}

cd "$PROJECT_ROOT"
exec "$PYTHON_BIN" -m src.quant.minute_history_backfill \
    --config "$CONFIG_PATH" \
    --source tushare
