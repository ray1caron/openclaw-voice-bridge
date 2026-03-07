"""Main entry point for Voice-OpenClaw Bridge v4."""

from __future__ import annotations

import asyncio
import signal
import sys
from pathlib import Path

import structlog

from bridge.config import AppConfig, get_config, DEFAULT_CONFIG_FILE
from bridge.audio_discovery import run_discovery, print_discovery_report
from bridge.bug_tracker import BugTracker, install_global_handler


def setup_logging(log_level: str = "INFO") -> None:
    """Configure structured logging."""
    import logging
    
    # Set Python logging level (required for structlog's filter_by_level)
    level = getattr(logging, log_level.upper(), logging.INFO)
    logging.basicConfig(
        format="",
        level=level,
        stream=sys.stdout,
    )
    
    structlog.configure(
        processors=[
            structlog.stdlib.filter_by_level,
            structlog.stdlib.add_logger_name,
            structlog.stdlib.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.UnicodeDecoder(),
            structlog.dev.ConsoleRenderer(),
        ],
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )


def check_first_run() -> bool:
    """Check if this is the first run (no config exists)."""
    return not DEFAULT_CONFIG_FILE.exists()


def run_first_time_setup() -> AppConfig:
    """Run first-time setup with audio discovery."""
    print("\n" + "=" * 60)
    print("🎙️  Voice-OpenClaw Bridge v4 - First Time Setup")
    print("=" * 60 + "\n")
    
    # Run audio discovery
    print("🔍 Discovering audio devices...\n")
    discovery = run_discovery()
    print_discovery_report(discovery)
    
    # Get recommendations
    input_dev = discovery.recommend_input()
    output_dev = discovery.recommend_output()
    
    # Create config with discovered devices
    config = AppConfig()
    
    if input_dev:
        config.audio.input_device = input_dev.name
        logger = structlog.get_logger()
        logger.info("Configured input device", device=input_dev.name)
    
    if output_dev:
        config.audio.output_device = output_dev.name
        logger = structlog.get_logger()
        logger.info("Configured output device", device=output_dev.name)
    
    # Save configuration
    config.save()
    
    print(f"\n✅ Configuration saved to: {DEFAULT_CONFIG_FILE}")
    print("\nYou can:")
    print("  - Edit the config file directly")
    print("  - Set environment variables in ~/.voice-bridge/.env")
    print("  - Changes will be detected automatically (hot-reload enabled)")
    
    return config


async def main():
    """Main entry point for the voice bridge."""
    setup_logging()
    logger = structlog.get_logger()
    
    logger.info("Starting Voice-OpenClaw Bridge v4", version="0.2.0")
    
    # Initialize bug tracker for error capture
    try:
        bug_tracker = BugTracker.get_instance()
        install_global_handler(bug_tracker)
        logger.info("Bug tracker initialized", db_path=str(bug_tracker.db_path))
    except Exception as e:
        logger.warning("Failed to initialize bug tracker, continuing without error tracking", error=str(e))
    
    # Check for first run
    if check_first_run():
        logger.info("First run detected - running setup")
        config = run_first_time_setup()
    else:
        # Load existing configuration
        try:
            config = get_config()
            logger.info(
                "Configuration loaded",
                config_file=str(DEFAULT_CONFIG_FILE),
                hot_reload=config.bridge.hot_reload,
            )
        except Exception as e:
            logger.error("Failed to load configuration", error=str(e))
            print(f"\n❌ Configuration error: {e}")
            print("\nYou can:")
            print(f"  - Fix the config file: {DEFAULT_CONFIG_FILE}")
            print("  - Delete it to re-run first-time setup")
            sys.exit(1)
    
    # Set log level from config
    setup_logging(config.bridge.log_level)
    
    # Setup signal handlers for graceful shutdown
    loop = asyncio.get_event_loop()
    shutdown_event = asyncio.Event()
    
    def signal_handler(sig):
        logger.info(f"Received signal {sig.name}, shutting down...")
        shutdown_event.set()
    
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, lambda s=sig: signal_handler(s))
    
    # Import and initialize the orchestrator and WebSocket server
    try:
        from bridge.orchestrator import VoiceOrchestrator, OrchestratorState
        from bridge.websocket_server import WebSocketServer
        
        logger.info("Initializing voice orchestrator")
        
        orchestrator = VoiceOrchestrator(config=config)
        
        # Initialize WebSocket server for OpenClaw Gateway communication
        try:
            ws_server = WebSocketServer.get_instance(
                host="0.0.0.0",
                port=18790,
                max_connections=10
            )
            logger.info("WebSocket server initialized")
        except Exception as e:
            logger.warning("Failed to initialize WebSocket server", error=str(e))
            ws_server = None
        
        # Set up callbacks
        def on_wake_word(detected: str):
            logger.info("Wake word detected", wake_word=detected)
        
        def on_speech_end(text: str, confidence: float):
            logger.info("Speech recognized", text=text, confidence=f"{confidence:.2f}")
        
        def on_response(text: str):
            logger.info("Response received", text=text[:100] + "..." if len(text) > 100 else text)
        
        def on_state_change(old_state: OrchestratorState, new_state: OrchestratorState):
            logger.debug("State changed", old=old_state.value, new=new_state.value)
        
        def on_error(error: Exception):
            logger.error("Orchestrator error", error=str(error))
        
        orchestrator.on_wake_word = on_wake_word
        orchestrator.on_speech_end = on_speech_end
        orchestrator.on_response = on_response
        orchestrator.on_state_change = on_state_change
        orchestrator.on_error = on_error
        
        # Register WebSocket server callback for OpenClaw responses
        if ws_server:
            ws_server.on_response(on_response)
            logger.info("WebSocket response callback registered")
        
        # Start the orchestrator
        logger.info(
            "Starting voice loop",
            wake_word=config.wake_word.wake_word,
            backend=config.wake_word.backend,
            stt_model=config.stt.model,
            tts_voice=config.tts.voice,
        )
        
        # orchestrator.start() returns bool, not awaitable
        if not orchestrator.start():
            logger.error("Failed to start orchestrator")
            print("\n❌ Failed to start voice bridge")
            sys.exit(1)
        
        # Start WebSocket server
        if ws_server:
            try:
                await ws_server.start()
                logger.info(
                    "WebSocket server started",
                    host=ws_server.host,
                    port=ws_server.port
                )
                print(f"   WebSocket server: ws://{ws_server.host}:{ws_server.port}")
            except Exception as e:
                logger.error("Failed to start WebSocket server", error=str(e))
                print("   WebSocket server: FAILED to start")
        
        logger.info("Voice bridge started - say '%s' to begin", config.wake_word.wake_word)
        print(f"\n🎙️  Voice Bridge is running!")
        print(f"   Wake word: '{config.wake_word.wake_word}'")
        print(f"   Backend: {config.wake_word.backend}")
        if ws_server:
            print(f"   WebSocket server: ws://{ws_server.host}:{ws_server.port}")
        else:
            print("   WebSocket server: Not available")
        print(f"   Say '{config.wake_word.wake_word}' to start a conversation.")
        print("   Press Ctrl+C to stop.\n")
        
        # Wait for shutdown signal
        await shutdown_event.wait()
        
        # Stop the WebSocket server
        if ws_server:
            try:
                await ws_server.stop()
                logger.info("WebSocket server stopped")
            except Exception as e:
                logger.error("Error stopping WebSocket server", error=str(e))
        
        # Stop the orchestrator
        logger.info("Stopping voice orchestrator")
        orchestrator.stop()  # Not async
        
        # Print stats
        stats = orchestrator.get_stats()
        logger.info(
            "Voice bridge stopped",
            wake_word_detections=stats.wake_word_detections,
            transcriptions=stats.completet_transcriptions,
            responses=stats.completet_responses,
            barge_ins=stats.barge_in_count,
            uptime_seconds=stats.uptime_seconds,
        )
        
    except ImportError as e:
        logger.warning("Orchestrator not available, running in stub mode", error=str(e))
        logger.info("Note: Install 'faster-whisper' and 'piper-tts' for full functionality")
        
        print("\n⚠️  Voice Bridge running in stub mode (no voice processing)")
        print("   Install missing dependencies:")
        print("     pip install faster-whisper piper-tts")
        print("\n   Press Ctrl+C to stop.\n")
        
        # Wait for shutdown in stub mode
        await shutdown_event.wait()
    
    except Exception as e:
        logger.error("Failed to start voice bridge", error=str(e), exc_info=True)
        print(f"\n❌ Error: {e}")
        sys.exit(1)
    
    finally:
        # Stop hot-reload watcher
        config.stop_hot_reload()
        
        logger.info("Bridge shutdown complete")


if __name__ == "__main__":
    asyncio.run(main())