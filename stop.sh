#!/usr/bin/env bash
# 漫画工作台 Comic Workbench — 停止服务（macOS / Linux）
#
# 用法：
#   ./stop.sh          # 停默认端口 8770
#   ./stop.sh 9000     # 停指定端口
#
# 实现说明：只用 `lsof -sTCP:LISTEN` 匹配**监听方**。
# 若图省事写成 `lsof -ti tcp:PORT`，会把 IDE / 浏览器等**客户端连接**也算进来，
# kill 时可能误杀你自己的 IDE（实测踩过：8770 上同时匹配到 WorkBuddy 与 uvicorn）。
set -uo pipefail
cd "$(dirname "$0")"

PORT="${1:-8770}"
listeners() { lsof -ti tcp:"$PORT" -sTCP:LISTEN 2>/dev/null || true; }

PIDS="$(listeners)"
if [ -z "$PIDS" ]; then
  echo "端口 ${PORT} 上没有运行中的服务"
  exit 0
fi

echo "正在停止端口 ${PORT} 的服务（PID: $(echo "$PIDS" | tr '\n' ' '))"
kill $PIDS 2>/dev/null || true

for _ in $(seq 1 10); do
  sleep 1
  if [ -z "$(listeners)" ]; then
    echo "✔ 已停止"
    exit 0
  fi
done

echo "优雅退出超时，强制结束…"
kill -9 $(listeners) 2>/dev/null || true
sleep 1
if [ -z "$(listeners)" ]; then
  echo "✔ 已强制停止"
else
  echo "✘ 端口 ${PORT} 仍被占用，请手动检查：lsof -i tcp:${PORT}"
  exit 1
fi
