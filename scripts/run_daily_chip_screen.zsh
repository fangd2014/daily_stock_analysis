#!/bin/zsh

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname "$0")" && pwd)
source "${SCRIPT_DIR}/load_deepseek_env.zsh"

set -eu

PROJECT_ROOT=$(CDPATH= cd -- "${SCRIPT_DIR}/.." && pwd)
PYTHON_BIN=${1:-${DAILY_CHIP_SCREEN_PYTHON:-$(command -v python3)}}
CONFIG_PATH=${2:-${DAILY_CHIP_SCREEN_CONFIG:-configs/quant/daily_chip_screen.json}}

cd "$PROJECT_ROOT"
exec "$PYTHON_BIN" -m src.quant.daily_chip_screener --config "$CONFIG_PATH" run
