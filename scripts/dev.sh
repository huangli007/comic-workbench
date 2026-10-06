#!/bin/zsh
# 开发模式：后端 8000 + 前端 Vite 5173（热更新）
set -e
cd "$(dirname "$0")/.."
PY=/Users/craxus/.workbuddy/binaries/python/envs/default/bin/python
NODE_BIN=/Users/craxus/.workbuddy/binaries/node/versions/22.22.2-3/bin

trap 'kill 0' EXIT INT TERM
"$PY" -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload --app-dir backend &
(cd frontend && PATH="$NODE_BIN:$PATH" npm run dev) &
wait
