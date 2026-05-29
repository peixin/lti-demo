#!/usr/bin/env bash
# bump_version.sh — 手动调整 exam-tool 或 platform 的版本号
#
# 用法:
#   ./bump_version.sh <component> [major|minor|patch]
#
# 示例:
#   ./bump_version.sh exam-tool          # 默认 bump patch
#   ./bump_version.sh platform minor     # bump minor，重置 patch 为 0
#   ./bump_version.sh exam-tool major    # bump major，重置 minor/patch 为 0
#   ./bump_version.sh all                # 同时 bump 两个组件的 patch

set -e

DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" >/dev/null 2>&1 && pwd )"

bump_component() {
  local component="$1"
  local bump_type="${2:-patch}"
  local version_file="$DIR/$component/VERSION"

  if [ ! -f "$version_file" ]; then
    echo "❌ 找不到 $version_file，请先创建该文件。"
    exit 1
  fi

  local current
  current=$(cat "$version_file" | tr -d '[:space:]')
  IFS='.' read -r MAJOR MINOR PATCH <<< "$current"

  case "$bump_type" in
    major)
      MAJOR=$((MAJOR + 1)); MINOR=0; PATCH=0 ;;
    minor)
      MINOR=$((MINOR + 1)); PATCH=0 ;;
    patch)
      PATCH=$((PATCH + 1)) ;;
    *)
      echo "❌ 无效的 bump 类型: $bump_type（可选: major | minor | patch）"
      exit 1 ;;
  esac

  local new_version="${MAJOR}.${MINOR}.${PATCH}"
  echo "$new_version" > "$version_file"
  echo "✅ [$component] $current → $new_version"
}

COMPONENT="${1:-}"
BUMP_TYPE="${2:-patch}"

case "$COMPONENT" in
  exam-tool|platform)
    bump_component "$COMPONENT" "$BUMP_TYPE" ;;
  all)
    bump_component "exam-tool" "$BUMP_TYPE"
    bump_component "platform" "$BUMP_TYPE" ;;
  "")
    echo "用法: $0 <exam-tool|platform|all> [major|minor|patch]"
    exit 1 ;;
  *)
    echo "❌ 未知组件: $COMPONENT（可选: exam-tool | platform | all）"
    exit 1 ;;
esac
