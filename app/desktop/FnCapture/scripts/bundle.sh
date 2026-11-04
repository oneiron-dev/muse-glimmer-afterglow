#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")/.."
swift build -c release --jobs 2
BIN_DIR="$(swift build -c release --show-bin-path)"
APP="$PWD/build/Afterglow fn.app"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"
cp "$BIN_DIR/afterglow-fn" "$APP/Contents/MacOS/afterglow-fn"
cp scripts/Info.plist "$APP/Contents/Info.plist"
codesign -s - --force --deep "$APP"
printf 'Built and signed: %s\n' "$APP"
