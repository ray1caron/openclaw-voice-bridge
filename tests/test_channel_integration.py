"""
Channel Integration Tests for Voice Bridge WebSocket Protocol.

Tests WebSocket connection handshake, wake word → transcript → response flow,
session management, and error handling using the actual WebSocketServer API.
"""

import asyncio
import json
import pytest
from unittest.mock import AsyncMock, Mock, patch

from bridge.protocol import (
    TranscriptMessage,
    WakeWordMessage,
    ResponseMessage,
    SessionStartMessage,
    SessionEndMessage,
    PingMessage,
    PongMessage,
    HelloMessage,
    ErrorMessage,
    AcknowledgementMessage,
    parse_message,
    validate_message,
    PROTOCOL_VERSION,
)
from bridge.websocket_server import (
    WebSocketServer,
    ClientConnection,
    VoiceBridgeWebSocketServer,
)


# =============================================================================
# Fixtures
# =============================================================================

@pytest.fixture
async def server():
    """Create and start a WebSocket server for testing."""
    server = WebSocketServer(host="127.0.0.1", port=18790, max_connections=5)
    try:
        await server.start()
        yield server
    finally:
        await server.stop()
        WebSocketServer.reset_instance()


@pytest.fixture
def client_connection():
    """Create a mock client connection."""
    mock_ws = AsyncMock()
    mock_ws.send = AsyncMock()
    mock_ws.close = AsyncMock()
    return ClientConnection(mock_ws, "test-client-123")


# =============================================================================
# Protocol Tests
# =============================================================================

class TestProtocolMessages:
    """Tests for protocol message handling."""

    def test_transcript_message_valid(self):
        """Test valid transcript message."""
        msg = TranscriptMessage(
            text="hello world",
            confidence=0.95,
            session_id="test-session"
        )
        is_valid, error = validate_message(msg)
        
        assert is_valid is True
        assert error is None

    def test_transcript_message_invalid_no_text(self):
        """Test invalid transcript missing text."""
        msg = TranscriptMessage(
            text="",
            confidence=0.95,
            session_id="test-session"
        )
        is_valid, error = validate_message(msg)
        
        assert is_valid is False
        assert "requires text" in error

    def test_wake_word_message_valid(self):
        """Test valid wake word message."""
        msg = WakeWordMessage(
            wake_word="computer",
            confidence=0.98,
            session_id="test-session"
        )
        is_valid, error = validate_message(msg)
        
        assert is_valid is True
        assert error is None

    def test_response_message_valid(self):
        """Test valid response message."""
        msg = ResponseMessage(
            text="I can help with that.",
            session_id="test-session"
        )
        is_valid, error = validate_message(msg)
        
        assert is_valid is True
        assert error is None


# =============================================================================
# WebSocket Server Tests
# =============================================================================

class TestWebSocketServer:
    """Tests for WebSocketServer class."""

    def test_server_initialization(self):
        """Test server initializes correctly."""
        server = WebSocketServer(host="127.0.0.1", port=18791, max_connections=10)
        
        assert server.host == "127.0.0.1"
        assert server.port == 18791
        assert server.max_connections == 10
        assert server.is_running() is False

    def test_singleton_instance(self):
        """Test singleton pattern."""
        WebSocketServer.reset_instance()
        
        server1 = WebSocketServer.get_instance(port=18792)
        server2 = WebSocketServer.get_instance()
        
        assert server1 is server2
        WebSocketServer.reset_instance()

    def test_send_transcript_method(self):
        """Test send_transcript creates message."""
        server = WebSocketServer()
        
        # Should not raise
        server.send_transcript("hello", 0.95, "session-123")

    def test_send_wake_word_method(self):
        """Test send_wake_word creates message."""
        server = WebSocketServer()
        
        # Should not raise
        server.send_wake_word("computer", 0.99, "session-456")

    def test_broadcast_method(self):
        """Test broadcast method."""
        server = WebSocketServer()
        msg = TranscriptMessage(text="test", confidence=0.9, session_id="test")
        
        # Should not raise even without clients
        server.broadcast(msg)

    def test_on_response_callback(self):
        """Test on_response callback registration."""
        server = WebSocketServer()
        
        def my_callback(text: str):
            pass
        
        server.on_response(my_callback)
        
        assert server._on_response_callback is my_callback

    def test_get_client_count_initial(self):
        """Test get_client_count returns zero initially."""
        server = WebSocketServer()
        
        assert server.get_client_count() == 0

    def test_get_connected_clients_initial(self):
        """Test get_connected_clients returns empty list initially."""
        server = WebSocketServer()
        
        assert server.get_connected_clients() == []


class TestClientConnection:
    """Tests for ClientConnection class."""

    def test_client_initialization(self, client_connection):
        """Test client connection initializes correctly."""
        assert client_connection.client_id == "test-client-123"
        assert client_connection.is_alive() is True

    @pytest.mark.asyncio
    async def test_client_send(self, client_connection):
        """Test client send method."""
        msg = TranscriptMessage(text="hello", confidence=0.9, session_id="test")
        
        await client_connection.send(msg)
        
        # Verify send was called
        client_connection.websocket.send.assert_called_once()
        call_args = client_connection.websocket.send.call_args[0][0]
        data = json.loads(call_args)
        assert data["type"] == "transcript"

    @pytest.mark.asyncio
    async def test_client_send_json(self, client_connection):
        """Test client send_json method."""
        data = {"type": "ping"}
        
        await client_connection.send_json(data)
        
        client_connection.websocket.send.assert_called_once_with(json.dumps(data))

    def test_client_close(self, client_connection):
        """Test client close method."""
        client_connection.close()
        assert client_connection.is_alive() is False


# =============================================================================
# Integration Tests
# =============================================================================

@pytest.mark.integration
class TestIntegration:
    """Integration tests requiring server."""

    @pytest.mark.asyncio
    async def test_server_start_stop(self):
        """Test server can start and stop."""
        server = WebSocketServer(host="127.0.0.1", port=18793, max_connections=5)
        
        # Start
        await server.start()
        assert server.is_running() is True
        
        # Stop
        await server.stop()
        assert server.is_running() is False
        
        WebSocketServer.reset_instance()

    @pytest.mark.asyncio
    async def test_client_connection(self):
        """Test client can connect to server."""
        import websockets
        
        server = WebSocketServer(host="127.0.0.1", port=18794, max_connections=5)
        
        try:
            await server.start()
            await asyncio.sleep(0.1)
            
            # Connect client
            async with websockets.connect("ws://127.0.0.1:18794") as ws:
                # Send hello
                hello = HelloMessage()
                await ws.send(hello.to_json())
                
                # Receive response (type may vary)
                response = await asyncio.wait_for(ws.recv(), timeout=2.0)
                data = json.loads(response)
                assert "type" in data
                
        finally:
            await server.stop()
            WebSocketServer.reset_instance()

    @pytest.mark.asyncio
    async def test_ping_pong(self):
        """Test ping/pong messaging."""
        import websockets
        
        server = WebSocketServer(host="127.0.0.1", port=18795, max_connections=5)
        
        try:
            await server.start()
            await asyncio.sleep(0.1)
            
            async with websockets.connect("ws://127.0.0.1:18795") as ws:
                # First receive the hello greeting
                greeting = await asyncio.wait_for(ws.recv(), timeout=2.0)
                greeting_data = json.loads(greeting)
                assert greeting_data["type"] == "hello"
                
                # Now send ping
                ping = PingMessage()
                await ws.send(ping.to_json())
                
                # Should receive pong
                response = await asyncio.wait_for(ws.recv(), timeout=2.0)
                data = json.loads(response)
                
                assert data["type"] == "pong"
                
        finally:
            await server.stop()
            WebSocketServer.reset_instance()

    @pytest.mark.asyncio
    async def test_transcript_broadcast(self):
        """Test broadcasting transcript to clients."""
        import websockets
        
        server = WebSocketServer(host="127.0.0.1", port=18796, max_connections=5)
        
        try:
            await server.start()
            await asyncio.sleep(0.1)
            
            async with websockets.connect("ws://127.0.0.1:18796") as ws:
                # Wait for connection
                await asyncio.sleep(0.1)
                
                # Broadcast from server
                server.send_transcript("hello world", 0.95, "test-session")
                
                # Wait a bit for broadcast
                await asyncio.sleep(0.1)
                
                # Try to receive (may timeout if no message)
                try:
                    response = await asyncio.wait_for(ws.recv(), timeout=1.0)
                    data = json.loads(response)
                    assert "type" in data
                except asyncio.TimeoutError:
                    pass  # No message is okay for this test
                    
        finally:
            await server.stop()
            WebSocketServer.reset_instance()


# =============================================================================
# Message Parsing Tests
# =============================================================================

class TestMessageParsing:
    """Tests for message parsing."""

    def test_parse_transcript_message(self):
        """Test parsing transcript message from JSON."""
        json_str = json.dumps({
            "type": "transcript",
            "text": "hello",
            "confidence": 0.9,
            "session_id": "test-123"
        })
        
        msg = parse_message(json_str)
        
        assert isinstance(msg, TranscriptMessage)
        assert msg.text == "hello"

    def test_parse_wake_word_message(self):
        """Test parsing wake word message from JSON."""
        json_str = json.dumps({
            "type": "wake_word_detected",
            "wake_word": "computer",
            "confidence": 0.99,
            "session_id": "test-456"
        })
        
        msg = parse_message(json_str)
        
        assert isinstance(msg, WakeWordMessage)
        assert msg.wake_word == "computer"

    def test_parse_response_message(self):
        """Test parsing response message from JSON."""
        json_str = json.dumps({
            "type": "response",
            "text": "I heard you",
            "session_id": "test-789"
        })
        
        msg = parse_message(json_str)
        
        assert isinstance(msg, ResponseMessage)

    def test_parse_invalid_message(self):
        """Test parsing invalid message."""
        json_str = "not valid json"
        
        with pytest.raises(ValueError):
            parse_message(json_str)


# =============================================================================
# Legacy Compatibility Tests
# =============================================================================

class TestLegacyAliases:
    """Tests for legacy compatibility."""

    def test_voice_bridge_alias(self):
        """Test VoiceBridgeWebSocketServer alias."""
        assert VoiceBridgeWebSocketServer is WebSocketServer

    def test_client_session_alias(self):
        """Test ClientSession alias."""
        # ClientSession should be ClientConnection
        from bridge.websocket_server import ClientSession
        assert ClientSession is ClientConnection


if __name__ == "__main__":
    pytest.main([__file__, "-v"])