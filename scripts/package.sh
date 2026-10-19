#!/bin/bash
# Build an installable Decky plugin zip: out/deck-as-controller.zip
set -euo pipefail

NAME="deck-as-controller"
cd "$(dirname "$0")/.."
pnpm build >/dev/null

STAGE="$(mktemp -d)"
mkdir -p "$STAGE/$NAME/dist"
cp dist/index.js "$STAGE/$NAME/dist/"
cp main.py plugin.json package.json README.md "$STAGE/$NAME/"
# rsync when it is there, plain cp otherwise (the plugin only needs a Linux host,
# which has both, but the build shouldn't depend on the tooling either way).
if command -v rsync >/dev/null; then
  rsync -a --exclude __pycache__ py_modules "$STAGE/$NAME/"
else
  cp -r py_modules "$STAGE/$NAME/"
  find "$STAGE/$NAME/py_modules" -name __pycache__ -type d -prune -exec rm -rf {} +
fi

mkdir -p out
rm -f "out/$NAME.zip"
(cd "$STAGE" && zip -qr - "$NAME") > "out/$NAME.zip"
rm -rf "$STAGE"
echo "out/$NAME.zip"
