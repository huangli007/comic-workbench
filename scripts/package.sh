#!/usr/bin/env bash
# 漫画工作台 —— 打包分发
#
# 用法：
#   ./scripts/package.sh                  # 纯代码包（含前端构建产物，可直接跑）
#   ./scripts/package.sh --with-docs      # 附带 docs/ 样张与截图
#   ./scripts/package.sh --with-data      # 附带作品数据（projects/ + 数据库）—— 用于备份/迁移
#   ./scripts/package.sh --all            # 代码 + 文档 + 数据（完整备份）
#
# 产物：dist/comic-workbench-<版本>-<日期>.tar.gz  与同名 .zip
set -euo pipefail

cd "$(dirname "$0")/.."
ROOT="$(pwd)"
SOURCE_DATE="$(date +%Y%m%d)"
VERSION="$(cat VERSION 2>/dev/null || echo 0.0.0)"
NAME="comic-workbench-${VERSION}-${SOURCE_DATE}"

WITH_DOCS=0
WITH_DATA=0
for arg in "$@"; do
  case "$arg" in
    --with-docs) WITH_DOCS=1 ;;
    --with-data) WITH_DATA=1 ;;
    --all) WITH_DOCS=1; WITH_DATA=1 ;;
    -h|--help) sed -n '2,12p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "未知参数: $arg（用 --help 看用法）" >&2; exit 2 ;;
  esac
done

# 带作品数据的包单独命名，避免覆盖纯代码包
[ "$WITH_DATA" = "1" ] && NAME="${NAME}-full"

OUT="$ROOT/dist"
mkdir -p "$OUT"
# staging 放临时目录：避免在项目内做批量删除（某些沙箱/安全策略会拦截 rm -rf 大量文件）
TMP="$(mktemp -d)"
STAGE="$TMP/$NAME"
mkdir -p "$STAGE"
echo "==> 打包 ${NAME}（$([ "$WITH_DATA" = 1 ] && echo '含作品数据' || echo '纯代码')）"

# 通用排除：缓存、虚拟环境、依赖目录、系统垃圾文件、本地大资产
EXCLUDES=(
  --exclude '__pycache__' --exclude '*.pyc' --exclude '.DS_Store'
  --exclude '.venv' --exclude 'venv' --exclude '.git' --exclude '.pytest_cache'
  --exclude 'node_modules' --exclude '.vite' --exclude '.workbuddy'
  # LoRA：保留训练好的 adapter（权重级一致性成果），排掉可重建/中间产物
  --exclude 'training*' --exclude 'dataset' --exclude '*_checkpoint.zip'
  --exclude 'preview' --exclude 'compare'
)

echo "    backend / frontend / scripts / workflows / deploy"
rsync -a "${EXCLUDES[@]}" "$ROOT/backend/" "$STAGE/backend/"
rsync -a "${EXCLUDES[@]}" "$ROOT/frontend/" "$STAGE/frontend/"
rsync -a "${EXCLUDES[@]}" "$ROOT/scripts/" "$STAGE/scripts/"
rsync -a "${EXCLUDES[@]}" "$ROOT/workflows/" "$STAGE/workflows/"

# 启动脚本与安装说明本来就在项目根（deploy/ 只是它们的源目录），直接带上
for f in start.sh start.bat INSTALL.md; do
  [ -f "$ROOT/$f" ] && cp "$ROOT/$f" "$STAGE/$f"
done
chmod +x "$STAGE/start.sh" "$STAGE/scripts/package.sh" 2>/dev/null || true

cp "$ROOT/README.md" "$STAGE/README.md"
cp "$ROOT/VERSION" "$STAGE/VERSION"

if [ "$WITH_DOCS" = "1" ]; then
  echo "    docs/（样张与截图）"
  rsync -a "${EXCLUDES[@]}" "$ROOT/docs/" "$STAGE/docs/"
fi
if [ "$WITH_DATA" = "1" ]; then
  echo "    projects/ + 数据库（作品数据）"
  rsync -a "${EXCLUDES[@]}" "$ROOT/projects/" "$STAGE/projects/"
  cp "$ROOT/comic_workbench.db" "$STAGE/comic_workbench.db" 2>/dev/null || true
fi

# 兜底清理缓存残留（rsync 已 exclude，这里再扫一遍；失败不影响打包）
/usr/bin/find "$STAGE" -name '__pycache__' -type d -prune -exec rm -rf {} + 2>/dev/null || true

echo "==> 压缩"
rm -f "$OUT/$NAME.tar.gz" "$OUT/$NAME.zip"
( cd "$TMP" && tar -czf "$OUT/$NAME.tar.gz" "$NAME" )
( cd "$TMP" && zip -qr "$OUT/$NAME.zip" "$NAME" )

echo ""
echo "==> 完成"
printf "    %-52s %s\n" "dist/$NAME.tar.gz" "$(du -h "$OUT/$NAME.tar.gz" | cut -f1)"
printf "    %-52s %s\n" "dist/$NAME.zip" "$(du -h "$OUT/$NAME.zip" | cut -f1)"
echo "    文件数: $(find "$STAGE" -type f | wc -l | tr -d ' ')   解压后体积: $(du -sh "$STAGE" | cut -f1)"
echo ""
echo "    包含: $([ "$WITH_DOCS" = 1 ] && echo -n 'docs ' ; [ "$WITH_DATA" = 1 ] && echo -n 'projects+db ' ; echo 'backend frontend(含dist) scripts workflows')"
echo "    未包含: $([ "$WITH_DATA" = 0 ] && echo -n '作品数据（加 --with-data 可带上）、')node_modules、虚拟环境、__pycache__、训练中间产物"
rm -rf "$TMP" 2>/dev/null || echo "    （临时目录未自动清理: $TMP）"
