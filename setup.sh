#!/bin/bash
# Voice Bridge Setup Script
# Run this to install all dependencies and set up the environment

set -e

echo "============================================"
echo "Voice Bridge Setup - $(date)"
echo "============================================"

# Check Python version
PYTHON_VERSION=$(python3 --version 2>&1 | awk '{print $2}')
echo "✓ Python version: $PYTHON_VERSION"

# Stop any running bridge processes
echo ""
echo "Stopping any running bridge processes..."
pkill -f "bridge.main" 2>/dev/null || true
sleep 1

# Create virtual environment if it doesn't exist
if [ ! -d "$HOME/.voice-bridge/venv" ]; then
    echo ""
    echo "Creating virtual environment..."
    python3 -m venv "$HOME/.voice-bridge/venv"
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
    sudo apt-get update
    sudo apt-get install -y portaudio19-dev libportaudio2 libportaudio-dev libsndfile1-dev
fi

# Install Python dependencies
echo ""
echo "Installing Python dependencies..."
pip install -r "$(dirname "$0")/requirements.txt"

# Force reinstall webrtcvad to ensure it works
echo ""
echo "Reinstalling webrtcvad..."
pip uninstall -y webrtcvad 2>/dev/null || true
pip install webrtcvad --no-cache-dir

# Install the bridge package
echo ""
echo "Installing voice-bridge..."
cd /home/hal/.openclaw/workspace/voice-bridge-v3
pip install -e . --no-deps

# Create config directory
echo ""
echo "Creating config directory..."
mkdir -p "$HOME/.voice-bridge"
mkdir -p "$HOME/.local/share/voice-bridge"
mkdir -p "$HOME/.local/state/voice-bridge/logs"

# Copy config if it doesn't exist
if [ ! -f "$HOME/.voice-bridge/config.yaml" ]; then
    echo ""
    echo "Copying default config..."
    cp "$(dirname "$0")/config.yaml" "$HOME/.voice-bridge/config.yaml"
fi

echo ""
echo "============================================"
echo "Setup complete!"
echo "============================================"
echo ""
echo "Next steps:"
echo "  1. Run: ./test_audio.sh    # Test audio devices"
echo "  2. Run: ./run_bridge.sh    # Start the bridge"
echo ""