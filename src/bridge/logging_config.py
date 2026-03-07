"""
Centralized logging configuration for Voice Bridge.

This module provides structured logging setup using structlog, addressing
the scattered logging setup issue (LOG-001). All components should use
get_logger() from this module for consistent logging.

Example:
    >>> from bridge.logging_config import setup_logging, get_logger
    >>> setup_logging(level="DEBUG")
    >>> logger = get_logger(__name__)
    >>> logger.info("Application started", component="main")

Attributes:
    LOG_FORMATS: Supported log format configurations
    DEFAULT_LEVEL: Default log level if not specified
"""

import logging
import sys
from pathlib import Path
from typing import Optional, List

try:
    import structlog
    STRUCTLOG_AVAILABLE = True
except ImportError:
    STRUCTLOG_AVAILABLE = False
    structlog = None  # type: ignore

# Default configuration
DEFAULT_LEVEL = "INFO"
LOG_FORMATS = {
    "console": "dev",
    "json": "json",
}


class LoggingConfigurationError(Exception):
    """Raised when logging configuration fails."""
    pass


def _get_log_level(level: str) -> int:
    """Convert string log level to logging constant.
    
    Args:
        level: String representation of log level (e.g., "INFO", "DEBUG")
    
    Returns:
        Logging level constant (e.g., logging.INFO)
    
    Raises:
        LoggingConfigurationError: If level string is invalid
    """
    level_map = {
        "DEBUG": logging.DEBUG,
        "INFO": logging.INFO,
        "WARNING": logging.WARNING,
        "WARN": logging.WARNING,
        "ERROR": logging.ERROR,
        "CRITICAL": logging.CRITICAL,
        "FATAL": logging.CRITICAL,
    }
    
    upper_level = level.upper()
    if upper_level not in level_map:
        valid_levels = ", ".join(sorted(level_map.keys()))
        raise LoggingConfigurationError(
            f"Invalid log level '{level}'. Valid levels: {valid_levels}"
        )
    
    return level_map[upper_level]


def _create_handlers(
    log_file: Optional[str],
    stream: Optional[object] = None
) -> List[logging.Handler]:
    """Create logging handlers for console and file output.
    
    Args:
        log_file: Optional path for file logging
        stream: Output stream (defaults to sys.stdout)
    
    Returns:
        List of configured logging handlers
    """
    handlers: List[logging.Handler] = []
    
    # Console handler
    output_stream = stream if stream is not None else sys.stdout
    console_handler = logging.StreamHandler(output_stream)
    console_handler.setLevel(logging.DEBUG)  # Level controlled by logger
    handlers.append(console_handler)
    
    # File handler (optional)
    if log_file:
        log_path = Path(log_file)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(log_file)
        file_handler.setLevel(logging.DEBUG)
        handlers.append(file_handler)
    
    return handlers


def setup_logging(
    level: str = DEFAULT_LEVEL,
    log_file: Optional[str] = None,
    json_format: bool = False,
    stream: Optional[object] = None
) -> None:
    """Configure structured logging for Voice Bridge.
    
    This function configures both the standard library logging and structlog
    to work together, providing consistent, structured output across all
    Voice Bridge components.
    
    Args:
        level: Log level (DEBUG, INFO, WARNING, ERROR, CRITICAL).
            Defaults to "INFO".
        log_file: Optional file path for logging. The directory will be
            created if it doesn't exist.
        json_format: Use JSON format instead of console renderer. Set to
            True for production/kibana-style log aggregation.
        stream: Output stream for console logging (defaults to sys.stdout).
            Use sys.stderr for error output.
    
    Raises:
        LoggingConfigurationError: If configuration fails.
    
    Example:
        >>> # Basic setup (console output)
        >>> setup_logging(level="DEBUG")
        >>> logger = get_logger(__name__)
        >>> logger.info("Started", component="bridge")
        
        >>> # Production setup (JSON to file)
        >>> setup_logging(
        ...     level="INFO",
        ...     log_file="/var/log/voice-bridge/app.log",
        ...     json_format=True
        ... )
    
    Note:
        This function should be called once at application startup.
        Subsequent calls will reconfigure logging.
    """
    if not STRUCTLOG_AVAILABLE:
        # Fallback to basic logging if structlog not installed
        logging.basicConfig(
            level=_get_log_level(level),
            format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
            stream=stream or sys.stdout,
        )
        return
    
    log_level = _get_log_level(level)
    handlers = _create_handlers(log_file, stream)
    
    # Configure standard library logging
    # Use empty format - structlog handles formatting
    logging.basicConfig(
        format="",
        level=log_level,
        handlers=handlers,
        force=True,  # Override any existing configuration
    )
    
    # Configure structlog processors
    processors = [
        structlog.stdlib.filter_by_level,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        structlog.processors.UnicodeDecoder(),
    ]
    
    # Add terminal processor based on format
    if json_format:
        processors.append(structlog.processors.JSONRenderer())
    else:
        processors.append(structlog.dev.ConsoleRenderer())
    
    structlog.configure(
        processors=processors,
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )


def get_logger(name: Optional[str] = None) -> "structlog.stdlib.BoundLogger":
    """Get a configured structlog logger instance.
    
    This function returns a bound logger configured for the Voice Bridge
    application. Use this throughout the codebase for consistent logging.
    
    Args:
        name: Logger name, typically __name__ of the calling module.
            If None, structlog will infer the name from the calling context.
    
    Returns:
        A configured structlog BoundLogger instance.
    
    Example:
        >>> # At module level
        >>> logger = get_logger(__name__)
        >>> logger.info("Processing audio", samples=1024, rate=16000)
        
        >>> # In a class
        >>> class AudioProcessor:
        ...     def __init__(self):
        ...         self.log = get_logger(__name__).bind(component="processor")
        ...         self.log.debug("Initialized")
        
        >>> # With exception handling
        >>> try:
        ...     risky_operation()
        ... except Exception as e:
        ...     logger.error("Operation failed", error=str(e))
    
    Note:
        If structlog is not available, returns a basic logging.Logger
        wrapper that mimics structlog's interface for compatibility.
    """
    if STRUCTLOG_AVAILABLE:
        return structlog.get_logger(name)
    else:
        # Return a wrapper that mimics structlog interface
        import logging
        base_logger = logging.getLogger(name)
        return _StructlogFallbackWrapper(base_logger)


class _StructlogFallbackWrapper:
    """Fallback wrapper when structlog is not available.
    
    Provides minimal compatibility with structlog interface using
    standard library logging.
    """
    
    def __init__(self, logger: logging.Logger):
        self._logger = logger
    
    def _log(self, level: str, msg: str, **kwargs: object) -> None:
        """Log a message with optional context."""
        # Extract structured context
        extra_data = " ".join(f"{k}={v}" for k, v in kwargs.items())
        full_msg = f"{msg} {extra_data}" if extra_data else msg
        
        log_method = getattr(self._logger, level, self._logger.info)
        log_method(full_msg)
    
    def debug(self, msg: str, **kwargs: object) -> None:
        self._log("debug", msg, **kwargs)
    
    def info(self, msg: str, **kwargs: object) -> None:
        self._log("info", msg, **kwargs)
    
    def warning(self, msg: str, **kwargs: object) -> None:
        self._log("warning", msg, **kwargs)
    
    def error(self, msg: str, **kwargs: object) -> None:
        self._log("error", msg, **kwargs)
    
    def critical(self, msg: str, **kwargs: object) -> None:
        self._log("critical", msg, **kwargs)
    
    def bind(self, **kwargs: object) -> "_StructlogFallbackWrapper":
        """Bind context (no-op for fallback, returns self)."""
        return self


# Module-level logger for internal use
_logger_name = __name__