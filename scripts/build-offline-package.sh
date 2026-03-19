#!/bin/bash
# Build offline AtlasClaw package with all dependencies and providers pre-installed
# Usage: ./scripts/build-offline-package.sh

set -e

echo "========================================"
echo "Building AtlasClaw Offline Package"
echo "========================================"

# Configuration
PACKAGE_NAME="atlasclaw-offline"
BUILD_DIR="build/${PACKAGE_NAME}"
OUTPUT_ZIP="${PACKAGE_NAME}.zip"

# Clean previous builds
echo "Cleaning previous builds..."
rm -rf build/ "${OUTPUT_ZIP}"

# Create build directory
echo "Creating package structure..."
mkdir -p "${BUILD_DIR}/atlasclaw/app"

# Copy AtlasClaw core files
echo "Copying AtlasClaw core files..."
cp -r app/atlasclaw/* "${BUILD_DIR}/atlasclaw/app/"
cp requirements.txt "${BUILD_DIR}/"
cp atlasclaw.json "${BUILD_DIR}/"

# Copy providers from atlasclaw-providers
echo "Copying providers..."
if [ -d "../atlasclaw-providers/providers" ]; then
    cp -r ../atlasclaw-providers/providers "${BUILD_DIR}/atlasclaw_providers/"
    echo "  - Providers copied from ../atlasclaw-providers"
elif [ -d "atlasclaw-providers/providers" ]; then
    cp -r atlasclaw-providers/providers "${BUILD_DIR}/atlasclaw_providers/"
    echo "  - Providers copied from atlasclaw-providers/"
else
    mkdir -p "${BUILD_DIR}/atlasclaw_providers"
    echo "  - Warning: No providers found, created empty directory"
fi

# Create virtual environment and install dependencies
echo ""
echo "Installing Python dependencies (this may take a few minutes)..."
cd "${BUILD_DIR}"
python3 -m venv venv
source venv/bin/activate

# Upgrade pip
pip install --upgrade pip -q

# Install all dependencies
pip install -r requirements.txt

# Verify installation
echo ""
echo "Verifying installation..."
python -c "import atlasclaw.app.atlasclaw.main; print('  ✓ AtlasClaw imported successfully')"

# Create marker file
touch venv/.requirements_installed

cd ../..

# Create atlasclaw.sh launcher
echo ""
echo "Creating atlasclaw.sh launcher..."
cat > "${BUILD_DIR}/atlasclaw.sh" << 'LAUNCHER_EOF'
#!/bin/bash
# AtlasClaw Offline Launcher
# This script automatically activates the pre-configured environment and starts the service

set -e

# Colors
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

# Get script directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# Default port
PORT="${1:-8000}"

echo -e "${GREEN}AtlasClaw Offline Launcher${NC}"
echo "=========================="

# Check if virtual environment exists
if [ ! -d "venv" ]; then
    echo "Error: Virtual environment not found"
    exit 1
fi

# Activate virtual environment
echo "Activating environment..."
source venv/bin/activate

# Verify Python can import atlasclaw
echo "Verifying installation..."
python -c "import atlasclaw.app.atlasclaw.main" 2>/dev/null || {
    echo "Error: Failed to import AtlasClaw modules"
    exit 1
}

# Start service
echo ""
echo -e "${GREEN}Starting AtlasClaw on http://0.0.0.0:$PORT${NC}"
echo "Press Ctrl+C to stop"
echo ""

exec uvicorn atlasclaw.app.atlasclaw.main:app --host 0.0.0.0 --port "$PORT"
LAUNCHER_EOF

chmod +x "${BUILD_DIR}/atlasclaw.sh"

# Create README
cat > "${BUILD_DIR}/README.txt" << 'README_EOF'
AtlasClaw Offline Package
=========================

This is a self-contained package with all dependencies pre-installed.

Quick Start:
1. Unzip this package on your Linux server
2. Run: ./atlasclaw.sh
3. Open http://your-server-ip:8000 in browser

Optional - Custom Port:
  ./atlasclaw.sh 8080

Configuration:
- Edit atlasclaw.json to configure API keys and providers
- Providers are loaded from atlasclaw_providers/ directory

Requirements:
- Python 3.11+ (for initial build only, not needed at runtime)
- All dependencies are pre-installed in venv/

No internet connection required after deployment!
README_EOF

# Create zip package
echo ""
echo "Creating zip package..."
cd build
zip -r "../${OUTPUT_ZIP}" "${PACKAGE_NAME}/"
cd ..

# Show results
echo ""
echo "========================================"
echo "Package build complete!"
echo "========================================"
echo ""
echo "Output: ${OUTPUT_ZIP}"
echo "Size: $(du -h "${OUTPUT_ZIP}" | cut -f1)"
echo ""
echo "Package contents:"
echo "  - atlasclaw/        : Core application"
echo "  - atlasclaw_providers/: Provider integrations"
echo "  - venv/             : Pre-installed Python dependencies"
echo "  - atlasclaw.sh      : Launcher script"
echo "  - atlasclaw.json    : Configuration file"
echo ""
echo "Deployment:"
echo "  1. Copy ${OUTPUT_ZIP} to target server"
echo "  2. Unzip: unzip ${OUTPUT_ZIP}"
echo "  3. Run: cd ${PACKAGE_NAME} && ./atlasclaw.sh"
echo ""
