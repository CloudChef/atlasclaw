#!/usr/bin/env bash
set -euo pipefail

INSTALL_DIR="${INSTALL_DIR:-/opt/atlasclaw}"
SERVICE_PORT="${SERVICE_PORT:-8000}"
COMPOSE_URL="${COMPOSE_URL:-https://raw.githubusercontent.com/CloudChef/atlasclaw/main/build/docker-compose.yml}"
INSTALL_PROVIDERS="${INSTALL_PROVIDERS:-true}"
PROVIDERS_URL="${PROVIDERS_URL:-https://github.com/CloudChef/atlasclaw-providers/archive/refs/heads/main.zip}"

LLM_ID="${LLM_ID:-deepseek-main}"
LLM_PROVIDER="${LLM_PROVIDER:-deepseek}"
LLM_MODEL="${LLM_MODEL:-deepseek-chat}"
LLM_BASE_URL="${LLM_BASE_URL:-https://api.deepseek.com}"
LLM_API_TYPE="${LLM_API_TYPE:-openai}"
LLM_API_KEY="${LLM_API_KEY:-YOUR_API_KEY_HERE}"

ADMIN_USERNAME="${ADMIN_USERNAME:-admin}"
ADMIN_PASSWORD="${ADMIN_PASSWORD:-admin}"
JWT_SECRET_KEY="${JWT_SECRET_KEY:-atlasclaw-docker-secret-CHANGE-ME}"

require_cmd() {
  if ! command -v "$1" >/dev/null 2>&1; then
    echo "ERROR: required command not found: $1" >&2
    exit 1
  fi
}

compose() {
  docker compose "$@"
}

require_cmd docker
require_cmd curl
require_cmd chmod

if [ "$INSTALL_PROVIDERS" = "true" ]; then
  require_cmd unzip
fi

mkdir -p \
  "$INSTALL_DIR/workspace" \
  "$INSTALL_DIR/data" \
  "$INSTALL_DIR/extensions/providers" \
  "$INSTALL_DIR/extensions/skills" \
  "$INSTALL_DIR/extensions/channels"

cd "$INSTALL_DIR"

if [ ! -f docker-compose.yml ]; then
  curl -fL -o docker-compose.yml "$COMPOSE_URL"
else
  echo "docker-compose.yml already exists, keeping existing file."
fi

cat > workspace/atlasclaw.json <<EOF
{
  "workspace": {
    "path": "/app/workspace"
  },
  "database": {
    "type": "sqlite",
    "sqlite": {
      "path": "/app/data/atlasclaw.db"
    }
  },
  "providers_root": "/app/extensions/providers",
  "skills_root": "/app/extensions/skills",
  "channels_root": "/app/extensions/channels",
  "model": {
    "primary": "$LLM_ID",
    "fallbacks": [],
    "temperature": 0.2,
    "tokens": [
      {
        "id": "$LLM_ID",
        "provider": "$LLM_PROVIDER",
        "model": "$LLM_MODEL",
        "base_url": "$LLM_BASE_URL",
        "api_key": "$LLM_API_KEY",
        "api_type": "$LLM_API_TYPE"
      }
    ]
  },
  "auth": {
    "provider": "local",
    "local": {
      "enabled": true,
      "default_admin_username": "$ADMIN_USERNAME",
      "default_admin_password": "$ADMIN_PASSWORD"
    },
    "jwt": {
      "secret_key": "$JWT_SECRET_KEY",
      "expires_minutes": 480
    }
  }
}
EOF

chmod 600 workspace/atlasclaw.json

if [ "$INSTALL_PROVIDERS" = "true" ]; then
  cd "$INSTALL_DIR/extensions"
  rm -rf src atlasclaw-providers.zip
  curl -fL -o atlasclaw-providers.zip "$PROVIDERS_URL"
  unzip -q atlasclaw-providers.zip
  mv atlasclaw-providers-main src
  command cp -rf src/providers/* ./providers/ 2>/dev/null || true
  command cp -rf src/skills/* ./skills/ 2>/dev/null || true
  command cp -rf src/channels/* ./channels/ 2>/dev/null || true
  rm -rf src atlasclaw-providers.zip
fi

cd "$INSTALL_DIR"
compose up -d

echo "Waiting for AtlasClaw health check..."
for _ in $(seq 1 30); do
  if curl -fsS "http://localhost:${SERVICE_PORT}/api/health" >/dev/null; then
    echo "AtlasClaw is healthy: http://localhost:${SERVICE_PORT}"
    exit 0
  fi
  sleep 2
done

echo "AtlasClaw started, but health check did not pass within timeout."
echo "Check logs with: cd $INSTALL_DIR && docker compose logs atlasclaw"
exit 1
