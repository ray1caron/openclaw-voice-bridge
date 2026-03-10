"""Configuration Summary for Installation UI.

Displays and validates configuration settings during installation,
showing the user what will be configured.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Any

import structlog

logger = structlog.get_logger()


@dataclass
class ConfigIssue:
    """A configuration issue found during validation."""
    field: str
    severity: str  # "error", "warning", "info"
    message: str
    suggestion: Optional[str] = None
    
    @property
    def is_error(self) -> bool:
        return self.severity == "error"
    
    @property
    def is_warning(self) -> bool:
        return self.severity == "warning"
    
    def __str__(self) -> str:
        icon = {"error": "❌", "warning": "⚠️", "info": "ℹ️"}.get(self.severity, "?")
        text = f"{icon} {self.field}: {self.message}"
        if self.suggestion:
            text += f"\n    💡 {self.suggestion}"
        return text


@dataclass
class ConfigSection:
    """A section of configuration."""
    name: str
    display_name: str
    icon: str
    fields: Dict[str, Any] = field(default_factory=dict)
    issues: List[ConfigIssue] = field(default_factory=list)
    
    @property
    def has_errors(self) -> bool:
        return any(i.is_error for i in self.issues)
    
    @property
    def has_warnings(self) -> bool:
        return any(i.is_warning for i in self.issues)
    
    def summary_line(self) -> str:
        """Generate a one-line summary."""
        status = "✅" if not self.issues else ("⚠️" if not self.has_errors else "❌")
        return f"{self.icon} {self.display_name}: {status}"


@dataclass
class ConfigReport:
    """Full configuration report."""
    sections: List[ConfigSection] = field(default_factory=list)
    issues: List[ConfigIssue] = field(default_factory=list)
    config_path: Optional[Path] = None
    valid: bool = True
    
    @property
    def has_errors(self) -> bool:
        return any(i.is_error for i in self.issues)
    
    @property
    def has_warnings(self) -> bool:
        return any(i.is_warning for i in self.issues)
    
    def summary(self) -> str:
        """Generate a summary string."""
        lines = []
        
        # Overall status
        if not self.issues:
            lines.append("✅ Configuration is valid and ready")
        elif self.has_errors:
            lines.append("❌ Configuration has errors that must be fixed")
        else:
            lines.append("⚠️ Configuration has warnings")
        
        # Section summaries
        lines.append("")
        lines.append("Sections:")
        for section in self.sections:
            lines.append(f"  {section.summary_line()}")
        
        # Issues
        if self.issues:
            lines.append("")
            lines.append("Issues:")
            for issue in self.issues:
                lines.append(f"  {issue}")
        
        return "\n".join(lines)


class ConfigSummary:
    """Generates configuration summary for installation display."""
    
    # Known config paths
    DEFAULT_CONFIG_PATHS = [
        Path.home() / ".voice-bridge" / "config.yaml",
        Path.home() / ".config" / "voice-bridge-v2" / "config.yaml",
        Path.home() / ".config" / "voice-bridge" / "config.yaml",
    ]
    
    def __init__(self, config_path: Optional[Path] = None):
        """Initialize config summary.
        
        Args:
            config_path: Optional explicit config path
        """
        self.config_path = config_path
        self.logger = structlog.get_logger()
        self._config = None
        self._config_loaded = False
    
    def _find_config(self) -> Optional[Path]:
        """Find the config file."""
        if self.config_path:
            return self.config_path if self.config_path.exists() else None
        
        for path in self.DEFAULT_CONFIG_PATHS:
            if path.exists():
                self.logger.debug("Found config", path=str(path))
                return path
        
        return None
    
    def _load_config(self) -> Dict[str, Any]:
        """Load configuration from file."""
        config_path = self._find_config()
        
        if not config_path:
            self.logger.info("No config file found")
            return {}
        
        try:
            import yaml
            
            with open(config_path) as f:
                config = yaml.safe_load(f) or {}
            
            self.config_path = config_path
            self._config_loaded = True
            return config
            
        except Exception as e:
            self.logger.error("Failed to load config", error=str(e))
            return {}
    
    def get_config(self) -> Dict[str, Any]:
        """Get loaded configuration."""
        if not self._config_loaded:
            self._config = self._load_config()
        return self._config or {}
    
    def validate(self) -> ConfigReport:
        """Validate the configuration.

        Returns:
            ConfigReport with validation results
        """
        config = self.get_config()
        report = ConfigReport(config_path=self.config_path)

        # Validate audio section
        audio_section = self._validate_audio(config)
        report.sections.append(audio_section)

        # Validate OpenClaw connection
        openclaw_section = self._validate_openclaw(config)
        report.sections.append(openclaw_section)

        # Validate STT config
        stt_section = self._validate_stt(config)
        report.sections.append(stt_section)

        # Validate TTS config
        tts_section = self._validate_tts(config)
        report.sections.append(tts_section)

        # Validate wake word config
        wake_word_section = self._validate_wake_word(config)
        report.sections.append(wake_word_section)

        # Validate persistence config
        persist_section = self._validate_persistence(config)
        report.sections.append(persist_section)

        # Validate bridge config
        bridge_section = self._validate_bridge(config)
        report.sections.append(bridge_section)

        # Collect all issues
        for section in report.sections:
            report.issues.extend(section.issues)

        # Overall validity
        report.valid = not report.has_errors

        return report
    
    def _validate_audio(self, config: Dict[str, Any]) -> ConfigSection:
        """Validate audio configuration."""
        section = ConfigSection(
            name="audio",
            display_name="Audio",
            icon="🎙️",
            fields=config.get("audio", {}),
        )
        
        audio_config = config.get("audio", {})
        
        # Check for input device
        input_device = audio_config.get("input_device", "default")
        if input_device == "default":
            section.issues.append(ConfigIssue(
                field="input_device",
                severity="info",
                message="Using system default input device",
                suggestion="Configure a specific microphone for better results",
            ))
        
        # Check for output device
        output_device = audio_config.get("output_device", "default")
        if output_device == "default":
            section.issues.append(ConfigIssue(
                field="output_device",
                severity="info",
                message="Using system default output device",
                suggestion="Configure a specific speaker/headphone for better results",
            ))
        
        # Validate sample rate
        sample_rate = audio_config.get("sample_rate", 16000)
        if sample_rate not in [8000, 16000, 22050, 44100, 48000]:
            section.issues.append(ConfigIssue(
                field="sample_rate",
                severity="warning",
                message=f"Unusual sample rate: {sample_rate}",
                suggestion="Common rates are 16000 (telephony) or 44100 (CD quality)",
            ))
        
        # Store fields
        section.fields = {
            "Input Device": input_device,
            "Output Device": output_device,
            "Sample Rate": f"{sample_rate} Hz",
            "Channels": audio_config.get("channels", 1),
        }
        
        return section
    
    def _validate_openclaw(self, config: Dict[str, Any]) -> ConfigSection:
        """Validate OpenClaw connection configuration."""
        section = ConfigSection(
            name="openclaw",
            display_name="OpenClaw Connection",
            icon="🔌",
            fields=config.get("openclaw", {}),
        )

        openclaw_config = config.get("openclaw", {})

        host = openclaw_config.get("host", "localhost")
        port = openclaw_config.get("port", 18789)
        api_mode = openclaw_config.get("api_mode", "http")

        # Check for auth token
        auth_token = openclaw_config.get("auth_token") or openclaw_config.get("api_key")
        if auth_token:
            section.issues.append(ConfigIssue(
                field="auth_token",
                severity="info",
                message="Auth token configured",
            ))
        else:
            section.issues.append(ConfigIssue(
                field="auth_token",
                severity="info",
                message="No auth token set (check OPENCLAW_GATEWAY_TOKEN env var)",
            ))

        # Store fields
        section.fields = {
            "Host": host,
            "Port": port,
            "API Mode": api_mode,
            "WS Path": openclaw_config.get("ws_path", "/api/voice"),
            "Secure": "Yes" if openclaw_config.get("secure") else "No",
            "Timeout": f"{openclaw_config.get('timeout', 30.0)}s",
            "Timeout (ms)": openclaw_config.get("timeout_ms", 30000),
        }

        return section
    
    def _validate_stt(self, config: Dict[str, Any]) -> ConfigSection:
        """Validate STT configuration."""
        section = ConfigSection(
            name="stt",
            display_name="Speech-to-Text",
            icon="📝",
            fields=config.get("stt", {}),
        )
        
        stt_config = config.get("stt", {})
        
        model = stt_config.get("model", "base")
        valid_models = ["tiny", "base", "small", "medium", "large", "large-v2", "large-v3"]
        
        if model not in valid_models:
            section.issues.append(ConfigIssue(
                field="model",
                severity="warning",
                message=f"Unknown model: {model}",
                suggestion=f"Valid models: {', '.join(valid_models)}",
            ))
        
        device = stt_config.get("device", "auto")
        if device == "cuda":
            section.issues.append(ConfigIssue(
                field="device",
                severity="info",
                message="CUDA acceleration enabled",
            ))
        
        # Store fields
        section.fields = {
            "Model": model,
            "Device": device,
            "Language": stt_config.get("language", "auto"),
            "Compute Type": stt_config.get("compute_type", "int8"),
        }
        
        return section
    
    def _validate_tts(self, config: Dict[str, Any]) -> ConfigSection:
        """Validate TTS configuration."""
        section = ConfigSection(
            name="tts",
            display_name="Text-to-Speech",
            icon="🔊",
            fields=config.get("tts", {}),
        )
        
        tts_config = config.get("tts", {})
        
        voice = tts_config.get("voice", "en_US-lessac-medium")
        
        # Store fields
        section.fields = {
            "Voice": voice,
            "Speed": tts_config.get("speed", 1.0),
            "Volume": tts_config.get("volume", 1.0),
        }
        
        return section
    
    def _validate_wake_word(self, config: Dict[str, Any]) -> ConfigSection:
        """Validate wake word configuration."""
        section = ConfigSection(
            name="wake_word",
            display_name="Wake Word",
            icon="🎤",
            fields=config.get("wake_word", {}),
        )

        ww_config = config.get("wake_word", {})

        backend = ww_config.get("backend", "stt")
        if backend not in ("stt", "openwakeword"):
            section.issues.append(ConfigIssue(
                field="backend",
                severity="warning",
                message=f"Unknown backend: {backend}",
                suggestion="Valid backends: 'stt' (reliable) or 'openwakeword' (fast)",
            ))

        section.fields = {
            "Wake Word": ww_config.get("wake_word", "computer"),
            "Backend": backend,
            "OWW Model": ww_config.get("openwakeword_model", "hey_mycroft"),
            "OWW Threshold": ww_config.get("openwakeword_threshold", 0.15),
            "Refractory (s)": ww_config.get("refractory_seconds", 2.0),
        }

        return section

    def _validate_bridge(self, config: Dict[str, Any]) -> ConfigSection:
        """Validate bridge behavior configuration."""
        section = ConfigSection(
            name="bridge",
            display_name="Bridge",
            icon="🌉",
            fields=config.get("bridge", {}),
        )

        bridge_config = config.get("bridge", {})
        ack_config = bridge_config.get("acknowledgement", {})

        section.fields = {
            "Response Timeout": f"{bridge_config.get('response_timeout', 10.0)}s",
            "Max Session Duration": f"{bridge_config.get('max_session_duration', 300.0)}s",
            "Log Level": bridge_config.get("log_level", "INFO"),
            "Hot Reload": "Yes" if bridge_config.get("hot_reload", True) else "No",
            "Ack Enabled": "Yes" if ack_config.get("enabled", True) else "No",
            "Ack Phrase": ack_config.get("response_phrase", "Yes?"),
            "Ack Timeout (ms)": ack_config.get("timeout_ms", 5000),
        }

        return section

    def _validate_persistence(self, config: Dict[str, Any]) -> ConfigSection:
        """Validate persistence configuration."""
        section = ConfigSection(
            name="persistence",
            display_name="Session Persistence",
            icon="💾",
            fields=config.get("persistence", {}),
        )
        
        persist_config = config.get("persistence", {})
        
        enabled = persist_config.get("enabled", True)
        ttl = persist_config.get("ttl_minutes", 30)
        max_history = persist_config.get("max_history", 10)
        
        # Store fields
        section.fields = {
            "Enabled": "Yes" if enabled else "No",
            "Session TTL": f"{ttl} minutes",
            "Max History": f"{max_history} turns",
        }
        
        return section
    
    def generate_display(self) -> str:
        """Generate a display-friendly configuration summary.
        
        Returns:
            Formatted configuration summary
        """
        report = self.validate()
        lines = []
        
        # Header
        lines.append("=" * 50)
        lines.append("Configuration Summary")
        lines.append("=" * 50)
        
        if self.config_path:
            lines.append(f"Config File: {self.config_path}")
        else:
            lines.append("Config File: (using defaults)")
        
        lines.append("")
        
        # Sections
        for section in report.sections:
            lines.append(f"\n{section.icon} {section.display_name}:")
            lines.append("-" * 40)
            
            for key, value in section.fields.items():
                lines.append(f"  {key}: {value}")
            
            if section.issues:
                for issue in section.issues:
                    lines.append(f"  {issue}")
        
        # Footer
        lines.append("")
        lines.append("=" * 50)
        lines.append(report.summary())
        
        return "\n".join(lines)
    
    def get_defaults(self) -> Dict[str, Any]:
        """Get default configuration values.

        Returns:
            Dictionary with default configuration
        """
        return {
            "audio": {
                "input_device": "default",
                "output_device": "default",
                "sample_rate": 16000,
                "channels": 1,
                "chunk_size": 1024,
                "wake_word_frame_size": 1280,
            },
            "openclaw": {
                "host": "localhost",
                "port": 18789,
                "secure": False,
                "timeout": 30.0,
                "timeout_ms": 30000,
                "api_mode": "http",
                "ws_path": "/api/voice",
                "auth_token": None,
            },
            "stt": {
                "model": "base",
                "device": "auto",
                "compute_type": "int8",
                "language": None,
                "beam_size": 5,
                "vad_filter": False,
                "vad_threshold": 0.5,
            },
            "tts": {
                "voice": "en_US-lessac-medium",
                "speed": 1.0,
                "volume": 1.0,
            },
            "wake_word": {
                "wake_word": "computer",
                "backend": "stt",
                "openwakeword_model": "hey_mycroft",
                "openwakeword_threshold": 0.15,
                "refractory_seconds": 2.0,
            },
            "persistence": {
                "enabled": True,
                "ttl_minutes": 30,
                "max_history": 10,
                "cleanup_interval": 60,
            },
            "bridge": {
                "response_timeout": 10.0,
                "max_session_duration": 300.0,
                "log_level": "INFO",
                "hot_reload": True,
                "acknowledgement": {
                    "enabled": True,
                    "response_phrase": "Yes?",
                    "timeout_ms": 5000,
                    "fallback_to_local_tts": True,
                },
            },
        }


def show_config_summary(config_path: Optional[Path] = None) -> str:
    """Convenience function to show config summary.
    
    Args:
        config_path: Optional explicit config path
        
    Returns:
        Formatted configuration summary
    """
    summary = ConfigSummary(config_path=config_path)
    return summary.generate_display()


def validate_config(config_path: Optional[Path] = None) -> ConfigReport:
    """Convenience function to validate config.
    
    Args:
        config_path: Optional explicit config path
        
    Returns:
        ConfigReport with validation results
    """
    summary = ConfigSummary(config_path=config_path)
    return summary.validate()