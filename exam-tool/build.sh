#!/usr/bin/env bash

# 遇到任何错误时立即退出脚本
set -e

# 获取当前脚本所在文件夹的绝对路径，并切换到该路径下执行
DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" >/dev/null 2>&1 && pwd )"
cd "$DIR"

IMAGE_NAME="beiming/lti-exam-tool"
VERSION_FILE="$DIR/VERSION"

# ── 读取版本（不自动 bump，版本由人工维护 VERSION 文件）─────────────────────
if [ ! -f "$VERSION_FILE" ]; then
  echo "❌ 找不到 $VERSION_FILE，请先手动创建并写入版本号（如 1.0.0）"
  exit 1
fi

NEW_VERSION=$(cat "$VERSION_FILE" | tr -d '[:space:]')

echo "=================================================="
echo "📦 版本: ${NEW_VERSION}"
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
