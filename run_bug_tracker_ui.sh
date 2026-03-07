#!/usr/bin/env python3
"""
Bug Tracker UI Startup Script

Launches the Bug Tracker terminal interface for viewing and managing
bug reports from the Voice Bridge database.

Usage:
    ./run_bug_tracker_ui.sh [options]

Or:
    python3 src/bug_tracker_ui.py [options]

Options:
    --db PATH       Path to bugs database (default: ~/.voice-bridge/bugs.db)
    --severity N    Filter by severity (critical, high, medium, low, info)
    --component C   Filter by component (audio_pipeline, wake_word, etc.)
    --status S      Filter by status (new, triaged, in_progress, fixed, closed)
    --limit N       Maximum bugs to display (default: 50)
    --bug ID        View specific bug by ID
    --stats         Show statistics only
    --export PATH   Export bugs to JSON or Markdown
    --watch         Watch mode - auto-refresh display
    --update-status STATUS   Update bug status (use with --bug ID)

Requirements:
    - Python 3.9+
    - rich library (pip install rich)

Author: Voice Bridge Team
"""

import os
import sys
import subprocess
from pathlib import Path

# Add src to path
SCRIPT_DIR = Path(__file__).parent
SRC_DIR = SCRIPT_DIR / "src"
sys.path.insert(0, str(SRC_DIR))

# Virtual environment activation (if exists)
VENV_DIR = SCRIPT_DIR / "venv"
if VENV_DIR.exists():
    activate_script = VENV_DIR / "bin" / "activate"
    if activate_script.exists():
        print(f"Activating virtual environment: {VENV_DIR}")
        # Source the activate script
        os.system(f"source {activate_script}")

def check_dependencies():
    """Check required dependencies."""
    missing = []
    
    try:
        import rich
        print("✓ rich installed")
    except ImportError:
        print("✗ rich not installed")
        print("  Install with: pip install rich")
        missing.append("rich")
    
    return missing

def main():
    """Main entry point."""
    print("=" * 60)
    print("🐛 Bug Tracker UI - Voice Bridge")
    print("=" * 60)
    print()
    
    # Check dependencies
    missing = check_dependencies()
    if missing:
        print()
        print("Missing dependencies. Install with:")
        print(f"  pip install {' '.join(missing)}")
        print()
        print("Continuing with limited functionality...")
        print()
    
    # Check if database exists
    db_path = Path.home() / ".voice-bridge" / "bugs.db"
    
    if not db_path.exists():
        print(f"⚠ Database not found: {db_path}")
        print()
        print("The database will be created automatically when Voice Bridge runs.")
        print("Start Voice Bridge first to initialize the bug tracking database.")
        print()
        print("Or specify a different database with: --db PATH")
        print()
    
    # Run the UI
    ui_script = SRC_DIR / "bug_tracker_ui.py"
    
    if not ui_script.exists():
        print(f"✗ UI script not found: {ui_script}")
        return 1
    
    # Pass through all arguments
    args = [sys.executable, str(ui_script)] + sys.argv[1:]
    
    try:
        return subprocess.run(args).returncode
    except KeyboardInterrupt:
        print("\n\n👋 Goodbye!")
        return 0

if __name__ == "__main__":
    sys.exit(main() or 0)