#!/bin/bash
# Build the bundled memory worker: a self-contained executable the app runs.
#
# The memory layer is Python and the app ships no interpreter for a user to
# install, so the worker is frozen with PyInstaller into a directory that
# carries its own CPython. Everything it imports is the standard library --
# the sync path deliberately has no third-party dependency -- so nothing is
# downloaded beyond the pinned build tool itself.
#
# Two shape decisions, both measured rather than assumed.
#
# `--onedir`, not `--onefile`. A onefile binary unpacks CPython into a
# temporary directory at run time; under the app's hardened runtime, library
# validation then refuses to map it, because the extracted framework still
# carries its upstream signature and the Team IDs do not match. A onedir tree
# can be signed in place with the app's own identity, so every nested Mach-O
# matches and hardening stays on. The alternative -- a
# `disable-library-validation` entitlement -- would weaken the app to suit the
# build, which is the wrong way round.
#
# `--windowed`, so the output is a real `.app`. A loose onedir directory
# copied into an app contains plain files (`base_library.zip` among them);
# codesign walks into them while sealing the wrapper and refuses with "code
# object is not signed at all". A nested bundle carries its own seal, which is
# what the nested-code rules expect. `LSBackgroundOnly` keeps it out of the
# Dock; the app runs the executable directly, never through LaunchServices.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
BUILD="$ROOT/.build/memory-worker"
OUT="$BUILD/dist/MemoryWorker.app"
EXECUTABLE="$OUT/Contents/MacOS/MemoryWorker"

# Pinned: the build tool, and the interpreter the worker will carry.
PYINSTALLER_VERSION="6.11.1"
REQUIRED_PYTHON_MINOR="12"

PYTHON="${MEMORY_WORKER_PYTHON:-}"
if [[ -z "$PYTHON" ]]; then
  for candidate in python3.12 python3; do
    if command -v "$candidate" >/dev/null 2>&1; then PYTHON="$(command -v "$candidate")"; break; fi
  done
fi
if [[ -z "$PYTHON" ]]; then
  echo "No Python found to BUILD the worker. This is a build-time tool only:" >&2
  echo "the shipped app depends on no interpreter." >&2
  exit 1
fi
minor="$("$PYTHON" -c 'import sys; print(sys.version_info[1])')"
if [[ "$minor" != "$REQUIRED_PYTHON_MINOR" ]]; then
  echo "Refusing to build with Python 3.$minor; the worker is pinned to 3.$REQUIRED_PYTHON_MINOR." >&2
  echo "Set MEMORY_WORKER_PYTHON to a 3.$REQUIRED_PYTHON_MINOR interpreter." >&2
  exit 1
fi

VENV="$BUILD/venv"
if [[ ! -x "$VENV/bin/pyinstaller" ]]; then
  rm -rf "$VENV"
  "$PYTHON" -m venv "$VENV"
  "$VENV/bin/pip" -q install --disable-pip-version-check "pyinstaller==$PYINSTALLER_VERSION"
fi

rm -rf "$BUILD/dist" "$BUILD/work"
"$VENV/bin/pyinstaller" \
  --onedir --noconfirm --clean --log-level ERROR \
  --windowed --name MemoryWorker \
  --osx-bundle-identifier com.lianghongjing.WeChatCompanion.MemoryWorker \
  --distpath "$BUILD/dist" --workpath "$BUILD/work" --specpath "$BUILD" \
  --paths "$ROOT/memory" --paths "$ROOT/bridge" \
  --hidden-import store_access \
  --hidden-import message_source \
  --hidden-import rion_reader_adapter \
  --hidden-import memory_paths \
  --hidden-import memory_sync \
  --hidden-import memory_freshness \
  --exclude-module tkinter \
  --exclude-module unittest \
  --exclude-module pydoc \
  "$ROOT/memory/memory_worker.py" >/dev/null

if [[ ! -x "$EXECUTABLE" ]]; then
  echo "PyInstaller produced no executable at $EXECUTABLE" >&2
  exit 1
fi

# A helper that is not a user-facing app: no Dock icon, no menu bar.
/usr/libexec/PlistBuddy -c "Add :LSBackgroundOnly bool true" "$OUT/Contents/Info.plist" >/dev/null 2>&1 \
  || /usr/libexec/PlistBuddy -c "Set :LSBackgroundOnly true" "$OUT/Contents/Info.plist" >/dev/null 2>&1 || true

# The nested bundle needs its own valid, unique, reverse-DNS identifier.
# PyInstaller defaults to the bare product name ("MemoryWorker"), which is
# neither, and which a notarization service is entitled to reject. It also
# becomes the code-signing identifier, so getting it right here fixes both.
identifier="$(/usr/libexec/PlistBuddy -c "Print :CFBundleIdentifier" "$OUT/Contents/Info.plist" 2>/dev/null || echo)"
if [[ "$identifier" != "com.lianghongjing.WeChatCompanion.MemoryWorker" ]]; then
  echo "Nested bundle identifier is '$identifier'; expected the reverse-DNS one." >&2
  exit 1
fi

# It must run with nothing in the environment: no interpreter on PATH, no
# PYTHONPATH, no site-packages. If this fails the bundle is not self-contained.
probe="$(printf '{"op":"paths"}' | env -i "$EXECUTABLE")"
if [[ "$probe" != *"Library/Application Support/WeChatCompanion/memory.sqlite"* ]]; then
  echo "The built worker did not answer the paths probe; not self-contained." >&2
  exit 1
fi

printf '%s\n' "$OUT"
