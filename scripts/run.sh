#!/bin/zsh
# 漫画工作台 —— 一键启动（生产模式：FastAPI 同时托管前端构建产物）
# 用法: ./scripts/run.sh [端口]，默认 8770（本机 8000 已被 Novel Studio 占用）
set -e
cd "$(dirname "$0")/.."
PORT="${1:-8770}"
PY=/Users/craxus/.workbuddy/binaries/python/envs/default/bin/python

if [ ! -d frontend/dist ]; then
  echo "[build] 前端构建产物不存在，正在构建…"
  (cd frontend && PATH="/Users/craxus/.workbuddy/binaries/node/versions/22.22.2-3/bin:$PATH" npm run build)
fi

echo "[run] Comic Workbench → http://127.0.0.1:${PORT}"
exec "$PY" -m uvicorn app.main:app --host 127.0.0.1 --port "$PORT" --app-dir backend
