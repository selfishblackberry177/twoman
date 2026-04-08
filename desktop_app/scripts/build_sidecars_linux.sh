#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
APP_ROOT="$ROOT/desktop_app"
BUILD_ROOT="$APP_ROOT/build/linux-sidecars"
VENV_DIR="$BUILD_ROOT/venv"
DIST_DIR="$APP_ROOT/src-tauri/resources/sidecars/linux"
HELPER_NAME="${TWOMAN_HELPER_BINARY_BASENAME:-twoman-helper}"
GATEWAY_NAME="${TWOMAN_GATEWAY_BINARY_BASENAME:-twoman-gateway}"

rm -rf "$BUILD_ROOT"
mkdir -p "$BUILD_ROOT" "$DIST_DIR"

python3 -m venv "$VENV_DIR"
"$VENV_DIR/bin/pip" install --upgrade pip wheel >/dev/null
"$VENV_DIR/bin/pip" install -r "$ROOT/requirements.txt" pyinstaller >/dev/null

"$VENV_DIR/bin/pyinstaller" \
  --noconfirm \
  --clean \
  --onefile \
  --strip \
  --name "$HELPER_NAME" \
  --paths "$ROOT" \
  --hidden-import local_client.helper \
  --hidden-import twoman_protocol \
  --hidden-import twoman_transport \
  --distpath "$DIST_DIR" \
  --workpath "$BUILD_ROOT/work-helper" \
  --specpath "$BUILD_ROOT/spec-helper" \
  "$ROOT/local_client/helper.py"

"$VENV_DIR/bin/pyinstaller" \
  --noconfirm \
  --clean \
  --onefile \
  --strip \
  --name "$GATEWAY_NAME" \
  --paths "$ROOT" \
  --distpath "$DIST_DIR" \
  --workpath "$BUILD_ROOT/work-gateway" \
  --specpath "$BUILD_ROOT/spec-gateway" \
  "$ROOT/desktop_client/socks_gateway.py"

echo "Built Linux sidecars in $DIST_DIR"
