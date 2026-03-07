#!/usr/bin/env python3
"""Setup script for Voice-OpenClaw Bridge v2.

Runs on first start to:
1. Discover audio devices
2. Generate configuration file
3. Validate the setup
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Add src to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from bridge.audio_discovery import run_discovery, print_discovery_report
from bridge.config import AppConfig, DEFAULT_CONFIG_FILE, DEFAULT_CONFIG_DIR
import structlog

structlog.configure(
    processors=[
        structlog.stdlib.filter_by_level,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.stdlib.PositionalArgumentsFormatter(),
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        structlog.processors.UnicodeDecoder(),
        structlog.processors.JSONRenderer()
    ],
    context_class=dict,
    logger_factory=structlog.stdlib.LoggerFactory(),
    wrapper_class=structlog.stdlib.BoundLogger,
    cache_logger_on_first_use=True,
)

logger = structlog.get_logger()


def generate_config_with_discovery() -> AppConfig:
    """Generate configuration with audio device discovery."""
    logger.info("Starting audio device discovery...")
    
    # Run discovery
    discovery = run_discovery()
    print_discovery_report(discovery)
    
    # Get recommendations
    input_device = discovery.recommend_input()
    output_device = discovery.recommend_output()
    
    # Create config with discovered devices
    config = AppConfig()
    
    if input_device:
        # Use device name for resilience to index changes
        config.audio.input_device = input_device.name
        logger.info("Configured input device", device=input_device.name)
    else:
        logger.warning("No input device found, using default")
    
    if output_device:
        config.audio.output_device = output_device.name
        logger.info("Configured output device", device=output_device.name)
    else:
        logger.warning("No output device found, using default")
    
    return config


def setup_config_directory() -> None:
    """Create config directory if it doesn't exist."""
    DEFAULT_CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    logger.info("Config directory ready", path=str(DEFAULT_CONFIG_DIR))


def create_env_file() -> None:
    """Create .env file template if it doesn't exist."""
    env_file = DEFAULT_CONFIG_DIR / ".env"
    if env_file.exists():
        logger.info(".env file already exists", path=str(env_file))
        return
    
    template_path = Path(__file__).parent.parent / "config" / ".env.template"
    if template_path.exists():
        with open(template_path) as f:
            template = f.read()
    else:
        # Fallback template
        template = """# Voice-OpenClaw Bridge v2 Environment Variables
# OPENCLAW_API_KEY=your_api_key_here
"""
    
    with open(env_file, "w") as f:
        f.write(template)
    
    logger.info("Created .env file template", path=str(env_file))


def main():
    """Main setup entry point."""
    parser = argparse.ArgumentParser(
        description="Setup Voice-OpenClaw Bridge v2 configuration"
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite existing configuration",
    )
    parser.add_argument(
        "--skip-discovery",
        action="store_true",
        help="Skip audio device discovery",
    )
    args = parser.parse_args()
    
    print("\n" + "=" * 60)
    print("🎙️  Voice-OpenClaw Bridge v2 - Setup")
    print("=" * 60 + "\n")
    
    # Check if config already exists
    if DEFAULT_CONFIG_FILE.exists() and not args.force:
        print(f"Configuration already exists at: {DEFAULT_CONFIG_FILE}")
        print("Use --force to overwrite, or delete the file manually.")
        print("\nCurrent configuration:")
        with open(DEFAULT_CONFIG_FILE) as f:
            print(f.read())
        return 0
    
    # Setup config directory
    setup_config_directory()
    
    # Create .env template
    create_env_file()
    
    # Generate configuration
    if args.skip_discovery:
        print("Skipping audio discovery, using defaults...")
        config = AppConfig()
    else:
        config = generate_config_with_discovery()
    
    # Save configuration
    config.save()
    
    print(f"\n✅ Configuration saved to: {DEFAULT_CONFIG_FILE}")
    print("\nYou can:")
    print("  - Edit the config file directly")
    print("  - Set environment variables in ~/.voice-bridge/.env")
    print("  - Changes will be detected automatically (hot-reload enabled)")
    
    return 0


if __name__ == "__main__":
    sys.exit(main())
