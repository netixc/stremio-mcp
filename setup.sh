#!/bin/bash
# Setup script for Stremio MCP Server

set -e

echo "======================================"
echo "Stremio MCP Server Setup"
echo "======================================"
echo ""

# Check if uv is installed
if ! command -v uv &> /dev/null; then
    echo "uv is not installed. Installing uv..."
    echo ""
    curl -LsSf https://astral.sh/uv/install.sh | sh
    echo ""
    echo "Please restart your terminal and run this script again."
    echo "Or run: source $HOME/.cargo/env"
    exit 0
fi

echo "Found uv $(uv --version)"

# Check Python version
if ! command -v python3 &> /dev/null; then
    echo "Error: Python 3 is not installed"
    echo "Please install Python 3.10 or higher"
    exit 1
fi

PYTHON_VERSION=$(python3 -c 'import sys; print(".".join(map(str, sys.version_info[:2])))')
echo "Found Python $PYTHON_VERSION"

echo ""
echo "Step 1: Installing Python dependencies with uv..."
uv sync

echo ""
echo "Step 2: Setting up environment file..."
if [ ! -f .env ]; then
    cp .env.example .env
    echo "Created .env file from .env.example"
    echo ""
    echo "IMPORTANT: Please edit .env and add:"
    echo "  1. Your TMDB API key (get from https://www.themoviedb.org/settings/api)"
    echo "  2. Your Android TV IP address"
else
    echo ".env file already exists, skipping..."
fi

echo ""
echo "Step 3: Checking ADB installation..."
if command -v adb &> /dev/null; then
    echo "ADB is already installed"
    ADB_VERSION=$(adb version | head -n 1)
    echo "$ADB_VERSION"
else
    echo "ADB not found in PATH"
    echo ""
    echo "To install ADB:"
    echo "  macOS: brew install android-platform-tools"
    echo "  Linux: sudo apt-get install adb"
    echo "  Windows: Download from https://developer.android.com/studio/releases/platform-tools"
fi

echo ""
echo "======================================"
echo "Setup Complete!"
echo "======================================"
echo ""
echo "Next steps:"
echo ""
echo "1. Edit .env file with your configuration:"
echo "   nano .env"
echo ""
echo "2. Enable ADB debugging on your Android TV:"
echo "   Settings > Device Preferences > About > Build (tap 7 times)"
echo "   Settings > Device Preferences > Developer Options > Enable USB & Wireless/Network Debugging"
echo ""
echo "3. Pair and connect to your Android TV (modern Wireless Debugging):"
echo "   adb pair YOUR_TV_IP:PAIRING_PORT"
echo "   adb connect YOUR_TV_IP:CONNECTION_PORT"
echo ""
echo "4. Add this server to Claude Desktop config:"
echo "   File location:"
if [[ "$OSTYPE" == "darwin"* ]]; then
    echo "   ~/Library/Application Support/Claude/claude_desktop_config.json"
elif [[ "$OSTYPE" == "msys" || "$OSTYPE" == "win32" ]]; then
    echo "   %APPDATA%\\Claude\\claude_desktop_config.json"
else
    echo "   ~/.config/Claude/claude_desktop_config.json"
fi
echo ""
echo "   Add this configuration:"
echo '   {
     "mcpServers": {
       "stremio": {
         "command": "uv",
         "args": ["--directory", "'$(pwd)'", "run", "src/stremio_mcp.py"],
         "env": {
           "TMDB_API_KEY": "your_api_key",
           "ANDROID_TV_HOST": "your_tv_ip",
           "ANDROID_TV_PORT": "your_connection_port"
         }
       }
     }
   }'
echo ""
echo "5. Restart Claude Desktop"
echo ""
echo "For more information, see README.md"
