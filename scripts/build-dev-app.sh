#!/bin/bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PROJECT="$ROOT/apps/WeChatCompanion/WeChatCompanion.xcodeproj"
DERIVED_DATA="$ROOT/.build/WeChatCompanion-arm64"
APP_PATH="$DERIVED_DATA/Build/Products/Debug/WeChat Companion.app"

xcodebuild \
  -project "$PROJECT" \
  -scheme WeChatCompanion \
  -configuration Debug \
  -destination 'platform=macOS,arch=arm64' \
  -derivedDataPath "$DERIVED_DATA" \
  ONLY_ACTIVE_ARCH=YES \
  CODE_SIGN_STYLE=Manual \
  CODE_SIGN_IDENTITY=- \
  build >&2

printf '%s\n' "$APP_PATH"
