#!/bin/zsh

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname "$0")" && pwd)
source "${SCRIPT_DIR}/load_deepseek_env.zsh"

set -eu

PROJECT_ROOT=$(CDPATH= cd -- "${SCRIPT_DIR}/.." && pwd)
PYTHON_BIN=${1:-${HOT_SECTOR_SCREEN_PYTHON:-$(command -v python3)}}
CONFIG_PATH=${2:-${HOT_SECTOR_SCREEN_CONFIG:-configs/quant/hot_sector_screen.json}}

cd "$PROJECT_ROOT"
exec "$PYTHON_BIN" -m src.quant.hot_sector_screener --config "$CONFIG_PATH" run
