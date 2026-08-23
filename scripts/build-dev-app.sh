#!/bin/bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PROJECT="$ROOT/apps/WeChatCompanion/WeChatCompanion.xcodeproj"
DERIVED_DATA="$ROOT/.build/WeChatCompanion-arm64"
BUILT_APP="$DERIVED_DATA/Build/Products/Debug/WeChat Companion.app"
INSTALL_DIR="$HOME/Applications"
INSTALL_APP="$INSTALL_DIR/WeChat Companion Dev.app"
EXPECTED_IDENTIFIER="com.lianghongjing.WeChatCompanion"
EXPECTED_TEAM="5M5KT5ZG74"
BUILD_LOG="$(mktemp "${TMPDIR:-/tmp}/wechat-companion-build.XXXXXX")"
STAGING_ROOT=""

cleanup() {
  rm -f "$BUILD_LOG"
  if [[ -n "$STAGING_ROOT" ]]; then rm -rf "$STAGING_ROOT"; fi
}
trap cleanup EXIT

verify_signature() {
  local app="$1"
  local details identifier team

  if ! codesign --verify --deep --strict "$app" >/dev/null 2>&1; then
    echo "Signature verification failed." >&2
    return 1
  fi
  details="$(codesign -dv --verbose=4 "$app" 2>&1)"
  identifier="$(sed -n 's/^Identifier=//p' <<<"$details")"
  team="$(sed -n 's/^TeamIdentifier=//p' <<<"$details")"

  if [[ "$identifier" != "$EXPECTED_IDENTIFIER" ]]; then
    echo "Unexpected app identifier." >&2
    return 1
  fi
  if [[ "$team" != "$EXPECTED_TEAM" ]]; then
    echo "Missing or unexpected TeamIdentifier." >&2
    return 1
  fi
  if grep -q '^Signature=adhoc$' <<<"$details" || ! grep -q '^Authority=Apple Development:' <<<"$details"; then
    echo "Expected Apple Development signing, not ad-hoc signing." >&2
    return 1
  fi
}

if ! xcodebuild \
  -project "$PROJECT" \
  -scheme WeChatCompanion \
  -configuration Debug \
  -destination 'platform=macOS,arch=arm64' \
  -derivedDataPath "$DERIVED_DATA" \
  ONLY_ACTIVE_ARCH=YES \
  DEVELOPMENT_TEAM="$EXPECTED_TEAM" \
  CODE_SIGN_STYLE=Automatic \
  -allowProvisioningUpdates \
  build >"$BUILD_LOG" 2>&1; then
  echo "Xcode build failed." >&2
  exit 1
fi

verify_signature "$BUILT_APP"

mkdir -p "$INSTALL_DIR"
STAGING_ROOT="$(mktemp -d "$INSTALL_DIR/.wechat-companion-dev.XXXXXX")"
STAGED_APP="$STAGING_ROOT/WeChat Companion Dev.app"

ditto "$BUILT_APP" "$STAGED_APP"
verify_signature "$STAGED_APP"

if [[ -e "$INSTALL_APP" ]]; then
  /usr/bin/swift - "$INSTALL_APP" "$STAGED_APP" <<'SWIFT'
import Foundation

let arguments = CommandLine.arguments
guard arguments.count == 3 else { throw CocoaError(.fileReadInvalidFileName) }
let installed = URL(fileURLWithPath: arguments[1])
let staged = URL(fileURLWithPath: arguments[2])
_ = try FileManager.default.replaceItemAt(installed, withItemAt: staged)
SWIFT
else
  mv "$STAGED_APP" "$INSTALL_APP"
fi

verify_signature "$INSTALL_APP"
printf '%s\n' "$INSTALL_APP"
