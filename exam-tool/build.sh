#!/usr/bin/env bash

# 遇到任何错误时立即退出脚本
set -e

# 获取当前脚本所在文件夹的绝对路径，并切换到该路径下执行
DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" >/dev/null 2>&1 && pwd )"
cd "$DIR"

IMAGE_NAME="beiming/lti-exam-tool"
VERSION_FILE="$DIR/VERSION"

# ── 读取并自动 bump patch 版本 ──────────────────────────────────────────────
if [ ! -f "$VERSION_FILE" ]; then
  echo "1.0.0" > "$VERSION_FILE"
fi

CURRENT_VERSION=$(cat "$VERSION_FILE" | tr -d '[:space:]')
IFS='.' read -r MAJOR MINOR PATCH <<< "$CURRENT_VERSION"
PATCH=$((PATCH + 1))
NEW_VERSION="${MAJOR}.${MINOR}.${PATCH}"
echo "$NEW_VERSION" > "$VERSION_FILE"

echo "=================================================="
echo "📦 版本: ${CURRENT_VERSION} → ${NEW_VERSION}"
echo "=================================================="

# ── 构建镜像（同时打版本 tag 和 latest tag）────────────────────────────────
echo "🚀 开始构建 ${IMAGE_NAME}:${NEW_VERSION} 镜像..."
echo "=================================================="

docker build \
  --build-arg APP_VERSION="${NEW_VERSION}" \
  -t "${IMAGE_NAME}:${NEW_VERSION}" \
  -t "${IMAGE_NAME}:latest" \
  .

echo ""
echo "=================================================="
echo "🎉 构建成功！"
echo "   ${IMAGE_NAME}:${NEW_VERSION}"
echo "   ${IMAGE_NAME}:latest"
echo "=================================================="
