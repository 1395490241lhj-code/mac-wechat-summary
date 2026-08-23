#!/bin/bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
if ! APP_PATH="$("$ROOT/scripts/build-dev-app.sh" 2>/dev/null)"; then
  echo "WeChat Companion build failed." >&2
  exit 1
fi
RESULT="$HOME/Library/Application Support/WeChatCompanion/interaction-diagnostics.json"
BEFORE="missing"

if [[ -f "$RESULT" ]]; then
  BEFORE="$(stat -f '%m:%z' "$RESULT"):$(cksum "$RESULT")"
fi

open -na "$APP_PATH" --args --run-interaction-diagnostics

for _ in $(seq 1 45); do
  if [[ -f "$RESULT" ]]; then
    AFTER="$(stat -f '%m:%z' "$RESULT"):$(cksum "$RESULT")"
    if [[ "$AFTER" != "$BEFORE" ]]; then
      cat "$RESULT"
      exit 0
    fi
  fi
  sleep 1
done

echo "Timed out waiting for fresh privacy-safe interaction diagnostics metadata." >&2
exit 1
