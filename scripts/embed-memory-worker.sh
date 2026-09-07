#!/bin/bash
# Copy the built memory worker into the app bundle and sign it in place.
#
# Run from an Xcode build phase. In a configuration that expects a worker the
# absence of one is a build failure -- a shipped app whose Sync Now cannot run
# is exactly what M2.2d exists to remove. In Debug it is a warning, so the
# test suite and ordinary development builds do not require a 20 MB
# PyInstaller run.
set -euo pipefail

ROOT="${SRCROOT:-$(cd "$(dirname "$0")/.." && pwd)}/.."
[[ -d "$ROOT/memory" ]] || ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SOURCE="$ROOT/.build/memory-worker/dist/MemoryWorker.app"
HELPERS="${BUILT_PRODUCTS_DIR:-}/${CONTENTS_FOLDER_PATH:-}/Helpers"
# A *nested bundle*, not a bare directory. PyInstaller's onedir tree carries
# plain files -- `base_library.zip` among them -- and when such a tree sits
# loose inside an app, codesign walks into it while sealing the wrapper and
# refuses: "code object is not signed at all". Inside a proper .app those
# files are sealed by the helper's own signature instead, which is what the
# nested-code rules expect. The executable keeps `_internal` beside it, where
# PyInstaller looks for it.
APP="$HELPERS/MemoryWorker.app"

if [[ ! -x "$SOURCE/Contents/MacOS/MemoryWorker" ]]; then
  if [[ "${CONFIGURATION:-Debug}" == "Debug" ]]; then
    echo "warning: no bundled memory worker at .build/memory-worker; Sync Now will report the packaging gap. Run scripts/build-memory-worker.sh to include it."
    exit 0
  fi
  echo "error: ${CONFIGURATION} expects a bundled memory worker. Run scripts/build-memory-worker.sh first." >&2
  exit 1
fi

rm -rf "$APP"
mkdir -p "$HELPERS"
/usr/bin/ditto "$SOURCE" "$APP"
: <<'PLIST_UNUSED'
PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
	<key>CFBundleIdentifier</key><string>com.lianghongjing.WeChatCompanion.MemoryWorker</string>
	<key>CFBundleName</key><string>MemoryWorker</string>
	<key>CFBundleExecutable</key><string>memoryworker</string>
	<key>CFBundlePackageType</key><string>APPL</string>
	<key>CFBundleInfoDictionaryVersion</key><string>6.0</string>
	<key>CFBundleShortVersionString</key><string>1.0</string>
	<key>CFBundleVersion</key><string>1</string>
	<key>LSBackgroundOnly</key><true/>
</dict>
</plist>
PLIST_UNUSED

# Sign every nested Mach-O with this build's identity, deepest first, then the
# helper bundle itself, so the hardened runtime's library validation sees one
# Team ID throughout and the wrapper has a sealed subcomponent to verify.
# The helper must be signed with the *same* identity as the app: hardened
# runtime library validation compares Team IDs, so an ad-hoc helper inside a
# Developer-ID-signed app would fail to load its own Python at run time.
IDENTITY="${EXPANDED_CODE_SIGN_IDENTITY:-}"
[[ -z "$IDENTITY" ]] && IDENTITY="${CODE_SIGN_IDENTITY:-}"
if [[ -z "$IDENTITY" || "$IDENTITY" == "-" ]] && [[ -n "${DEVELOPMENT_TEAM:-}" ]]; then
  IDENTITY="$(security find-identity -v -p codesigning 2>/dev/null \
    | sed -n "s/.*\"\(Apple Development[^\"]*\)\".*/\1/p" | head -1)"
fi
[[ -z "$IDENTITY" ]] && IDENTITY="-"

if [[ "${CODE_SIGNING_ALLOWED:-YES}" == "YES" ]]; then
  echo "note: signing bundled memory worker with identity: ${IDENTITY:0:24}..."
  while IFS= read -r -d '' binary; do
    /usr/bin/codesign --force --options runtime --timestamp=none -s "$IDENTITY" "$binary" >/dev/null 2>&1 || true
  done < <(find "$APP/Contents" -type f \( -name '*.so' -o -name '*.dylib' \) -print0)
  if [[ -e "$APP/Contents/Frameworks/Python.framework" ]]; then
    /usr/bin/codesign --force --options runtime --timestamp=none -s "$IDENTITY" \
      "$APP/Contents/Frameworks/Python.framework" >/dev/null 2>&1 || true
  fi
  /usr/bin/codesign --force --options runtime --timestamp=none -s "$IDENTITY" "$APP"
fi
