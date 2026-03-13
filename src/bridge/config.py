"""Configuration management for Voice-OpenClaw Bridge.

Supports:
- YAML configuration files
- Environment variables
- .env file loading
- Hot-reload via file watching
- Strict validation with Pydantic
"""

from __future__ import annotations

import os
import sys
import threading
from pathlib import Path
from typing import Literal, Optional

from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
import structlog
import yaml
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

logger = structlog.get_logger()

# Configuration paths
DEFAULT_CONFIG_DIR = Path.home() / ".voice-bridge"
DEFAULT_CONFIG_FILE = DEFAULT_CONFIG_DIR / "config.yaml"
DEFAULT_ENV_FILE = DEFAULT_CONFIG_DIR / ".env"

# Global singleton
_config: Optional[AppConfig] = None
_config_lock = threading.Lock()


class AudioConfig(BaseModel):
    """Audio device configuration."""
    
    input_device: str | int = Field(default="default", description="Input device name or index")
    output_device: str | int = Field(default="default", description="Output device name or index")
    sample_rate: int = Field(default=16000, ge=8000, le=192000)
    channels: int = Field(default=1, ge=1, le=2)
    chunk_size: int = Field(default=1024, ge=256, le=8192)
    wake_word_frame_size: int = Field(
        default=1280,
        ge=256,
        le=8192,
        description="Frame size in samples for wake word detection (default 1280 = 80ms at 16kHz)"
    )
    
    @field_validator("input_device", "output_device")
    @classmethod
    def validate_device(cls, v: str | int) -> str | int:
        """Validate device is string name or integer index."""
        if isinstance(v, int) and v < -1:
            raise ValueError(f"Device index must be >= -1, got {v}")
        return v


class STTConfig(BaseModel):
    """Speech-to-Text configuration."""
    
    model: str = Field(default="base", description="Whisper model size")
    language: str | None = Field(default=None, description="Language code (auto-detect if None)")
    device: Literal["cpu", "cuda", "auto"] = Field(default="auto")
    compute_type: Literal["int8", "float16", "float32"] = Field(default="int8")
    beam_size: int = Field(default=5, ge=1, le=10, description="Beam size for decoding")
    vad_filter: bool = Field(default=False, description="Enable Whisper's VAD filtering (disabled - we use WebRTC VAD)")
    vad_threshold: float = Field(default=0.5, ge=0.0, le=1.0, description="VAD threshold")
    
    @field_validator("model")
    @classmethod
    def validate_model(cls, v: str) -> str:
        """Validate Whisper model size."""
        valid = {"tiny", "base", "small", "medium", "large", "large-v2", "large-v3"}
        if v not in valid:
            raise ValueError(f"Invalid model '{v}'. Must be one of: {valid}")
        return v


class TTSConfig(BaseModel):
    """Text-to-Speech configuration."""
    
    voice: str = Field(default="en_US-lessac-medium", description="Piper voice model")
    speed: float = Field(default=1.0, ge=0.5, le=2.0)
    volume: float = Field(default=1.0, ge=0.0, le=2.0)


class OpenClawConfig(BaseModel):
    """OpenClaw connection configuration."""
    
    host: str = Field(default="localhost", description="OpenClaw host")
    port: int = Field(default=18789, ge=1, le=65535)
    secure: bool = Field(default=False, description="Use WSS/HTTPS")
    api_key: str | None = Field(default=None, description="API key if required")
    timeout: float = Field(default=30.0, ge=1.0, le=300.0)
    timeout_ms: int = Field(
        default=30000,
        ge=1000,
        le=300000,
        description="Request timeout in milliseconds for HTTP API calls"
    )

    # HTTP API mode configuration
    api_mode: Literal["http", "websocket"] = Field(
        default="http",
        description="Communication mode: 'http' for REST API, 'websocket' for WebSocket"
    )
    ws_path: str = Field(
        default="/api/voice",
        description="WebSocket endpoint path on the OpenClaw Gateway"
    )
    auth_token: str | None = Field(
        default=None,
        description="Bearer token for authentication (set OPENCLAW_GATEWAY_TOKEN env var)"
    )

    def get_auth_token(self) -> str | None:
        """Get auth token from config or environment variable."""
        if self.auth_token:
            return self.auth_token
        import os
        return os.environ.get("OPENCLAW_GATEWAY_TOKEN")
    
    @field_validator("host")
    @classmethod
    def validate_host(cls, v: str) -> str:
        """Validate host is not empty."""
        if not v or not v.strip():
            raise ValueError("Host cannot be empty")
        return v.strip()


class PersistenceConfig(BaseModel):
    """Session persistence configuration."""
    
    enabled: bool = Field(default=True, description="Enable session persistence")
    db_path: str | None = Field(default=None, description="Custom database path (None for default)")
    ttl_minutes: int = Field(default=30, ge=1, le=1440, description="Session timeout in minutes")
    max_history: int = Field(default=10, ge=1, le=100, description="Max conversation turns to persist")
    cleanup_interval: int = Field(default=60, ge=10, le=3600, description="Seconds between cleanup runs")


class WakeAcknowledgementConfig(BaseModel):
    """Wake word acknowledgement configuration.

    When enabled, sends a wake word notification to OpenClaw after detection.
    OpenClaw is solely responsible for responding with a voice_response.
    If OpenClaw does not respond within timeout, the bridge proceeds silently
    to listening state — no local fallback phrases are ever generated.
    """

    enabled: bool = Field(default=True, description="Enable wake word acknowledgement")
    timeout_ms: int = Field(default=5000, ge=1000, le=10000, description="Timeout waiting for OpenClaw acknowledgement response")


class WakeWordConfig(BaseModel):
    """Wake word detection configuration."""
    wake_word: str = Field(default="computer", description="Wake word phrase to detect")
    backend: str = Field(default="stt", description="Backend: 'openwakeword' (fast) or 'stt' (reliable)")
    openwakeword_model: str = Field(default="hey_mycroft", description="OpenWakeWord model name")
    openwakeword_threshold: float = Field(default=0.15, ge=0.0, le=1.0, description="Detection threshold")
    refractory_seconds: float = Field(default=2.0, ge=0.0, le=10.0, description="Cooldown period after detection")


class InteractiveConfig(BaseModel):
    """Interactive conversation mode configuration.

    After the wake word is acknowledged, the bridge enters an interactive
    session where the user can speak and OpenClaw responds in a continuous
    loop without requiring the wake word again.

    The session ends when:
    - The user says one of the configured cancel phrases
    - No speech is detected for idle_timeout_seconds
    """

    enabled: bool = Field(
        default=True,
        description="Enter interactive mode after wake word acknowledgement"
    )
    idle_timeout_seconds: float = Field(
        default=30.0,
        ge=5.0,
        le=300.0,
        description="Exit interactive mode after this many seconds without user speech"
    )
    cancel_phrases: list[str] = Field(
        default=["stop", "cancel", "nevermind", "never mind", "exit", "goodbye", "bye"],
        description="Phrases that exit interactive mode when spoken by the user"
    )


class BridgeConfig(BaseModel):
    """Bridge behavior configuration."""

    response_timeout: float = Field(default=10.0, ge=1.0, le=60.0)
    max_session_duration: float = Field(default=300.0, ge=60.0, le=3600.0)
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = Field(default="INFO")
    hot_reload: bool = Field(default=True, description="Enable config file watching")

    # Wake word acknowledgement configuration
    acknowledgement: WakeAcknowledgementConfig = Field(
        default_factory=WakeAcknowledgementConfig,
        description="Wake word acknowledgement settings"
    )

    # Interactive conversation mode
    interactive: InteractiveConfig = Field(
        default_factory=InteractiveConfig,
        description="Interactive conversation mode settings"
    )


class AppConfig(BaseSettings):
    """Main application configuration.
    
    Loads from:
    1. Environment variables (highest priority)
    2. .env file
    3. config.yaml file
    4. Default values (lowest priority)
    """
    
    model_config = SettingsConfigDict(
        env_file=DEFAULT_ENV_FILE,
        env_file_encoding="utf-8",
        env_nested_delimiter="__",
        validate_assignment=True,
        extra="forbid",  # Strict: fail on unknown fields
    )
    
    # Nested configuration sections
    audio: AudioConfig = Field(default_factory=AudioConfig)
    stt: STTConfig = Field(default_factory=STTConfig)
    tts: TTSConfig = Field(default_factory=TTSConfig)
    wake_word: WakeWordConfig = Field(default_factory=WakeWordConfig)
    openclaw: OpenClawConfig = Field(default_factory=OpenClawConfig)
    bridge: BridgeConfig = Field(default_factory=BridgeConfig)
    persistence: PersistenceConfig = Field(default_factory=PersistenceConfig)
    
    # Internal
    _config_file: Path | None = None
    _watcher: Observer | None = None
    _on_reload: list[callable] = []
    
    @model_validator(mode="after")
    def apply_env_vars(self) -> "AppConfig":
        """Apply environment variable overrides for OpenClaw config.
        
        Environment variables (take precedence over config file):
        - OPENCLAW_GATEWAY_TOKEN: Bearer token for authentication
        - OPENCLAW_HOST: OpenClaw host address
        - OPENCLAW_PORT: OpenClaw port number
        
        Also auto-discovers from multiple locations, validating each token.
        """
        # Try to discover and validate token from multiple locations
        token = self._discover_openclaw_token()
        if token:
            self.openclaw.auth_token = token
        
        # Check for OPENCLAW_HOST
        if host := os.getenv("OPENCLAW_HOST"):
            self.openclaw.host = host.strip()
        
        # Check for OPENCLAW_PORT
        if port_str := os.getenv("OPENCLAW_PORT"):
            try:
                self.openclaw.port = int(port_str)
            except ValueError:
                logger.warning(
                    "Invalid OPENCLAW_PORT value, ignoring",
                    value=port_str
                )
        
        return self
    
    def _discover_openclaw_token(self) -> str | None:
        """Discover OpenClaw auth token from multiple locations.
        
        Searches in order:
        1. OPENCLAW_GATEWAY_TOKEN environment variable
        2. OPENCLAW_TOKEN environment variable (alternate)
        3. Voice Bridge config (~/.voice-bridge/config.yaml)
        4. OpenClaw config (~/.openclaw/openclaw.json)
        5. Gateway token file (~/.openclaw/gateway_token)
        6. Hidden token file (~/.openclaw/.token)
        7. OpenClaw YAML config (~/.openclaw/config.yaml)
        8. Workspace config (~/.openclaw/workspace/openclaw.yaml)
        
        Each token is validated by making a test request to OpenClaw.
        Returns the first valid token found.
        """
        import json
        import requests
        
        # Token source locations (name, getter function)
        token_sources = [
            ("OPENCLAW_GATEWAY_TOKEN env", lambda: os.environ.get("OPENCLAW_GATEWAY_TOKEN")),
            ("OPENCLAW_TOKEN env", lambda: os.environ.get("OPENCLAW_TOKEN")),
            ("Voice Bridge config", lambda: self._get_token_from_yaml(Path.home() / ".voice-bridge" / "config.yaml")),
            ("OpenClaw JSON config", lambda: self._get_token_from_openclaw_json()),
            ("Gateway token file", lambda: self._get_token_from_file(Path.home() / ".openclaw" / "gateway_token")),
            ("Hidden token file", lambda: self._get_token_from_file(Path.home() / ".openclaw" / ".token")),
            ("OpenClaw YAML config", lambda: self._get_token_from_yaml(Path.home() / ".openclaw" / "config.yaml")),
            ("Workspace config", lambda: self._get_token_from_yaml(Path.home() / ".openclaw" / "workspace" / "openclaw.yaml")),
        ]
        
        # Build OpenClaw URL for validation
        openclaw_url = f"http://{self.openclaw.host}:{self.openclaw.port}"
        
        for source_name, getter in token_sources:
            try:
                token = getter()
                if not token:
                    continue
                
                logger.debug("token_source_checking", source=source_name, token_preview=f"{token[:8]}..." if len(token) > 8 else token)
                
                # Validate token by making test request
                if self._validate_token(openclaw_url, token):
                    logger.info("token_validated", source=source_name, token_preview=f"{token[:8]}..." if len(token) > 8 else token)
                    return token
                else:
                    logger.warning("token_invalid", source=source_name)
                    
            except Exception as e:
                logger.debug("token_source_failed", source=source_name, error=str(e))
                continue
        
        logger.warning("no_valid_token_found", searched=[s[0] for s in token_sources])
        return None
    
    def _get_token_from_openclaw_json(self) -> str | None:
        """Extract token from OpenClaw JSON config."""
        import json
        from pathlib import Path
        
        config_path = Path.home() / ".openclaw" / "openclaw.json"
        if not config_path.exists():
            return None
        
        with open(config_path) as f:
            config = json.load(f)
            gateway_auth = config.get("gateway", {}).get("auth", {})
            if gateway_auth.get("mode") == "token":
                return gateway_auth.get("token")
        return None
    
    def _get_token_from_yaml(self, path: Path) -> str | None:
        """Extract token from YAML config file."""
        if not path.exists():
            return None
        
        try:
            with open(path) as f:
                config = yaml.safe_load(f)
                if not config:
                    return None
                # Check various token locations in YAML
                return (
                    config.get("openclaw", {}).get("auth_token") or
                    config.get("openclaw", {}).get("auth", {}).get("token") or
                    config.get("gateway", {}).get("auth_token") or
                    config.get("gateway", {}).get("auth", {}).get("token") or
                    config.get("auth_token")
                )
        except Exception:
            return None
    
    def _get_token_from_file(self, path: Path) -> str | None:
        """Read token from plain text file."""
        if not path.exists():
            return None
        
        try:
            with open(path) as f:
                return f.read().strip()
        except Exception:
            return None
    
    def _validate_token(self, openclaw_url: str, token: str) -> bool:
        """Validate token by making test request to OpenClaw.
        
        Returns True if token is valid (200 or 401 with valid response), False otherwise.
        """
        import requests
        
        try:
            # Try a simple models endpoint
            headers = {"Authorization": f"Bearer {token}"}
            response = requests.get(
                f"{openclaw_url}/v1/models",
                headers=headers,
                timeout=5
            )
            
            # 200 = valid token
            if response.status_code == 200:
                return True
            
            # 401 = invalid token
            if response.status_code == 401:
                return False
            
            # Other status codes might indicate OpenClaw is running but not accepting
            # this endpoint - still consider token potentially valid
            return response.status_code < 500
            
        except requests.exceptions.RequestException:
            # OpenClaw might not be running, can't validate
            # Return True to allow offline usage
            return True
    
    @classmethod
    def load(cls, config_path: Path | str | None = None) -> AppConfig:
        """Load configuration from file.
        
        Args:
            config_path: Path to config file. If None, uses default location.
            
        Returns:
            Loaded AppConfig instance
            
        Raises:
            FileNotFoundError: If config file doesn't exist and create_default=False
            ValidationError: If config is invalid (strict mode)
        """
        if config_path is None:
            config_path = DEFAULT_CONFIG_FILE
        else:
            config_path = Path(config_path)
        
        # Ensure config directory exists
        config_path.parent.mkdir(parents=True, exist_ok=True)
        
        # Load from YAML if exists, otherwise use defaults
        if config_path.exists():
            logger.info("Loading configuration", config_file=str(config_path))
            with open(config_path) as f:
                yaml_data = yaml.safe_load(f) or {}
            
            # Create instance with YAML data
            instance = cls(**yaml_data)
        else:
            logger.info("No config file found, using defaults", config_file=str(config_path))
            instance = cls()
            # Create default config file
            instance.save(config_path)
        
        instance._config_file = config_path
        return instance
    
    def save(self, path: Path | str | None = None) -> None:
        """Save configuration to YAML file."""
        if path is None:
            path = self._config_file or DEFAULT_CONFIG_FILE
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        
        # Convert to dict and save
        config_dict = self.model_dump(exclude={"_config_file", "_watcher", "_on_reload"})
        with open(path, "w") as f:
            yaml.dump(config_dict, f, default_flow_style=False, sort_keys=False)
        
        logger.info("Configuration saved", config_file=str(path))
    
    def start_hot_reload(self) -> None:
        """Start watching config file for changes."""
        if not self.bridge.hot_reload or self._watcher is not None:
            return
        
        config_file = self._config_file or DEFAULT_CONFIG_FILE
        if not config_file.exists():
            logger.warning("Cannot watch config file - does not exist", config_file=str(config_file))
            return
        
        logger.info("Starting config hot-reload watcher", config_file=str(config_file))
        
        class ConfigReloadHandler(FileSystemEventHandler):
            def __init__(self, config: AppConfig):
                self.config = config
                self._debounce_timer: Optional[threading.Timer] = None

            def on_modified(self, event):
                if event.src_path == str(config_file):
                    # Cancel any pending reload before scheduling a new one so
                    # rapid successive saves don't trigger multiple concurrent
                    # reloads (timer leak fix).
                    if self._debounce_timer is not None:
                        self._debounce_timer.cancel()
                        self._debounce_timer = None

                    def reload():
                        try:
                            logger.info("Config file changed, reloading...")
                            new_config = AppConfig.load(config_file)
                            # Copy over callbacks
                            new_config._on_reload = self.config._on_reload
                            # Update this instance's data
                            for key, value in new_config.model_dump().items():
                                setattr(self.config, key, value)
                            # Notify callbacks
                            for callback in self.config._on_reload:
                                try:
                                    callback()
                                except Exception as e:
                                    logger.error("Config reload callback failed", error=str(e))
                            logger.info("Config reloaded successfully")
                        except Exception as e:
                            logger.error("Config reload failed", error=str(e))
                        finally:
                            self._debounce_timer = None

                    self._debounce_timer = threading.Timer(0.5, reload)
                    self._debounce_timer.start()
        
        handler = ConfigReloadHandler(self)
        self._watcher = Observer()
        self._watcher.schedule(handler, path=str(config_file.parent), recursive=False)
        self._watcher.start()
    
    def stop_hot_reload(self) -> None:
        """Stop watching config file."""
        if self._watcher:
            logger.info("Stopping config hot-reload watcher")
            self._watcher.stop()
            self._watcher.join()
            self._watcher = None
    
    def on_reload(self, callback: callable) -> None:
        """Register a callback to be called when config is reloaded."""
        self._on_reload.append(callback)
    
    def remove_reload_callback(self, callback: callable) -> bool:
        """Remove a reload callback.
        
        Args:
            callback: The callback to remove
            
        Returns:
            True if callback was found and removed, False otherwise
        """
        if callback in self._on_reload:
            self._on_reload.remove(callback)
            return True
        return False
    
    def clear_reload_callbacks(self) -> int:
        """Clear all reload callbacks.
        
        Returns:
            Number of callbacks removed
        """
        count = len(self._on_reload)
        self._on_reload.clear()
        return count


def get_config() -> AppConfig:
    """Get or create the global configuration instance.
    
    Returns:
        Singleton AppConfig instance
    """
    global _config
    if _config is None:
        with _config_lock:
            if _config is None:
                _config = AppConfig.load()
    return _config


def reload_config() -> AppConfig:
    """Force reload configuration from disk.
    
    Returns:
        New AppConfig instance (replaces singleton)
    """
    global _config
    with _config_lock:
        logger.info("Force reloading configuration")
        old_config = _config
        _config = AppConfig.load()
        
        # Transfer callbacks
        if old_config:
            _config._on_reload = old_config._on_reload
        
        return _config
