#!/bin/bash
# Xuanwu Build Script
# Usage: ./build.sh --mode opensource|enterprise --tag TAG [--username USER] [--password PASS]
#
# Fixed registry: registry.cn-shanghai.aliyuncs.com/xuanwu

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Fixed configuration (no user override)
REGISTRY="registry.cn-shanghai.aliyuncs.com"
NAMESPACE="xuanwu"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BUILD_DIR="$SCRIPT_DIR"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

# Parse arguments
MODE=""
TAG=""
USERNAME=""
PASSWORD=""

while [[ $# -gt 0 ]]; do
    case $1 in
        --mode)
            MODE="$2"
            shift 2
            ;;
        --tag)
            TAG="$2"
            shift 2
            ;;
        --username|-u)
            USERNAME="$2"
            shift 2
            ;;
        --password|-p)
            PASSWORD="$2"
            shift 2
            ;;
        --help|-h)
            show_usage
            exit 0
            ;;
        *)
            print_error "Unknown option: $1"
            show_usage
            exit 1
            ;;
    esac
done

# Function to print status
print_status() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

print_success() {
    echo -e "${GREEN}[SUCCESS]${NC} $1"
}

print_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

print_warning() {
    echo -e "${YELLOW}[WARNING]${NC} $1"
}

# Function to show usage
show_usage() {
    cat << EOF
Xuanwu Build Script

Usage:
    ./build.sh --mode opensource|enterprise --tag TAG [--username USER] [--password PASS]

Options:
    --mode          Build mode: opensource or enterprise (required)
    --tag           Image version tag, e.g., v0.6.3 (required)
    --username, -u  Registry username (required for push)
    --password, -p  Registry password (required for push)
    --help, -h      Show this help message

Examples:
    # Build opensource with specific tag and push
    ./build.sh --mode opensource --tag v0.6.3 --username myuser --password mypass

    # Build enterprise with specific tag and push
    ./build.sh --mode enterprise --tag v0.6.3 --username myuser --password mypass

Modes:
    opensource  - Lightweight build with SQLite (image: xuanwu)
    enterprise  - Full build with MySQL 8.4 (image: xuanwu-official)

Registry:
    Fixed: registry.cn-shanghai.aliyuncs.com/xuanwu
EOF
}

# Validate required parameters
if [[ -z "$MODE" ]]; then
    print_error "Mode is required. Use --mode opensource or --mode enterprise"
    show_usage
    exit 1
fi

if [[ -z "$TAG" ]]; then
    print_error "Tag is required. Use --tag <version>, e.g., --tag v0.6.3"
    show_usage
    exit 1
fi

if [[ "$MODE" != "opensource" && "$MODE" != "enterprise" ]]; then
    print_error "Invalid mode: $MODE. Must be 'opensource' or 'enterprise'"
    exit 1
fi

if [[ -z "$USERNAME" || -z "$PASSWORD" ]]; then
    print_error "Username and password are required for push"
    show_usage
    exit 1
fi

# Set mode-specific variables and fixed image names
if [[ "$MODE" == "opensource" ]]; then
    BASE_IMAGE_NAME="xuanwu"
    DOCKERFILE="Dockerfile"
    COMPOSE_FILE="docker-compose.yml"
    DB_TYPE="sqlite"
else
    BASE_IMAGE_NAME="xuanwu-official"
    DOCKERFILE="Dockerfile.enterprise"
    COMPOSE_FILE="docker-compose.enterprise.yml"
    DB_TYPE="mysql"
fi

# Fixed image path
FULL_REGISTRY_PATH="${REGISTRY}/${NAMESPACE}"
IMAGE_NAME="${FULL_REGISTRY_PATH}/${BASE_IMAGE_NAME}"

echo ""
echo -e "${GREEN}========================================${NC}"
echo -e "${GREEN}  Xuanwu Build Script${NC}"
echo -e "${GREEN}========================================${NC}"
echo ""
print_status "Build Mode:    $MODE"
print_status "Registry:      $FULL_REGISTRY_PATH"
print_status "Image Name:    $IMAGE_NAME"
print_status "Version Tag:   $TAG"
print_status "Database:      $DB_TYPE"
echo ""

# Step 1: Check prerequisites
print_status "Checking prerequisites..."

if ! command -v docker &> /dev/null; then
    print_error "Docker is not installed. Please install Docker first."
    exit 1
fi

print_success "Prerequisites check passed (Docker)"
echo ""

# Step 2: Prepare build environment
print_status "Preparing build environment..."

cd "$BUILD_DIR"

# Create necessary directories
mkdir -p "$BUILD_DIR/config"
mkdir -p "$BUILD_DIR/data"
mkdir -p "$BUILD_DIR/logs"

if [[ "$MODE" == "enterprise" ]]; then
    mkdir -p "$BUILD_DIR/secrets"
    mkdir -p "$BUILD_DIR/mysql-data"
fi

print_success "Build directories created"
echo ""

# Step 3: Generate configuration
print_status "Generating configuration..."

# Generate passwords for enterprise mode
if [[ "$MODE" == "enterprise" ]]; then
    if [ ! -f "$BUILD_DIR/secrets/mysql_root_password.txt" ]; then
        openssl rand -base64 32 > "$BUILD_DIR/secrets/mysql_root_password.txt"
        chmod 600 "$BUILD_DIR/secrets/mysql_root_password.txt"
        print_status "Generated MySQL root password"
    fi

    if [ ! -f "$BUILD_DIR/secrets/mysql_password.txt" ]; then
        openssl rand -base64 32 > "$BUILD_DIR/secrets/mysql_password.txt"
        chmod 600 "$BUILD_DIR/secrets/mysql_password.txt"
        print_status "Generated MySQL user password"
    fi
fi

# Generate xuanwu.json if not exists
if [ ! -f "$BUILD_DIR/config/xuanwu.json" ]; then
    if [[ "$MODE" == "enterprise" ]]; then
        MYSQL_PASSWORD=$(cat "$BUILD_DIR/secrets/mysql_password.txt")
        DB_CONFIG='{
      "type": "mysql",
      "mysql": {
        "host": "mysql",
        "port": 3306,
        "database": "xuanwu",
        "user": "xuanwu",
        "password": "'$MYSQL_PASSWORD'",
        "charset": "utf8mb4"
      },
      "pool_size": 20,
      "max_overflow": 30
    }'
    else
        DB_CONFIG='{
      "type": "sqlite",
      "sqlite": {
        "path": "/app/data/xuanwu.db"
      }
    }'
    fi

    cat > "$BUILD_DIR/config/xuanwu.json" << EOF
{
  "_comment": "Xuanwu Configuration - Auto-generated by build script",
  "workspace": {
    "path": "/app/workspace"
  },
  "database": $DB_CONFIG,
  "model": {
    "primary": "deepseek-main",
    "fallbacks": [],
    "temperature": 0.2,
    "selection_strategy": "health",
    "tokens": [
      {
        "id": "deepseek-main",
        "provider": "deepseek",
        "model": "deepseek-chat",
        "base_url": "https://api.deepseek.com",
        "api_key": "YOUR_API_KEY_HERE",
        "api_type": "openai",
        "priority": 100,
        "weight": 100
      }
    ]
  },
  "providers_root": "/app/extensions/providers",
  "skills_root": "/app/extensions/skills",
  "channels_root": "/app/extensions/channels",
  "service_providers": {},
  "webhook": {
    "enabled": false,
    "header_name": "X-Xuanwu-SK",
    "systems": []
  },
  "auth": {
    "enabled": true,
    "provider": "local",
    "cache_ttl_seconds": 300,
    "local": {
      "enabled": true,
      "default_admin_username": "admin",
      "default_admin_password": "admin"
    },
    "jwt": {
      "header_name": "Xuanwu-Authenticate",
      "cookie_name": "Xuanwu-Authenticate",
      "issuer": "xuanwu",
      "secret_key": "xuanwu-docker-secret-CHANGE-ME",
      "expires_minutes": 480
    }
  },
  "agent_defaults": {
    "max_concurrent": 10,
    "timeout_seconds": 600,
    "max_tool_calls": 50
  }
}
EOF

    chmod 600 "$BUILD_DIR/config/xuanwu.json"
    print_status "Generated config/xuanwu.json"
fi

print_success "Configuration completed"
echo ""

# Step 4: Copy required files to build directory
print_status "Copying project files..."

cp "$PROJECT_ROOT/requirements.txt" "$BUILD_DIR/"
cp -r "$PROJECT_ROOT/app" "$BUILD_DIR/"
cp -r "$PROJECT_ROOT/migrations" "$BUILD_DIR/"
cp "$PROJECT_ROOT/alembic.ini" "$BUILD_DIR/"

print_success "Project files copied"
echo ""

# Step 5: Build Docker image
print_status "Building Docker image..."

cd "$BUILD_DIR"

docker build \
    -f "$DOCKERFILE" \
    --build-arg BUILD_DATE=$(date -u +'%Y-%m-%dT%H:%M:%SZ') \
    --build-arg VERSION="$TAG" \
    -t "$IMAGE_NAME:$TAG" \
    -t "$IMAGE_NAME:latest" \
    .

print_success "Docker image built: $IMAGE_NAME:$TAG"
echo ""

# Step 6: Verify image
print_status "Verifying Docker image..."

if docker image inspect "$IMAGE_NAME:$TAG" > /dev/null 2>&1; then
    IMAGE_SIZE=$(docker images --format "{{.Size}}" "$IMAGE_NAME:$TAG")
    print_success "Image verified (Size: $IMAGE_SIZE)"
else
    print_error "Image verification failed"
    exit 1
fi

echo ""

# Step 7: Push image to registry (always push)
print_status "Logging into registry: $REGISTRY..."
echo "$PASSWORD" | docker login -u "$USERNAME" --password-stdin "$REGISTRY"
print_success "Logged in successfully"
echo ""

print_status "Pushing image to $FULL_REGISTRY_PATH..."
docker push "$IMAGE_NAME:$TAG"
docker push "$IMAGE_NAME:latest"
print_success "Image pushed successfully"
echo ""
print_status "Image location: $IMAGE_NAME:$TAG"

# Step 8: Clean up build artifacts
print_status "Cleaning up temporary files..."

rm -rf "$BUILD_DIR/app"
rm -rf "$BUILD_DIR/migrations"
rm -rf "$BUILD_DIR/alembic.ini"
rm -f "$BUILD_DIR/requirements.txt"
rm -rf "$BUILD_DIR/.venv"

print_success "Cleanup completed"
echo ""

# Create symlink to compose file for convenience
ln -sf "$COMPOSE_FILE" "$BUILD_DIR/docker-compose.yml"

# Summary
echo -e "${GREEN}========================================${NC}"
echo -e "${GREEN}  Build Completed Successfully!${NC}"
echo -e "${GREEN}========================================${NC}"
echo ""
echo "Build Mode:     $MODE"
echo "Registry:       $FULL_REGISTRY_PATH"
echo "Image:          $IMAGE_NAME:$TAG"
echo "Pushed:         true"
echo "Configuration:  $BUILD_DIR/config/xuanwu.json"
echo "Compose File:   $BUILD_DIR/$COMPOSE_FILE"
if [[ "$MODE" == "enterprise" ]]; then
    echo "Secrets:        $BUILD_DIR/secrets/"
fi
echo ""
echo "Image has been pushed to:"
echo "  - $IMAGE_NAME:$TAG"
echo "  - $IMAGE_NAME:latest"
echo ""
echo "To deploy on target server, please refer to the README for detailed deployment steps:"
if [[ "$MODE" == "enterprise" ]]; then
    echo "  - See: $BUILD_DIR/README_ENT.md"
else
    echo "  - See: $BUILD_DIR/README.md"
fi
echo ""
