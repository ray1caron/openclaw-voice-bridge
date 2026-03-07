"""
constants.py - Centralized constants module for Voice Bridge v4.

This module consolidates all magic numbers and configuration constants
that were previously scattered across multiple files. Using centralized
constants improves maintainability, reduces errors from inconsistent values,
and makes the codebase easier to understand and modify.

All constants are typed and documented for clarity.

CONST-001: Consolidation of scattered magic numbers.
"""

from pathlib import Path


# =============================================================================
# Audio Settings
# =============================================================================

DEFAULT_SAMPLE_RATE: int = 16000
"""
Default audio sample rate in Hz.

16kHz is the standard sample rate for speech recognition systems.
This matches Whisper's expected input and provides sufficient quality
for voice while keeping computational requirements reasonable.
"""

DEFAULT_CHANNELS: int = 1
"""
Default number of audio channels.

1 = mono audio. Voice assistants don't need stereo; mono reduces
processing overhead and simplifies the audio pipeline.
"""

DEFAULT_CHUNK_SIZE: int = 1024
"""
Default audio chunk size in frames.

This is the number of samples read per audio read operation.
At 16kHz, 1024 samples = 64ms of audio. Smaller chunks provide
lower latency but more frequent callbacks.
"""


# =============================================================================
# Wake Word Detection
# =============================================================================

DEFAULT_WAKE_WORD_FRAME_SIZE: int = 1280
"""
Default frame size for wake word detection in samples.

80ms at 16kHz = 1280 samples. This frame size is optimized for
openWakeWord's expectation and provides good detection accuracy
while maintaining reasonable processing overhead.
"""

DEFAULT_WAKE_WORD: str = "hey jarvis"
"""
Default wake word phrase.

The wake word that triggers the assistant to start listening.
Can be configured via user preferences. The default is a common
assistant wake word that's easy to say and distinctly recognizable.
"""

WAKE_WORD_REFRACTORY_SECONDS: float = 2.0
"""
Cooldown period after a wake word detection in seconds.

After detecting a wake word, the detector ignores further detections
for this duration to prevent multiple triggers from a single utterance
and to allow the user to complete their command.
"""


# =============================================================================
# Voice Activity Detection (VAD)
# =============================================================================

VAD_FRAME_MS: int = 30
"""
VAD frame duration in milliseconds.

30ms frames are standard for WebRTC VAD. This matches the internal
processing windows of the VAD algorithm for optimal accuracy.
"""

VAD_AGGRESSIVENESS_MODE: int = 3
"""
VAD aggressiveness level (0-3).

Higher values = more aggressive filtering of non-speech.
- 0: Least aggressive (most permissive, may include noise)
- 1: Low aggressiveness
- 2: Moderate aggressiveness
- 3: Most aggressive (strict speech detection, may cut off quiet speech)

Mode 3 is recommended for voice assistants where false positives
(noise interpreted as speech) are more problematic than occasionally
missing very quiet speech beginnings.
"""


# =============================================================================
# Session Management
# =============================================================================

SESSION_TIMEOUT_MINUTES: int = 30
"""
Default session timeout in minutes.

After this period of inactivity, a conversation session is considered
stale and may be cleaned up. This balances memory usage with user
experience (returning users expect their context to persist briefly).
"""

MAX_CONTEXT_TURNS: int = 20
"""
Maximum number of conversation turns to retain in context.

This limits the conversation history passed to the LLM, ensuring
context windows aren't exceeded while maintaining enough history
for coherent multi-turn conversations. Adjust based on model context
length and typical conversation patterns.
"""


# =============================================================================
# HTTP/WebSocket Communication
# =============================================================================

DEFAULT_HTTP_TIMEOUT_SECONDS: int = 30
"""
Default timeout for HTTP requests in seconds.

This is a reasonable default for most API calls. Long-running requests
(like complex LLM completions) should specify their own timeouts.
"""

DEFAULT_WEBSOCKET_TIMEOUT_SECONDS: int = 30
"""
Default timeout for WebSocket operations in seconds.

Applies to connection establishment and message reception. WebSocket
connections for real-time audio may use different timeouts for the
streaming portion vs. initial connection.
"""


# =============================================================================
# File System Paths
# =============================================================================

DEFAULT_CONFIG_DIR: Path = Path.home() / ".voice-bridge"
"""
Default configuration directory path.

User-specific configuration, data, and models are stored here.
Using XDG Base Directory specification would be ideal, but for
simplicity we use ~/.voice-bridge for all user data.
"""

DEFAULT_CONFIG_FILE: Path = DEFAULT_CONFIG_DIR / "config.yaml"
"""
Default configuration file path.

YAML format allows for human-readable configuration with comments.
The file is created on first run with sensible defaults if it doesn't exist.
"""

DEFAULT_DATA_DIR: Path = DEFAULT_CONFIG_DIR / "data"
"""
Default data directory path.

Stores persistent data like conversation history, user preferences,
cached models, and session logs. Separate from config for clarity.
"""


# =============================================================================
# Database Settings
# =============================================================================

DB_VERSION: int = 1
"""
Current database schema version.

Used for migration tracking. When the schema changes, increment this
and add migration logic to handle the upgrade path.
"""

MAX_SESSIONS: int = 100
"""
Maximum number of concurrent sessions to track.

This prevents unbounded memory growth from session accumulation.
Older/less active sessions are evicted first when the limit is reached.
"""


# =============================================================================
# Speech-to-Text Models
# =============================================================================

SUPPORTED_STT_MODELS: set[str] = {
    "tiny",
    "base", 
    "small",
    "medium",
    "large",
    "large-v2",
    "large-v3",
}
"""
Set of supported Whisper STT model variants.

These are the available Whisper model sizes from OpenAI:
- tiny: Fastest, lowest accuracy (~39M params)
- base: Fast, basic accuracy (~74M params)
- small: Good balance (~244M params)
- medium: Better accuracy (~769M params)
- large/large-v2/large-v3: Best accuracy (~1550M params)

v2 and v3 are improved versions of the large model.
"""


# =============================================================================
# Text-to-Speech Voices
# =============================================================================

DEFAULT_TTS_VOICE: str = "en_US-lessac-medium"
"""
Default TTS voice identifier.

Format: {language}_{voice_name}_{quality}

Common options include:
- en_US-lessac-medium: Natural American English, good quality
- en_US-amy-medium: Another natural American voice
- en_GB-alba-medium: British English voice

The "medium" quality strikes a good balance between naturalness
and processing speed for real-time responses.
"""


# =============================================================================
# Validation
# =============================================================================

def _validate_constants() -> None:
    """
    Validate constants at module import time for early error detection.
    
    This catches configuration errors before they cause runtime issues.
    """
    assert DEFAULT_SAMPLE_RATE > 0, "Sample rate must be positive"
    assert DEFAULT_CHANNELS >= 1, "Channels must be at least 1"
    assert DEFAULT_CHUNK_SIZE > 0, "Chunk size must be positive"
    assert DEFAULT_WAKE_WORD_FRAME_SIZE > 0, "Wake word frame size must be positive"
    assert WAKE_WORD_REFRACTORY_SECONDS >= 0, "Refractory must be non-negative"
    assert 0 <= VAD_AGGRESSIVENESS_MODE <= 3, "VAD mode must be 0-3"
    assert SESSION_TIMEOUT_MINUTES > 0, "Session timeout must be positive"
    assert MAX_CONTEXT_TURNS > 0, "Max context turns must be positive"
    assert DEFAULT_HTTP_TIMEOUT_SECONDS > 0, "HTTP timeout must be positive"
    assert DEFAULT_WEBSOCKET_TIMEOUT_SECONDS > 0, "WebSocket timeout must be positive"
    assert DB_VERSION >= 1, "DB version must be at least 1"
    assert MAX_SESSIONS > 0, "Max sessions must be positive"
    assert len(SUPPORTED_STT_MODELS) > 0, "Must support at least one STT model"
    assert len(DEFAULT_TTS_VOICE) > 0, "TTS voice cannot be empty"


# Run validation on import
_validate_constants()


__all__ = [
    # Audio Settings
    "DEFAULT_SAMPLE_RATE",
    "DEFAULT_CHANNELS",
    "DEFAULT_CHUNK_SIZE",
    # Wake Word
    "DEFAULT_WAKE_WORD_FRAME_SIZE",
    "DEFAULT_WAKE_WORD",
    "WAKE_WORD_REFRACTORY_SECONDS",
    # VAD
    "VAD_FRAME_MS",
    "VAD_AGGRESSIVENESS_MODE",
    # Session Management
    "SESSION_TIMEOUT_MINUTES",
    "MAX_CONTEXT_TURNS",
    # HTTP/WebSocket
    "DEFAULT_HTTP_TIMEOUT_SECONDS",
    "DEFAULT_WEBSOCKET_TIMEOUT_SECONDS",
    # Paths
    "DEFAULT_CONFIG_DIR",
    "DEFAULT_CONFIG_FILE",
    "DEFAULT_DATA_DIR",
    # Database
    "DB_VERSION",
    "MAX_SESSIONS",
    # STT Models
    "SUPPORTED_STT_MODELS",
    # TTS Voices
    "DEFAULT_TTS_VOICE",
]