#!/usr/bin/env bash
# 发布到 GitHub
#
# 首次：创建公开仓库并推送
#   ./scripts/publish_github.sh                    # 默认仓库名 comic-workbench，公开
#   ./scripts/publish_github.sh my-comic-repo      # 自定义名字
#   ./scripts/publish_github.sh my-comic-repo private
#
# 之后：已配置 origin 时，直接推送当前分支（相当于 git push）
#
# 前置：gh CLI 已安装且完成认证（gh auth login）
set -euo pipefail
cd "$(dirname "$0")/.."

REPO_NAME="${1:-comic-workbench}"
VISIBILITY="${2:-public}"
DESC="本地优先的漫画生产系统：故事拆解 → 分镜 → 多引擎出图 → 一致性资产 → 中文排版 → 多语言导出"

command -v gh >/dev/null || { echo "[错误] 未安装 gh，先执行：brew install gh" >&2; exit 1; }
gh auth status >/dev/null 2>&1 || { echo "[错误] gh 未认证，先执行：gh auth login" >&2; exit 1; }

if git remote get-url origin >/dev/null 2>&1; then
  BRANCH="$(git rev-parse --abbrev-ref HEAD)"
  echo "==> origin 已存在（$(git remote get-url origin)），推送分支 $BRANCH"
  git push -u origin "$BRANCH"
  echo "==> 完成：$(gh repo view --json url -q .url 2>/dev/null || echo '')"
else
  echo "==> 创建 $VISIBILITY 仓库 $REPO_NAME 并推送"
  gh repo create "$REPO_NAME" --"$VISIBILITY" --source=. --remote=origin --push --description "$DESC"
  echo "==> 完成：$(gh repo view --json url -q .url 2>/dev/null || echo "$REPO_NAME")"
fi
