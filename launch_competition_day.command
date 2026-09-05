#!/bin/bash
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"
EXPECTED_VERSION="4.2.2"

if [ ! -x ".venv/bin/python" ]; then
  python3 -m venv .venv
  .venv/bin/python -m pip install --upgrade pip
fi

if ! .venv/bin/python -c "import tasa_v4, casadi, pymoo; assert tasa_v4.__version__ == '$EXPECTED_VERSION'" >/dev/null 2>&1; then
  WHEEL_FILE="$(ls -1 "$SCRIPT_DIR"/dist/tasa_orbit_v4-*.whl 2>/dev/null | sort | tail -n 1)"
  if [ -n "$WHEEL_FILE" ]; then
    echo "正在安裝或修復 TASA Orbit V4..."
    .venv/bin/python -m pip install --upgrade "$WHEEL_FILE"
    .venv/bin/python -m pip install --force-reinstall --no-deps "$WHEEL_FILE"
  else
    echo "正在從原始碼安裝或修復 TASA Orbit V4..."
    .venv/bin/python -m pip install -e ".[dev]"
  fi
fi

exec .venv/bin/tasa-v4 gui configs/current_competition.yaml
