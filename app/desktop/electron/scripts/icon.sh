#!/bin/bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
# This is the supplied dusk tile, copied unchanged from the buildout's icon/afterglow-icon.svg.
SOURCE="${AFTERGLOW_ICON_SVG:-$ROOT/assets/afterglow-icon.svg}"
for tool in sips iconutil; do
  command -v "$tool" >/dev/null || { echo "Missing $tool. Run this script on macOS." >&2; exit 1; }
done
mkdir -p "$ROOT/build"
TEMP="$(mktemp -d)"
trap 'rm -rf "$TEMP"' EXIT
ICONSET="$TEMP/afterglow.iconset"
mkdir -p "$ICONSET"
# The SVG's rounded rectangle leaves the corners transparent. No opaque canvas is added.
sips -s format png "$SOURCE" --out "$ROOT/build/afterglow.png" >/dev/null
sips -z 1024 1024 "$ROOT/build/afterglow.png" >/dev/null
for size in 16 32 128 256 512; do
  sips -z "$size" "$size" "$ROOT/build/afterglow.png" --out "$ICONSET/icon_${size}x${size}.png" >/dev/null
  double=$((size * 2))
  sips -z "$double" "$double" "$ROOT/build/afterglow.png" --out "$ICONSET/icon_${size}x${size}@2x.png" >/dev/null
done
iconutil --convert icns --output "$ROOT/build/afterglow.icns" "$ICONSET"
echo "Created build/afterglow.png and build/afterglow.icns"
