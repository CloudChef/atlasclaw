#!/usr/bin/env bash
set -euo pipefail

INSTALL_DIR="${INSTALL_DIR:-/opt/atlasclaw}"
PROVIDERS_URL="${PROVIDERS_URL:-https://github.com/CloudChef/atlasclaw-providers/archive/refs/heads/main.zip}"

require_cmd() {
  if ! command -v "$1" >/dev/null 2>&1; then
    echo "ERROR: required command not found: $1" >&2
    exit 1
  fi
}

require_cmd docker
require_cmd curl
require_cmd unzip

if [ ! -f "$INSTALL_DIR/docker-compose.yml" ]; then
  echo "ERROR: docker-compose.yml not found in $INSTALL_DIR" >&2
  exit 1
fi

echo "Upgrading AtlasClaw providers..."
mkdir -p \
  "$INSTALL_DIR/extensions/providers" \
  "$INSTALL_DIR/extensions/skills" \
  "$INSTALL_DIR/extensions/channels"

cd "$INSTALL_DIR/extensions"
rm -rf src atlasclaw-providers.zip
curl -fL -o atlasclaw-providers.zip "$PROVIDERS_URL"
unzip -q atlasclaw-providers.zip
mv atlasclaw-providers-main src
command cp -rf src/providers/* ./providers/ 2>/dev/null || true
command cp -rf src/skills/* ./skills/ 2>/dev/null || true
command cp -rf src/channels/* ./channels/ 2>/dev/null || true
rm -rf src atlasclaw-providers.zip

echo "Pulling latest AtlasClaw image..."
cd "$INSTALL_DIR"
docker compose pull

echo "Restarting AtlasClaw..."
docker compose up -d

echo "AtlasClaw upgrade finished."
