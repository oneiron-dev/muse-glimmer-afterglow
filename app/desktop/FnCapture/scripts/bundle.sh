#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")/.."
swift build -c release --jobs 2
BIN_DIR="$(swift build -c release --show-bin-path)"
APP="$PWD/build/AfterglowFn.app"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"
cp "$BIN_DIR/afterglow-fn" "$APP/Contents/MacOS/afterglow-fn"
cp scripts/Info.plist "$APP/Contents/Info.plist"
# Sign with an Apple Development identity when there is one: macOS then keeps the
# Accessibility, Input Monitoring, Microphone and Screen Recording grants across
# rebuilds. Without one, sign ad hoc (each rebuild asks for the grants again).
IDENTITY="$(security find-identity -v -p codesigning 2>/dev/null | sed -n 's/.*"\(Apple Development: [^"]*\)".*/\1/p' | head -1)"
codesign -s "${IDENTITY:--}" --force --deep "$APP"
printf 'Built and signed: %s\n' "$APP"
