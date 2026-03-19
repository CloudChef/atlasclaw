#!/bin/bash
# One-line package command for AtlasClaw
# Usage: ./package.sh

set -e

echo "Building AtlasClaw offline package..."

# Clean
rm -rf atlasclaw-dist atlasclaw.zip

# Create structure
mkdir -p atlasclaw-dist/atlasclaw/app
cp -r app/atlasclaw/* atlasclaw-dist/atlasclaw/app/
cp requirements.txt atlasclaw-dist/
cp atlasclaw.json atlasclaw-dist/

# Copy providers
if [ -d "../atlasclaw-providers/providers" ]; then
    cp -r ../atlasclaw-providers/providers atlasclaw-dist/atlasclaw_providers/
    echo "Providers: ../atlasclaw-providers"
elif [ -d "atlasclaw-providers/providers" ]; then
    cp -r atlasclaw-providers/providers atlasclaw-dist/atlasclaw_providers/
    echo "Providers: atlasclaw-providers/"
else
    mkdir -p atlasclaw-dist/atlasclaw_providers
    echo "Providers: none (empty)"
fi

# Install dependencies
echo "Installing dependencies..."
cd atlasclaw-dist
python3 -m venv venv
source venv/bin/activate
pip install -q --upgrade pip
pip install -q -r requirements.txt
touch venv/.installed
cd ..

# Create launcher
cat > atlasclaw-dist/atlasclaw.sh << 'EOF'
#!/bin/bash
cd "$(dirname "$0")"
source venv/bin/activate
exec uvicorn atlasclaw.app.atlasclaw.main:app --host 0.0.0.0 --port "${1:-8000}"
EOF
chmod +x atlasclaw-dist/atlasclaw.sh

# Create zip
zip -qr atlasclaw.zip atlasclaw-dist/

# Done
echo ""
echo "✓ Package created: atlasclaw.zip ($(du -h atlasclaw.zip | cut -f1))"
echo ""
echo "Usage:"
echo "  unzip atlasclaw.zip"
echo "  cd atlasclaw-dist"
echo "  ./atlasclaw.sh"
echo ""
