"""
WebSocket Protocol for OpenClaw Voice Bridge Communication.

Defines message types and serialization for bidirectional communication
between Voice Bridge (Python) and OpenClaw Gateway (Node.js).

Protocol Version: 1.0.0
"""
import json
from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Optional, Any
from uuid import uuid4

import structlog

logger = structlog.get_logger()


# Protocol version
PROTOCOL_VERSION = "1.0.0"


@dataclass
class BaseMessage:
    """Base class for all protocol messages."""
    
    type: str = field(default="base")
    timestamp: str = field(default_factory=lambda: datetime.utcnow().isoformat() + "Z")
    session_id: Optional[str] = None
    
    def to_json(self) -> str:
        """Serialize message to JSON string."""
        data = asdict(self)
        # Include all fields except timestamp can be omitted if None
        # Note: None values are included as null for optional fields
        return json.dumps(data)
    
    @classmethod
    def from_json(cls, json_str: str) -> "BaseMessage":
        """Deserialize message from JSON string."""
        data = json.loads(json_str)
        # Remove 'type' since it's set by init=False in subclasses
        data.pop('type', None)
        return cls(**data)


# =============================================================================
# Voice Bridge → OpenClaw Messages
# =============================================================================

@dataclass
class TranscriptMessage(BaseMessage):
    """
    Transcribed speech from Voice Bridge.
    
    Sent after wake word detection and speech transcription is complete.
    OpenClaw should process this as a user message.
    """
    type: str = field(default="transcript", init=False)
    text: str = ""
    confidence: float = 0.0
    session_id: str = field(default_factory=lambda: str(uuid4()))
    language: Optional[str] = None
    duration_ms: Optional[int] = None
    
    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = datetime.utcnow().isoformat() + "Z"


@dataclass
class WakeWordMessage(BaseMessage):
    """
    Wake word detection notification.
    
    Sent when wake word is detected. Creates a new session.
    OpenClaw may respond with an acknowledgement or wait for transcript.
    """
    type: str = field(default="wake_word_detected", init=False)
    wake_word: str = "computer"
    confidence: float = 1.0
    session_id: str = field(default_factory=lambda: str(uuid4()))
    
    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = datetime.utcnow().isoformat() + "Z"


@dataclass
class SessionStartMessage(BaseMessage):
    """
    Session start notification.
    
    Sent when a new voice session begins (after wake word).
    """
    type: str = field(default="session_start", init=False)
    session_id: str = field(default_factory=lambda: str(uuid4()))
    user_id: Optional[str] = None
    device_id: Optional[str] = None
    metadata: dict = field(default_factory=dict)
    
    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = datetime.utcnow().isoformat() + "Z"


@dataclass
class SessionEndMessage(BaseMessage):
    """
    Session end notification.
    
    Sent when a voice session ends (timeout, explicit end, or error).
    """
    type: str = field(default="session_end", init=False)
    session_id: str = ""
    reason: str = "timeout"  # timeout, explicit, error
    duration_ms: Optional[int] = None
    
    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = datetime.utcnow().isoformat() + "Z"


@dataclass
class TTSStartMessage(BaseMessage):
    """
    TTS playback start notification.
    
    Sent when Voice Bridge starts playing TTS audio.
    OpenClaw can use this to track speaking state.
    """
    type: str = field(default="tts_start", init=False)
    session_id: str = ""
    text_length: Optional[int] = None
    
    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = datetime.utcnow().isoformat() + "Z"


@dataclass
class TTSEndMessage(BaseMessage):
    """
    TTS playback end notification.
    
    Sent when Voice Bridge finishes playing TTS audio.
    OpenClaw can use this to resume listening state.
    """
    type: str = field(default="tts_end", init=False)
    session_id: str = ""
    was_interrupted: bool = False
    
    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = datetime.utcnow().isoformat() + "Z"


@dataclass
class ErrorMessage(BaseMessage):
    """
    Error notification.
    
    Sent when an error occurs in Voice Bridge.
    """
    type: str = field(default="error", init=False)
    message: str = ""
    code: Optional[str] = None
    session_id: Optional[str] = None
    recoverable: bool = False
    
    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = datetime.utcnow().isoformat() + "Z"


# =============================================================================
# OpenClaw → Voice Bridge Messages
# =============================================================================

@dataclass
class ResponseMessage(BaseMessage):
    """
    Response from OpenClaw to be spoken.
    
    OpenClaw sends this after processing a transcript.
    Voice Bridge should speak this text via TTS.
    """
    type: str = field(default="response", init=False)
    text: str = ""
    session_id: str = ""
    speak: bool = True  # If False, don't speak (just update state)
    interrupt_current: bool = False  # Interrupt current TTS if speaking
    
    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = datetime.utcnow().isoformat() + "Z"


@dataclass
class AcknowledgementMessage(BaseMessage):
    """
    Wake word acknowledgement.
    
    OpenClaw can send this to acknowledge wake word detection
    and provide a custom acknowledgement phrase.
    """
    type: str = field(default="acknowledgement", init=False)
    session_id: str = ""
    text: Optional[str] = None  # Custom ack phrase, or None for default
    sound: Optional[str] = None  # Optional sound effect
    
    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = datetime.utcnow().isoformat() + "Z"


@dataclass
class ControlMessage(BaseMessage):
    """
    Control message for session management.
    
    Used to control Voice Bridge behavior from OpenClaw.
    """
    type: str = field(default="control", init=False)
    action: str = ""  # interrupt, pause, resume, cancel
    session_id: Optional[str] = None
    data: dict = field(default_factory=dict)
    
    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = datetime.utcnow().isoformat() + "Z"


@dataclass
class ConfigUpdateMessage(BaseMessage):
    """
    Configuration update from OpenClaw.
    
    Used to dynamically update Voice Bridge settings.
    """
    type: str = field(default="config_update", init=False)
    config: dict = field(default_factory=dict)
    
    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = datetime.utcnow().isoformat() + "Z"


# =============================================================================
# Connection Management Messages
# =============================================================================

@dataclass
class PingMessage(BaseMessage):
    """Ping message for keepalive."""
    type: str = field(default="ping", init=False)
    
    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = datetime.utcnow().isoformat() + "Z"


@dataclass
class PongMessage(BaseMessage):
    """Pong response to ping."""
    type: str = field(default="pong", init=False)
    
    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = datetime.utcnow().isoformat() + "Z"


@dataclass
class HelloMessage(BaseMessage):
    """
    Initial handshake message.
    
    Sent by Voice Bridge when WebSocket connection is established.
    """
    type: str = field(default="hello", init=False)
    version: str = PROTOCOL_VERSION
    capabilities: list = field(default_factory=lambda: [
        "transcript",
        "wake_word_detected",
        "session_start",
        "session_end",
        "tts_start",
        "tts_end",
        "error",
    ])
    
    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = datetime.utcnow().isoformat() + "Z"


# =============================================================================
# Message Parsing
# =============================================================================

# Message type registry
MESSAGE_TYPES = {
    "transcript": TranscriptMessage,
    "wake_word_detected": WakeWordMessage,
    "session_start": SessionStartMessage,
    "session_end": SessionEndMessage,
    "tts_start": TTSStartMessage,
    "tts_end": TTSEndMessage,
    "error": ErrorMessage,
    "response": ResponseMessage,
    "acknowledgement": AcknowledgementMessage,
    "control": ControlMessage,
    "config_update": ConfigUpdateMessage,
    "ping": PingMessage,
    "pong": PongMessage,
    "hello": HelloMessage,
}


def parse_message(json_str: str) -> BaseMessage:
    """
    Parse a JSON message string into the appropriate message type.
    
    Args:
        json_str: JSON string to parse
        
    Returns:
        BaseMessage subclass instance
        
    Raises:
        ValueError: If message type is unknown or JSON is invalid
    """
    try:
        data = json.loads(json_str)
    except json.JSONDecodeError as e:
        raise ValueError(f"Invalid JSON: {e}")
    
    msg_type = data.get("type")
    if not msg_type:
        raise ValueError("Missing message type")
    
    message_class = MESSAGE_TYPES.get(msg_type)
    if not message_class:
        raise ValueError(f"Unknown message type: {msg_type}")
    
    # Remove type from data since dataclass sets it automatically
    data_copy = {k: v for k, v in data.items() if k != "type"}
    
    try:
        return message_class(**data_copy)
    except TypeError as e:
        raise ValueError(f"Invalid message data: {e}")


def validate_message(message: BaseMessage) -> tuple[bool, Optional[str]]:
    """
    Validate a message.
    
    Args:
        message: Message to validate
        
    Returns:
        Tuple of (is_valid, error_message)
    """
    # Check required fields based on message type
    if isinstance(message, TranscriptMessage):
        if not message.text:
            return False, "TranscriptMessage requires text"
        if not message.session_id:
            return False, "TranscriptMessage requires session_id"
        if not 0.0 <= message.confidence <= 1.0:
            return False, "TranscriptMessage confidence must be between 0 and 1"
            
    elif isinstance(message, WakeWordMessage):
        if not message.wake_word:
            return False, "WakeWordMessage requires wake_word"
        if not message.session_id:
            return False, "WakeWordMessage requires session_id"
            
    elif isinstance(message, ResponseMessage):
        if not message.session_id:
            return False, "ResponseMessage requires session_id"
            
    elif isinstance(message, ErrorMessage):
        if not message.message:
            return False, "ErrorMessage requires message"
    
    return True, None


# =============================================================================
# Utility Functions
# =============================================================================

def generate_session_id() -> str:
    """Generate a new session ID."""
    return str(uuid4())


def create_transcript(
    text: str,
    confidence: float,
    session_id: Optional[str] = None,
    **kwargs
) -> TranscriptMessage:
    """
    Create a transcript message.
    
    Args:
        text: Transcribed text
        confidence: Confidence score (0.0 - 1.0)
        session_id: Session ID (generates new if None)
        **kwargs: Additional fields
        
    Returns:
        TranscriptMessage instance
    """
    return TranscriptMessage(
        text=text,
        confidence=confidence,
        session_id=session_id or generate_session_id(),
        **kwargs
    )


def create_wake_word(
    wake_word: str = "computer",
    confidence: float = 1.0,
    session_id: Optional[str] = None,
    **kwargs
) -> WakeWordMessage:
    """
    Create a wake word message.
    
    Args:
        wake_word: Detected wake word
        confidence: Detection confidence (0.0 - 1.0)
        session_id: Session ID (generates new if None)
        **kwargs: Additional fields
        
    Returns:
        WakeWordMessage instance
    """
    return WakeWordMessage(
        wake_word=wake_word,
        confidence=confidence,
        session_id=session_id or generate_session_id(),
        **kwargs
    )


def create_response(
    text: str,
    session_id: str,
    **kwargs
) -> ResponseMessage:
    """
    Create a response message.
    
    Args:
        text: Response text to speak
        session_id: Target session ID
        **kwargs: Additional fields
        
    Returns:
        ResponseMessage instance
    """
    return ResponseMessage(
        text=text,
        session_id=session_id,
        **kwargs
    )


def create_error(
    message: str,
    code: Optional[str] = None,
    session_id: Optional[str] = None,
    recoverable: bool = False,
    **kwargs
) -> ErrorMessage:
    """
    Create an error message.
    
    Args:
        message: Error message
        code: Error code
        session_id: Affected session (if any)
        recoverable: Whether error is recoverable
        **kwargs: Additional fields
        
    Returns:
        ErrorMessage instance
    """
    return ErrorMessage(
        message=message,
        code=code,
        session_id=session_id,
        recoverable=recoverable,
        **kwargs
    )


# =============================================================================
# Logging Helpers
# =============================================================================

def log_message(message: BaseMessage, direction: str = "send") -> None:
    """
    Log a message with consistent formatting.
    
    Args:
        message: Message to log
        direction: "send" or "receive"
    """
    logger.info(
        f"websocket_{direction}",
        type=message.type,
        session_id=message.session_id,
        timestamp=message.timestamp,
    )


def log_message_summary(messages: list[BaseMessage]) -> dict:
    """
    Create a summary of messages for logging.
    
    Args:
        messages: List of messages
        
    Returns:
        Summary dict with counts by type
    """
    summary = {}
    for msg in messages:
        msg_type = msg.type
        if msg_type not in summary:
            summary[msg_type] = 0
        summary[msg_type] += 1
    return summary