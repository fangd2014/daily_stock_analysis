#!/bin/zsh

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname "$0")" && pwd)

set -eu

PROJECT_ROOT=$(CDPATH= cd -- "${SCRIPT_DIR}/.." && pwd)
PYTHON_BIN=${1:-${DEEPSEEK_REVIEW_PYTHON:-$(command -v python3)}}
CONFIG_PATH=${2:-${DEEPSEEK_REVIEW_CONFIG:-configs/quant/tech_chip_portfolio_paper.json}}
REVIEW_TYPE=${3:-daily}

if [[ "${REVIEW_TYPE}" != "daily" && "${REVIEW_TYPE}" != "weekly" ]]; then
    print -u2 "Review type must be daily or weekly"
    exit 2
fi

cd "$PROJECT_ROOT"
exec "$PYTHON_BIN" -m src.quant.deepseek_review --config "$CONFIG_PATH" "$REVIEW_TYPE"
