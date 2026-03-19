#!/bin/bash
# AtlasClaw Launcher Script
# Usage: ./atlasclaw.sh [port]
# This script automatically sets up the environment and starts AtlasClaw

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Get script directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# Configuration
VENV_DIR="venv"
REQUIREMENTS_FILE="requirements.txt"
PORT="${1:-8000}"

echo -e "${GREEN}AtlasClaw Launcher${NC}"
echo "===================="

# Check Python version
echo -e "\n${YELLOW}Checking Python...${NC}"
if ! command -v python3 &> /dev/null; then
    echo -e "${RED}Error: Python 3 is not installed${NC}"
    exit 1
fi

PYTHON_VERSION=$(python3 --version 2>&1 | awk '{print $2}')
echo "Found Python $PYTHON_VERSION"

# Check if Python version is 3.11+
if ! python3 -c "import sys; exit(0 if sys.version_info >= (3, 11) else 1)" 2>/dev/null; then
    echo -e "${YELLOW}Warning: Python 3.11+ is recommended (found $PYTHON_VERSION)${NC}"
fi

# Create virtual environment if not exists
if [ ! -d "$VENV_DIR" ]; then
    echo -e "\n${YELLOW}Creating virtual environment...${NC}"
    python3 -m venv "$VENV_DIR"
    echo -e "${GREEN}Virtual environment created${NC}"
fi

# Activate virtual environment
echo -e "\n${YELLOW}Activating virtual environment...${NC}"
source "$VENV_DIR/bin/activate"

# Upgrade pip
echo -e "\n${YELLOW}Upgrading pip...${NC}"
pip install --upgrade pip -q

# Install requirements if not installed or if requirements.txt is newer
if [ ! -f "$VENV_DIR/.requirements_installed" ] || [ "$REQUIREMENTS_FILE" -nt "$VENV_DIR/.requirements_installed" ]; then
    echo -e "\n${YELLOW}Installing dependencies...${NC}"
    if [ -f "$REQUIREMENTS_FILE" ]; then
        pip install -r "$REQUIREMENTS_FILE"
        touch "$VENV_DIR/.requirements_installed"
        echo -e "${GREEN}Dependencies installed${NC}"
    else
        echo -e "${YELLOW}Warning: $REQUIREMENTS_FILE not found${NC}"
    fi
else
    echo -e "${GREEN}Dependencies already installed${NC}"
fi

# Check if atlasclaw.json exists
if [ ! -f "atlasclaw.json" ]; then
    echo -e "\n${YELLOW}Creating default atlasclaw.json...${NC}"
    cat > atlasclaw.json << 'EOF'
{
  "providers_root": "./atlasclaw_providers",
  "model": {
    "primary": "openai/gpt-4",
    "temperature": 0.7,
    "providers": {
      "openai": {
        "api_key": "${OPENAI_API_KEY}"
      }
    }
  }
}
EOF
    echo -e "${GREEN}Default configuration created${NC}"
    echo -e "${YELLOW}Please edit atlasclaw.json to configure your API keys${NC}"
fi

# Start the service
echo -e "\n${GREEN}Starting AtlasClaw on http://0.0.0.0:$PORT${NC}"
echo "Press Ctrl+C to stop"
echo ""

exec uvicorn atlasclaw.app.atlasclaw.main:app --host 0.0.0.0 --port "$PORT"
