#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")/.."
# Build first. The preview mode never runs the helper or orders a window.
BIN_DIR="$(swift build --show-bin-path)"
"$BIN_DIR/afterglow-fn" --render-permissions "${1:?Pass an absolute output directory}"
