#!/bin/bash
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

if [ ! -x ".venv/bin/python" ]; then
  echo "找不到可用的 .venv，正在建立..."
  python3 -m venv .venv
  .venv/bin/python -m pip install --upgrade pip
fi

WHEEL_FILE="$(ls -1 "$SCRIPT_DIR"/dist/tasa_orbit_v4-*.whl 2>/dev/null | sort | tail -n 1)"
if [ -n "$WHEEL_FILE" ]; then
  echo "正在確認相依套件..."
  .venv/bin/python -m pip install --upgrade "$WHEEL_FILE"
  echo "正在重建 tasa_v4 套件..."
  .venv/bin/python -m pip install --force-reinstall --no-deps "$WHEEL_FILE"
else
  echo "找不到 dist wheel，改由目前原始碼安裝..."
  .venv/bin/python -m pip install -e ".[dev]"
fi

.venv/bin/python -c "import tasa_v4, casadi, pymoo; print('修復成功：TASA Orbit V4', tasa_v4.__version__); print('套件位置：', tasa_v4.__file__)"
echo
echo "現在可執行："
echo "source .venv/bin/activate"
echo "tasa-v4 optimize configs/current_competition.yaml --output runs/quick --quick --no-refine"
echo
read -r -p "按 Return 關閉此視窗..." _
