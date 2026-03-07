"""
Test WebSocket message protocol.

This file tests the protocol module that defines the message format
for communication between Voice Bridge and OpenClaw Gateway.
"""
import pytest
import json
from datetime import datetime

from bridge.protocol import (
    # Message classes
    BaseMessage,
    TranscriptMessage,
    WakeWordMessage,
    SessionStartMessage,
    SessionEndMessage,
    TTSStartMessage,
    TTSEndMessage,
    ErrorMessage,
    ResponseMessage,
    AcknowledgementMessage,
    ControlMessage,
    ConfigUpdateMessage,
    PingMessage,
    PongMessage,
    HelloMessage,
    # Parsing
    parse_message,
    validate_message,
    MESSAGE_TYPES,
    # Utilities
    generate_session_id,
    create_transcript,
    create_wake_word,
    create_response,
    create_error,
    log_message,
    log_message_summary,
    PROTOCOL_VERSION,
)


class TestTranscriptMessage:
    """Tests for TranscriptMessage."""
    
    def test_serialization(self):
        """Test transcript message serialization."""
        msg = TranscriptMessage(
            text="hello world",
            confidence=0.95,
            session_id="test-123"
        )
        serialized = msg.to_json()
        data = json.loads(serialized)
        
        assert data["type"] == "transcript"
        assert data["text"] == "hello world"
        assert data["confidence"] == 0.95
        assert data["session_id"] == "test-123"
        assert "timestamp" in data
    
    def test_deserialization(self):
        """Test transcript message deserialization."""
        json_str = json.dumps({
            "type": "transcript",
            "text": "what time is it",
            "confidence": 0.87,
            "session_id": "session-456",
            "timestamp": "2026-03-06T10:30:00Z"
        })
        
        msg = TranscriptMessage.from_json(json_str)
        
        assert msg.type == "transcript"
        assert msg.text == "what time is it"
        assert msg.confidence == 0.87
        assert msg.session_id == "session-456"
    
    def test_auto_session_id(self):
        """Test that session_id is auto-generated if not provided."""
        msg = TranscriptMessage(text="test", confidence=0.9)
        
        assert msg.session_id is not None
        assert len(msg.session_id) == 36  # UUID format
    
    def test_auto_timestamp(self):
        """Test that timestamp is auto-generated."""
        msg = TranscriptMessage(text="test", confidence=0.9, session_id="test")
        
        assert msg.timestamp is not None
        # Should be ISO format
        assert "T" in msg.timestamp
    
    def test_optional_fields(self):
        """Test optional fields."""
        msg = TranscriptMessage(
            text="hello",
            confidence=0.95,
            session_id="test",
            language="en",
            duration_ms=1500
        )
        
        data = json.loads(msg.to_json())
        assert data["language"] == "en"
        assert data["duration_ms"] == 1500


class TestWakeWordMessage:
    """Tests for WakeWordMessage."""
    
    def test_serialization(self):
        """Test wake word message serialization."""
        msg = WakeWordMessage(
            wake_word="computer",
            confidence=0.98,
            session_id="wake-session"
        )
        
        serialized = msg.to_json()
        data = json.loads(serialized)
        
        assert data["type"] == "wake_word_detected"
        assert data["wake_word"] == "computer"
        assert data["confidence"] == 0.98
        assert data["session_id"] == "wake-session"
    
    def test_default_values(self):
        """Test default values for wake word message."""
        msg = WakeWordMessage()
        
        assert msg.wake_word == "computer"
        assert msg.confidence == 1.0


class TestSessionMessages:
    """Tests for session start/end messages."""
    
    def test_session_start(self):
        """Test session start message."""
        msg = SessionStartMessage(
            session_id="session-123",
            user_id="user-456",
            device_id="device-789",
            metadata={"location": "kitchen"}
        )
        
        data = json.loads(msg.to_json())
        
        assert data["type"] == "session_start"
        assert data["session_id"] == "session-123"
        assert data["user_id"] == "user-456"
        assert data["metadata"]["location"] == "kitchen"
    
    def test_session_end_timeout(self):
        """Test session end message with timeout reason."""
        msg = SessionEndMessage(
            session_id="session-123",
            reason="timeout",
            duration_ms=15000
        )
        
        data = json.loads(msg.to_json())
        
        assert data["type"] == "session_end"
        assert data["reason"] == "timeout"
        assert data["duration_ms"] == 15000


class TestTTS_Messages:
    """Tests for TTS start/end messages."""
    
    def test_tts_start(self):
        """Test TTS start message."""
        msg = TTSStartMessage(
            session_id="session-123",
            text_length=50
        )
        
        data = json.loads(msg.to_json())
        
        assert data["type"] == "tts_start"
        assert data["session_id"] == "session-123"
        assert data["text_length"] == 50
    
    def test_tts_end(self):
        """Test TTS end message."""
        msg = TTSEndMessage(
            session_id="session-123",
            was_interrupted=True
        )
        
        data = json.loads(msg.to_json())
        
        assert data["type"] == "tts_end"
        assert data["was_interrupted"] is True


class TestErrorMessage:
    """Tests for ErrorMessage."""
    
    def test_basic_error(self):
        """Test basic error message."""
        msg = ErrorMessage(
            message="Microphone not found",
            code="AUDIO_INIT_FAILED"
        )
        
        data = json.loads(msg.to_json())
        
        assert data["type"] == "error"
        assert data["message"] == "Microphone not found"
        assert data["code"] == "AUDIO_INIT_FAILED"
        assert data["recoverable"] is False
    
    def test_recoverable_error(self):
        """Test recoverable error."""
        msg = ErrorMessage(
            message="Connection lost, reconnecting...",
            code="WS_DISCONNECTED",
            recoverable=True
        )
        
        data = json.loads(msg.to_json())
        
        assert data["recoverable"] is True


class TestResponseMessage:
    """Tests for ResponseMessage (OpenClaw → Voice Bridge)."""
    
    def test_basic_response(self):
        """Test basic response message."""
        msg = ResponseMessage(
            text="I can help with that.",
            session_id="session-123"
        )
        
        data = json.loads(msg.to_json())
        
        assert data["type"] == "response"
        assert data["text"] == "I can help with that."
        assert data["speak"] is True
    
    def test_silent_response(self):
        """Test silent response (don't speak)."""
        msg = ResponseMessage(
            text="Context updated",
            session_id="session-123",
            speak=False
        )
        
        data = json.loads(msg.to_json())
        
        assert data["speak"] is False
    
    def test_interrupt_response(self):
        """Test interrupt response."""
        msg = ResponseMessage(
            text="Stop what you're doing and listen.",
            session_id="session-123",
            interrupt_current=True
        )
        
        data = json.loads(msg.to_json())
        
        assert data["interrupt_current"] is True


class TestAcknowledgementMessage:
    """Tests for AcknowledgementMessage."""
    
    def test_basic_acknowledgement(self):
        """Test basic acknowledgement."""
        msg = AcknowledgementMessage(session_id="session-123")
        
        data = json.loads(msg.to_json())
        
        assert data["type"] == "acknowledgement"
        assert data["session_id"] == "session-123"
        assert data["text"] is None
    
    def test_custom_acknowledgement(self):
        """Test custom acknowledgement phrase."""
        msg = AcknowledgementMessage(
            session_id="session-123",
            text="Yes, I'm listening"
        )
        
        data = json.loads(msg.to_json())
        
        assert data["text"] == "Yes, I'm listening"


class TestControlMessage:
    """Tests for ControlMessage."""
    
    def test_interrupt_control(self):
        """Test interrupt control message."""
        msg = ControlMessage(
            action="interrupt",
            session_id="session-123"
        )
        
        data = json.loads(msg.to_json())
        
        assert data["type"] == "control"
        assert data["action"] == "interrupt"
    
    def test_config_control(self):
        """Test config update control message."""
        msg = ControlMessage(
            action="set_wake_word",
            session_id="session-123",
            data={"wake_word": "hey assistant"}
        )
        
        data = json.loads(msg.to_json())
        
        assert data["action"] == "set_wake_word"
        assert data["data"]["wake_word"] == "hey assistant"


class TestPingPong:
    """Tests for ping/pong messages."""
    
    def test_ping_message(self):
        """Test ping message."""
        msg = PingMessage()
        
        data = json.loads(msg.to_json())
        
        assert data["type"] == "ping"
    
    def test_pong_message(self):
        """Test pong message."""
        msg = PongMessage()
        
        data = json.loads(msg.to_json())
        
        assert data["type"] == "pong"
    
    def test_ping_pong_roundtrip(self):
        """Test ping/pong roundtrip."""
        ping = PingMessage()
        pong = PongMessage()
        
        ping_json = ping.to_json()
        pong_json = pong.to_json()
        
        parsed_ping = parse_message(ping_json)
        parsed_pong = parse_message(pong_json)
        
        assert isinstance(parsed_ping, PingMessage)
        assert isinstance(parsed_pong, PongMessage)


class TestHelloMessage:
    """Tests for HelloMessage."""
    
    def test_hello_message(self):
        """Test hello handshake message."""
        msg = HelloMessage()
        
        data = json.loads(msg.to_json())
        
        assert data["type"] == "hello"
        assert data["version"] == PROTOCOL_VERSION
        assert "transcript" in data["capabilities"]
        assert "wake_word_detected" in data["capabilities"]
    
    def test_hello_capabilities(self):
        """Test hello message capabilities."""
        msg = HelloMessage()
        
        # Should include all message types Voice Bridge can send
        assert "transcript" in msg.capabilities
        assert "wake_word_detected" in msg.capabilities
        assert "error" in msg.capabilities


class TestMessageParsing:
    """Tests for message parsing."""
    
    def test_parse_transcript(self):
        """Test parsing transcript message."""
        json_str = json.dumps({
            "type": "transcript",
            "text": "hello",
            "confidence": 0.9,
            "session_id": "test-123"
        })
        
        msg = parse_message(json_str)
        
        assert isinstance(msg, TranscriptMessage)
        assert msg.text == "hello"
        assert msg.confidence == 0.9
    
    def test_parse_response(self):
        """Test parsing response message."""
        json_str = json.dumps({
            "type": "response",
            "text": "I heard you",
            "session_id": "test-456"
        })
        
        msg = parse_message(json_str)
        
        assert isinstance(msg, ResponseMessage)
        assert msg.text == "I heard you"
    
    def test_parse_unknown_type(self):
        """Test parsing unknown message type."""
        json_str = json.dumps({
            "type": "unknown_type",
            "data": "something"
        })
        
        with pytest.raises(ValueError, match="Unknown message type"):
            parse_message(json_str)
    
    def test_parse_missing_type(self):
        """Test parsing message without type."""
        json_str = json.dumps({
            "text": "hello",
            "confidence": 0.9
        })
        
        with pytest.raises(ValueError, match="Missing message type"):
            parse_message(json_str)
    
    def test_parse_invalid_json(self):
        """Test parsing invalid JSON."""
        with pytest.raises(ValueError, match="Invalid JSON"):
            parse_message("not valid json")


class TestMessageValidation:
    """Tests for message validation."""
    
    def test_valid_transcript(self):
        """Test validating valid transcript."""
        msg = TranscriptMessage(
            text="hello",
            confidence=0.95,
            session_id="test-123"
        )
        
        is_valid, error = validate_message(msg)
        
        assert is_valid is True
        assert error is None
    
    def test_invalid_transcript_no_text(self):
        """Test validating transcript without text."""
        msg = TranscriptMessage(
            text="",
            confidence=0.95,
            session_id="test-123"
        )
        
        is_valid, error = validate_message(msg)
        
        assert is_valid is False
        assert "requires text" in error
    
    def test_invalid_transcript_no_session(self):
        """Test validating transcript without session."""
        msg = TranscriptMessage(
            text="hello",
            confidence=0.95,
            session_id=""
        )
        
        is_valid, error = validate_message(msg)
        
        assert is_valid is False
        assert "requires session_id" in error
    
    def test_invalid_confidence_range(self):
        """Test validating transcript with invalid confidence."""
        msg = TranscriptMessage(
            text="hello",
            confidence=1.5,  # > 1.0
            session_id="test-123"
        )
        
        is_valid, error = validate_message(msg)
        
        assert is_valid is False
        assert "confidence" in error.lower()
    
    def test_valid_response(self):
        """Test validating valid response."""
        msg = ResponseMessage(
            text="I heard you",
            session_id="test-123"
        )
        
        is_valid, error = validate_message(msg)
        
        assert is_valid is True
        assert error is None
    
    def test_valid_error(self):
        """Test validating valid error."""
        msg = ErrorMessage(
            message="Something went wrong",
            code="ERR_001"
        )
        
        is_valid, error = validate_message(msg)
        
        assert is_valid is True
        assert error is None
    
    def test_invalid_error_no_message(self):
        """Test validating error without message."""
        msg = ErrorMessage(message="")
        
        is_valid, error = validate_message(msg)
        
        assert is_valid is False
        assert "requires message" in error


class TestUtilityFunctions:
    """Tests for utility functions."""
    
    def test_generate_session_id(self):
        """Test session ID generation."""
        session_id = generate_session_id()
        
        assert session_id is not None
        assert len(session_id) == 36  # UUID format
        assert "-" in session_id  # UUID has dashes
    
    def test_generate_unique_session_ids(self):
        """Test that generated session IDs are unique."""
        ids = [generate_session_id() for _ in range(100)]
        
        assert len(set(ids)) == 100  # All unique
    
    def test_create_transcript_helper(self):
        """Test transcript helper function."""
        msg = create_transcript(
            text="hello world",
            confidence=0.95
        )
        
        assert isinstance(msg, TranscriptMessage)
        assert msg.text == "hello world"
        assert msg.confidence == 0.95
        assert msg.session_id is not None
    
    def test_create_transcript_with_session(self):
        """Test transcript helper with session ID."""
        msg = create_transcript(
            text="hello",
            confidence=0.9,
            session_id="my-session"
        )
        
        assert msg.session_id == "my-session"
    
    def test_create_wake_word_helper(self):
        """Test wake word helper function."""
        msg = create_wake_word(wake_word="assistant")
        
        assert isinstance(msg, WakeWordMessage)
        assert msg.wake_word == "assistant"
        assert msg.confidence == 1.0
    
    def test_create_response_helper(self):
        """Test response helper function."""
        msg = create_response(
            text="I can help",
            session_id="test-123"
        )
        
        assert isinstance(msg, ResponseMessage)
        assert msg.text == "I can help"
        assert msg.session_id == "test-123"
        assert msg.speak is True
    
    def test_create_error_helper(self):
        """Test error helper function."""
        msg = create_error(
            message="Connection failed",
            code="WS_ERROR",
            recoverable=True
        )
        
        assert isinstance(msg, ErrorMessage)
        assert msg.message == "Connection failed"
        assert msg.code == "WS_ERROR"
        assert msg.recoverable is True


class TestMessageRegistry:
    """Tests for message type registry."""
    
    def test_all_types_registered(self):
        """Test that all message types are registered."""
        expected_types = [
            "transcript",
            "wake_word_detected",
            "session_start",
            "session_end",
            "tts_start",
            "tts_end",
            "error",
            "response",
            "acknowledgement",
            "control",
            "config_update",
            "ping",
            "pong",
            "hello",
        ]
        
        for msg_type in expected_types:
            assert msg_type in MESSAGE_TYPES, f"Missing type: {msg_type}"
    
    def test_message_types_are_classes(self):
        """Test that all registered types are classes."""
        for msg_type, cls in MESSAGE_TYPES.items():
            assert isinstance(cls, type), f"Type for {msg_type} is not a class"
            assert issubclass(cls, BaseMessage), f"Type for {msg_type} does not extend BaseMessage"


class TestLogMessageSummary:
    """Tests for message summary logging."""
    
    def test_summary_counts(self):
        """Test that summary counts messages correctly."""
        messages = [
            TranscriptMessage(text="a", confidence=0.9, session_id="1"),
            TranscriptMessage(text="b", confidence=0.8, session_id="2"),
            WakeWordMessage(session_id="3"),
            ResponseMessage(text="c", session_id="1"),
            ResponseMessage(text="d", session_id="2"),
            ResponseMessage(text="e", session_id="3"),
        ]
        
        summary = log_message_summary(messages)
        
        assert summary["transcript"] == 2
        assert summary["wake_word_detected"] == 1
        assert summary["response"] == 3
    
    def test_empty_summary(self):
        """Test summary of empty message list."""
        summary = log_message_summary([])
        
        assert summary == {}


class TestProtocolVersion:
    """Tests for protocol versioning."""
    
    def test_protocol_version_exists(self):
        """Test that protocol version is defined."""
        assert PROTOCOL_VERSION is not None
        assert isinstance(PROTOCOL_VERSION, str)
    
    def test_protocol_version_format(self):
        """Test protocol version format."""
        import re
        # Should be semver format
        assert re.match(r"\d+\.\d+\.\d+", PROTOCOL_VERSION)
    
    def test_hello_includes_version(self):
        """Test that hello message includes version."""
        msg = HelloMessage()
        
        assert msg.version == PROTOCOL_VERSION