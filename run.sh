#!/bin/bash
set -e

# Navigate to project directory
cd "$(dirname "$0")"

echo "======================================================================="
echo "   🚀 ZOHO -> GOOGLE WORKSPACE MIGRATION AGENT LAUNCHER"
echo "======================================================================="

echo "🔄 [1/3] Fetching latest updates from GitHub..."
git pull origin main || echo "⚠️  Could not connect to git remote, running local version."

echo "📦 [2/3] Verifying Python dependencies..."
if ! python3 -c "import cryptography" &> /dev/null; then
    echo "⚙️  'cryptography' package missing. Installing now..."
    python3 -m pip install cryptography || python3 -m pip install cryptography --break-system-packages || pip3 install cryptography
fi

echo "🌐 [3/3] Launching Migration Web UI on http://localhost:8080 ..."
python3 main.py --ui --port 8080
