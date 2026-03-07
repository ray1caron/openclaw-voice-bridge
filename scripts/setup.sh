#!/bin/bash
# Initial project setup script for voice-openclaw-bridge-v2
# Run this after cloning the GitHub repository

set -e

echo "🎙️ Voice-OpenClaw-Bridge v2 Setup"
echo "===================================="

# Check Python version
PYTHON_VERSION=$(python3 --version 2>&1 | awk '{print $2}')
echo "Python version: $PYTHON_VERSION"

# Create virtual environment
if [ ! -d "venv" ]; then
    echo "Creating virtual environment..."
    python3 -m venv venv
fi

# Activate environment
source venv/bin/activate

# Upgrade pip
echo "Upgrading pip..."
pip install --upgrade pip

# Install dependencies
echo "Installing dependencies..."
pip install -e .

# Create config directory
mkdir -p ~/.config/voice-bridge-v2

# Copy default config if not exists
if [ ! -f ~/.config/voice-bridge-v2/config.yaml ]; then
    echo "Creating default configuration..."
    cp config/default.yaml ~/.config/voice-bridge-v2/config.yaml
    echo "⚠️  Please edit ~/.config/voice-bridge-v2/config.yaml with your settings"
fi

# Download Piper if not exists
if [ ! -d "piper" ]; then
    echo "Downloading Piper TTS..."
    mkdir -p piper
    cd piper
    wget -q https://github.com/rhasspy/piper/releases/download/2023.11.14-2/piper_amd64.tar.gz
    tar -xzf piper_amd64.tar.gz
    rm piper_amd64.tar.gz
    
    # Download default voice
    mkdir -p voices
    cd voices
    echo "Downloading default voice (Amy)..."
    wget -q https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/en/en_US/amy/medium/en_US-amy-medium.onnx
    wget -q https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/en/en_US/amy/medium/en_US-amy-medium.onnx.json
    cd ../..
fi

echo ""
echo "✅ Setup complete!"
echo ""
echo "Next steps:"
echo "1. Edit ~/.config/voice-bridge-v2/config.yaml"
echo "2. Test audio: python scripts/test-audio.py"
echo "3. Run bridge: python -m src.bridge.main"
echo ""

# Deactivate
deactivate
