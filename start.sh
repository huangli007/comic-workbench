#!/usr/bin/env bash
# 漫画工作台 Comic Workbench — 一键启动（macOS / Linux）
#
# 用法：
#   ./start.sh            # 默认端口 8770
#   ./start.sh 9000       # 指定端口
#   PYTHON=python3.12 ./start.sh
#
# 首次运行会自动创建 .venv 并安装依赖（需要能访问 pypi 或已配好镜像）。
set -euo pipefail
cd "$(dirname "$0")"

PORT="${1:-8770}"
PY="${PYTHON:-python3}"

if [ ! -d .venv ]; then
  echo "[setup] 创建虚拟环境 .venv …"
  "$PY" -m venv .venv
  ./.venv/bin/python -m pip install -q --upgrade pip
  echo "[setup] 安装依赖（首次约 1-2 分钟）…"
  ./.venv/bin/pip install -q -r backend/requirements.txt
fi

if [ ! -d frontend/dist ]; then
  echo "[error] 缺少前端构建产物 frontend/dist —— 分发包应自带，请确认解包完整" >&2
  exit 1
fi

echo ""
echo "  漫画工作台 → http://127.0.0.1:${PORT}"
echo "  （Ctrl+C 停止）"
echo ""
exec ./.venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port "$PORT" --app-dir backend
