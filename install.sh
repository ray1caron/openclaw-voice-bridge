#!/bin/bash
# Voice Bridge Interactive Installer
# Automatically handles paths and starts in interactive mode

set -e

# Find script location
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

# If we're inside the project, use current directory
if [[ -f "$SCRIPT_DIR/src/installer/__init__.py" ]]; then
    cd "$SCRIPT_DIR"
elif [[ -f "$PROJECT_DIR/src/installer/__init__.py" ]]; then
    cd "$PROJECT_DIR"
else
    echo "Error: Cannot find installer module"
    echo "Please run this script from the voice-bridge-v4 directory"
    exit 1
fi

# Set Python path to include src directory
export PYTHONPATH="${PWD}/src:${PYTHONPATH:-}"

# Check Python version
PYTHON_VERSION=$(python3 --version 2>&1 | awk '{print $2}')
echo "Using Python $PYTHON_VERSION"
echo "Working directory: ${PWD}"
echo ""

# Install OpenWakeWord dependency
# Use --break-system-packages for PEP 668 compliance on Ubuntu/Debian
echo ""
echo "Installing OpenWakeWord..."
pip install --break-system-packages openwakeword 2>/dev/null || pip install openwakeword

# Pre-download the "hey_jarvis" model
echo ""
echo "Pre-downloading wake word model..."
python3 -c "from openwakeword import Model; Model(); print('Model downloaded')"

# Run the interactive installer
python3 -m installer "$@"