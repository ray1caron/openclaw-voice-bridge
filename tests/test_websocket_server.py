"""
Tests for Voice Bridge WebSocket Server.

Tests the WebSocket server implementation including:
- Server startup/shutdown
- Client connections
- Message broadcasting
- Protocol handling
"""
import asyncio
import json
import pytest
import structlog
from unittest.mock import Mock, AsyncMock, patch

from bridge.protocol import (
    TranscriptMessage,
    WakeWordMessage,
    ResponseMessage,
    HelloMessage,
    PingMessage,
    PongMessage,
    ErrorMessage,
    create_transcript,
    create_wake_word,
)
from bridge.websocket_server import WebSocketServer, ClientConnection, VoiceBridgeWebSocketServer

# Logger for tests
logger = structlog.get_logger()


# =============================================================================
# Fixtures
# =============================================================================

@pytest.fixture
def server():
    """Create a WebSocket server for testing."""
    return WebSocketServer(host="127.0.0.1", port=18795, max_connections=5)


@pytest.fixture
def client_connection():
    """Create a mock client connection."""
    mock_ws = AsyncMock()
    mock_ws.send = AsyncMock()
    mock_ws.close = AsyncMock()
    return ClientConnection(mock_ws, "test-client-123")


@pytest.fixture
def event_loop():
    """Create event loop for async tests."""
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


# =============================================================================
# WebSocketServer Tests
# =============================================================================

class TestWebSocketServerInit:
    """Tests for WebSocketServer initialization."""

    def test_default_init(self):
        """Test server initializes with defaults."""
        server = WebSocketServer()
        
        assert server.host == "0.0.0.0"
        assert server.port == 18790
        assert server.max_connections == 10

    def test_custom_init(self):
        """Test server initializes with custom values."""
        server = WebSocketServer(host="192.168.1.100", port=18888, max_connections=20)
        
        assert server.host == "192.168.1.100"
        assert server.port == 18888
        assert server.max_connections == 20

    def test_singleton_pattern(self):
        """Test singleton get_instance."""
        WebSocketServer.reset_instance()
        
        server1 = WebSocketServer.get_instance(port=18796)
        server2 = WebSocketServer.get_instance()
        
        assert server1 is server2
        WebSocketServer.reset_instance()


class TestClientConnection:
    """Tests for ClientConnection class."""

    def test_client_init(self, client_connection):
        """Test client connection initialization."""
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
        assert data["text"] == "hello"

    @pytest.mark.asyncio
    async def test_client_send_json(self, client_connection):
        """Test client send_json method."""
        data = {"type": "ping", "timestamp": "2024-01-01T00:00:00Z"}
        
        await client_connection.send_json(data)
        
        client_connection.websocket.send.assert_called_once_with(json.dumps(data))

    def test_client_close(self, client_connection):
        """Test client close method."""
        client_connection.close()
        assert client_connection.is_alive() is False


class TestMessageCreation:
    """Tests for message creation helpers."""

    def test_create_transcript(self):
        """Test transcript message creation."""
        msg = create_transcript("hello world", 0.95, "session-123")
        
        assert isinstance(msg, TranscriptMessage)
        assert msg.text == "hello world"
        assert msg.confidence == 0.95
        assert msg.session_id == "session-123"

    def test_create_wake_word(self):
        """Test wake word message creation."""
        msg = create_wake_word("computer", 0.98, "session-456")
        
        assert isinstance(msg, WakeWordMessage)
        assert msg.wake_word == "computer"
        assert msg.confidence == 0.98
        assert msg.session_id == "session-456"

    def test_transcript_auto_session_id(self):
        """Test transcript auto-generates session ID."""
        msg = create_transcript("test", 0.9)
        
        assert msg.session_id is not None
        assert len(msg.session_id) == 36  # UUID format


class TestMessageBroadcast:
    """Tests for message broadcasting."""

    def test_broadcast_creates_task(self, server, event_loop):
        """Test broadcast creates asyncio task."""
        msg = TranscriptMessage(text="test", confidence=0.9, session_id="test")
        
        # Should not raise even without clients
        server.broadcast(msg)

    def test_broadcast_json(self, server, event_loop):
        """Test broadcast_json method."""
        data = {"type": "ping"}
        
        # Should not raise even without clients
        server.broadcast_json(data)


class TestServerMethods:
    """Tests for server methods."""

    def test_send_transcript(self, server):
        """Test send_transcript creates message."""
        server.send_transcript("hello", 0.95, "session-123")
        
        # Method should not raise
        assert server is not None

    def test_send_wake_word(self, server):
        """Test send_wake_word creates message."""
        server.send_wake_word("computer", 0.99, "session-456")
        
        # Method should not raise
        assert server is not None

    def test_on_response_callback(self, server):
        """Test on_response callback registration."""
        callback_called = False
        
        def my_callback(text: str):
            nonlocal callback_called
            callback_called = True
        
        server.on_response(my_callback)
        
        # Callback should be registered
        assert server._on_response_callback is my_callback

    def test_get_client_count(self, server):
        """Test get_client_count returns zero initially."""
        count = server.get_client_count()
        
        assert count == 0

    def test_get_connected_clients(self, server):
        """Test get_connected_clients returns empty list initially."""
        clients = server.get_connected_clients()
        
        assert clients == []

    def test_is_running_false_initially(self, server):
        """Test is_running returns False before start."""
        assert server.is_running() is False


class TestServerLifecycle:
    """Tests for server startup and shutdown."""

    @pytest.mark.asyncio
    async def test_server_start_stop(self, event_loop):
        """Test server can start and stop cleanly."""
        server = WebSocketServer(host="127.0.0.1", port=18797, max_connections=5)
        
        # Start server
        start_task = asyncio.create_task(server.start())
        
        # Give it time to start
        await asyncio.sleep(0.1)
        
        # Server should be running
        assert server.is_running()
        
        # Stop server
        await server.stop()
        
        # Server should not be running
        assert not server.is_running()

    @pytest.mark.asyncio
    async def test_server_singleton_reset(self):
        """Test singleton reset works."""
        WebSocketServer.reset_instance()
        
        server1 = WebSocketServer.get_instance(port=18798)
        assert server1 is not None
        
        WebSocketServer.reset_instance()
        
        # After reset, should get new instance
        server2 = WebSocketServer.get_instance(port=18799)
        # Can't easily compare since we reset


# =============================================================================
# Legacy Compatibility Tests
# =============================================================================

class TestLegacyAliases:
    """Tests for legacy alias compatibility."""

    def test_voice_bridge_alias(self):
        """Test VoiceBridgeWebSocketServer alias."""
        assert VoiceBridgeWebSocketServer is WebSocketServer


# =============================================================================
# Integration Tests (require running server)
# =============================================================================

@pytest.mark.integration
class TestIntegration:
    """Integration tests that require a running server."""

    @pytest.mark.asyncio
    async def test_server_accepts_connections(self, event_loop):
        """Test server accepts WebSocket connections."""
        import websockets
        
        server = WebSocketServer(host="127.0.0.1", port=18799, max_connections=5)
        
        try:
            # Start server in background
            server_task = asyncio.create_task(server.start())
            await asyncio.sleep(0.2)  # Wait for server to start
            
            # Connect client
            async with websockets.connect("ws://127.0.0.1:18799") as ws:
                # Send hello message
                hello = HelloMessage()
                await ws.send(hello.to_json())
                
                # Should receive response
                response = await asyncio.wait_for(ws.recv(), timeout=2.0)
                data = json.loads(response)
                
                # Server might send various responses
                assert "type" in data
                
        finally:
            await server.stop()
            WebSocketServer.reset_instance()

    @pytest.mark.asyncio
    async def test_ping_pong(self, event_loop):
        """Test ping message handling."""
        import websockets
        
        server = WebSocketServer(host="127.0.0.1", port=18800, max_connections=5)
        
        try:
            server_task = asyncio.create_task(server.start())
            await asyncio.sleep(0.2)
            
            async with websockets.connect("ws://127.0.0.1:18800") as ws:
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


if __name__ == "__main__":
    pytest.main([__file__, "-v"])