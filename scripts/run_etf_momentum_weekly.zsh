#!/bin/zsh

set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname "$0")" && pwd)
PROJECT_ROOT=$(CDPATH= cd -- "${SCRIPT_DIR}/.." && pwd)
PYTHON_BIN=${1:-${ETF_MOMENTUM_PYTHON:-$(command -v python3)}}
CONFIG_PATH=${2:-${ETF_MOMENTUM_CONFIG:-configs/quant/etf_momentum_rotation.json}}

unset HTTP_PROXY HTTPS_PROXY ALL_PROXY http_proxy https_proxy all_proxy || true
cd "$PROJECT_ROOT"
exec "$PYTHON_BIN" -m src.quant.etf_momentum --config "$CONFIG_PATH" weekly
