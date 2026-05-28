#!/usr/bin/env bash

# 遇到任何错误时立即退出脚本
set -e

# 获取当前脚本所在文件夹的绝对路径，并切换到该路径下执行
DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" >/dev/null 2>&1 && pwd )"
cd "$DIR"

IMAGE_NAME="beiming/lti-exam-tool"
TAG="latest"

echo "=================================================="
echo "🚀 开始构建 ${IMAGE_NAME}:${TAG} 镜像..."
echo "=================================================="

# 执行 docker 构建
docker build -t "${IMAGE_NAME}:${TAG}" .

echo ""
echo "=================================================="
echo "🎉 镜像 ${IMAGE_NAME}:${TAG} 构建成功！"
echo "=================================================="
