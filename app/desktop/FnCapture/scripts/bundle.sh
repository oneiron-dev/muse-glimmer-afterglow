#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")/.."
swift build -c release --jobs 2
BIN_DIR="$(swift build -c release --show-bin-path)"
APP="$PWD/build/AfterglowFn.app"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"
cp "$BIN_DIR/afterglow-fn" "$APP/Contents/MacOS/afterglow-fn"
cp scripts/Info.plist "$APP/Contents/Info.plist"
cp scripts/AfterglowFn.icns "$APP/Contents/Resources/AfterglowFn.icns"
# Sign with an Apple Development identity when there is one: macOS then keeps the
# Accessibility, Input Monitoring, Microphone and Screen Recording grants across
# rebuilds. Without one, sign ad hoc (each rebuild asks for the grants again).
IDENTITY="$(security find-identity -v -p codesigning 2>/dev/null | sed -n 's/.*"\(Apple Development: [^"]*\)".*/\1/p' | head -1)"
if [[ -z "$IDENTITY" ]]; then
    printf 'WARNING: No Apple Development identity found. Signing ad hoc; every rebuild will ask for permissions again.\n' >&2
fi
codesign -s "${IDENTITY:--}" --force --deep "$APP"
printf 'Built and signed: %s\n' "$APP"
