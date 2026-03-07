#!/bin/bash
# Voice Bridge Setup Script - Enhanced with Installation UI
# Run this to install all dependencies and set up the environment

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORKSPACE="$(dirname "$SCRIPT_DIR")"

echo "============================================"
echo "Voice Bridge Setup - $(date)"
echo "============================================"

# Check Python version
PYTHON_VERSION=$(python3 --version 2>&1 | awk '{print $2}')
echo "✓ Python version: $PYTHON_VERSION"

# Check for previous installations
echo ""
echo "Checking for previous installations..."

# Check for running bridge processes
if pgrep -f "bridge.main" > /dev/null 2>&1; then
    echo "⚠️  Found running bridge process(es)"
    echo "   Stopping..."
    pkill -f "bridge.main" 2>/dev/null || true
    sleep 2
fi

# Check for virtual environments
VENV_PATHS=(
    "$HOME/.voice-bridge/venv"
    "$HOME/venv"
    "$WORKSPACE/venv"
)

for venv_path in "${VENV_PATHS[@]}"; do
    if [ -d "$venv_path" ]; then
        echo "   Found venv: $venv_path"
    fi
done

# Check for config files
CONFIG_PATHS=(
    "$HOME/.voice-bridge/config.yaml"
    "$HOME/.config/voice-bridge-v2/config.yaml"
)

for config_path in "${CONFIG_PATHS[@]}"; do
    if [ -f "$config_path" ]; then
        echo "   Found config: $config_path"
    fi
done

# Ask about cleanup if previous installation found
if [ -d "$HOME/.voice-bridge" ] || [ -d "$HOME/.config/voice-bridge-v2" ]; then
    echo ""
    echo "Previous installation detected."
    read -p "Remove previous installation? (y/N) " -n 1 -r
    echo
    if [[ $REPLY =~ ^[Yy]$ ]]; then
        echo "Removing previous installation..."
        rm -rf "$HOME/.voice-bridge" 2>/dev/null || true
        rm -rf "$HOME/.config/voice-bridge-v2" 2>/dev/null || true
        rm -rf "$HOME/.local/share/voice-bridge" 2>/dev/null || true
        rm -rf "$HOME/.local/state/voice-bridge" 2>/dev/null || true
        rm -rf "$HOME/.cache/voice-bridge" 2>/dev/null || true
        echo "✓ Previous installation removed"
    else
        echo "Keeping previous installation files"
    fi
fi

# Create virtual environment
echo ""
if [ ! -d "$HOME/.voice-bridge/venv" ]; then
    echo "Creating virtual environment..."
    python3 -m venv "$HOME/.voice-bridge/venv"
else
    echo "✓ Virtual environment exists"
fi

# Activate virtual environment
echo ""
echo "Activating virtual environment..."
source "$HOME/.voice-bridge/venv/bin/activate"

# Upgrade pip
echo ""
echo "Upgrading pip..."
pip install --upgrade pip setuptools wheel setuptools-scm

# Install system dependencies (Ubuntu/Debian)
if command -v apt-get &> /dev/null; then
    echo ""
    echo "Installing system dependencies..."
    
    # Check if we need sudo for apt
    if [ -w /var/lib/apt/lists ] || sudo -n true 2>/dev/null; then
        sudo apt-get update
        sudo apt-get install -y portaudio19-dev libportaudio2 libportaudio-dev libsndfile1-dev
    else
        echo "⚠️  System dependencies require sudo - skipping"
        echo "   Run manually: sudo apt-get install portaudio19-dev libsndfile1-dev"
    fi
fi

# Install Python dependencies
echo ""
echo "Installing Python dependencies..."
pip install -r "$SCRIPT_DIR/requirements.txt"

# Force reinstall webrtcvad to ensure it works
echo ""
echo "Reinstalling webrtcvad..."
pip uninstall -y webrtcvad 2>/dev/null || true
pip install webrtcvad --no-cache-dir

# Install the bridge package
echo ""
echo "Installing voice-bridge..."
cd "$WORKSPACE"
pip install -e . --no-deps

# Create directories
echo ""
echo "Creating directories..."
mkdir -p "$HOME/.voice-bridge"
mkdir -p "$HOME/.local/share/voice-bridge"
mkdir -p "$HOME/.local/state/voice-bridge/logs"

# Copy config if it doesn't exist
if [ ! -f "$HOME/.voice-bridge/config.yaml" ]; then
    echo ""
    echo "Creating default config..."
    if [ -f "$SCRIPT_DIR/config.yaml" ]; then
        cp "$SCRIPT_DIR/config.yaml" "$HOME/.voice-bridge/config.yaml"
    fi
fi

# Run Python installer for validation
echo ""
echo "Running installation validation..."
python3 -m installer --auto --skip-hardware 2>/dev/null || {
    echo "⚠️  Installer validation skipped (installer module not available)"
}

echo ""
echo "============================================"
echo "Setup complete!"
echo "============================================"
echo ""
echo "Next steps:"
echo "  1. Test audio:    python -m installer --test-audio"
echo "  2. Start bridge:   ./run_bridge.sh"
echo "  3. Or manually:    python -m bridge.main"
echo ""