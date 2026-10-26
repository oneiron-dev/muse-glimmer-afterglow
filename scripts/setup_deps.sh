#!/usr/bin/env bash
# Fetch the pre-existing dependencies into .deps/ (gitignored). Nothing here is copied into the repository.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DEPS="$ROOT/.deps"
AFTERGLOW_REPO="https://github.com/oneiron-dev/afterglow.git"
AFTERGLOW_COMMIT="06e9ecf754a25ffbf0fed3eae812aea72994a9a4"

mkdir -p "$DEPS"
if [ ! -d "$DEPS/afterglow/.git" ]; then
  git clone --quiet "$AFTERGLOW_REPO" "$DEPS/afterglow"
fi
git -C "$DEPS/afterglow" fetch --quiet origin
git -C "$DEPS/afterglow" checkout --quiet --detach "$AFTERGLOW_COMMIT"
echo "afterglow at $(git -C "$DEPS/afterglow" rev-parse HEAD)"
