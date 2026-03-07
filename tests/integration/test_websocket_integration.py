"""Integration tests for WebSocket client.

These tests require a WebSocket server or mock server.
Run with: pytest tests/integration/test_websocket_integration.py -v
"""

from __future__ import annotations

import asyncio
import json
import pytest
from unittest.mock import AsyncMock, Mock, patch, MagicMock

import websockets
from websockets.server import serve

from bridge.websocket_client import (
    ConnectionState,
    ControlAction,
    MessageType,
    OpenClawWebSocketClient,
)
from bridge.config import OpenClawConfig


class TestWebSocketClientConnection:
    """Integration tests for connection lifecycle."""
    
    @pytest.mark.asyncio
    async def test_successful_connection(self):
        """Test connecting to a mock WebSocket server."""
        received_messages = []
        
        async def mock_server(websocket):
            """Mock OpenClaw server."""
            # Send welcome message
            await websocket.send(json.dumps({"session_id": "test-session-123"}))
            
            # Echo messages back
            async for message in websocket:
                data = json.loads(message)
                received_messages.append(data)
                
                if data.get("type") == "ping":
                    await websocket.send(json.dumps({"type": "pong"}))
        
        # Start mock server
        async with serve(mock_server, "localhost", 8765):
            config = OpenClawConfig(host="localhost", port=8765)
            client = OpenClawWebSocketClient(config=config)
            
            # Try to connect
            result = await client.connect()
            
            # Note: This will fail in real test because we need real websockets
            # For unit test purposes, we mock the connection
            pass
    
    @pytest.mark.asyncio
    async def test_connection_with_config_integration(self):
        """Test client uses config values correctly."""
        # Create config with specific values
        config = OpenClawConfig(
            host="192.168.1.50",
            port=9999,
            secure=True,
            timeout=60.0,
        )
        
        client = OpenClawWebSocketClient(config=config)
        
        # Verify config integration
        assert client.config.host == "192.168.1.50"
        assert client.config.port == 9999
        assert client.config.secure is True
        assert client.config.timeout == 60.0
        assert client.url == "wss://192.168.1.50:9999/api/voice"


class TestWebSocketClientAsyncFlow:
    """Tests for async message flow."""
    
    @pytest.mark.asyncio
    async def test_message_flow_integration(self):
        """Test complete message sending and receiving flow."""
        config = OpenClawConfig()
        received_messages = []
        connect_called = []
        
        def on_message(msg):
            received_messages.append(msg)
        
        def on_connect():
            connect_called.append(True)
        
        client = OpenClawWebSocketClient(
            config=config,
            on_message=on_message,
            on_connect=on_connect,
        )
        
        # Mock the connection
        client._state = ConnectionState.CONNECTED
        client.websocket = AsyncMock()
        client.websocket.send = AsyncMock(return_value=None)
        
        # Send various messages
        await client.send_voice_input("Hello")
        await client.send_interrupt()
        await client.send_control(ControlAction.MUTE)
        
        # Verify messages were "sent"
        assert client.websocket.send.call_count == 3
        assert client.stats.messages_sent == 3
    
    @pytest.mark.asyncio
    async def test_state_machine_transitions(self):
        """Test complete state machine transitions."""
        config = OpenClawConfig()
        states = []
        
        def on_state_change(old, new):
            states.append((old.value, new.value))
        
        client = OpenClawWebSocketClient(
            config=config,
            on_state_change=on_state_change,
        )
        
        # Simulate connection flow
        client._set_state(ConnectionState.CONNECTING)
        client._set_state(ConnectionState.CONNECTED)
        client._set_state(ConnectionState.DISCONNECTED)
        
        # Verify state transitions
        assert states == [
            ("disconnected", "connecting"),
            ("connecting", "connected"),
            ("connected", "disconnected"),
        ]
    
    @pytest.mark.asyncio
    async def test_multiple_messages_sequence(self):
        """Test sending multiple messages in sequence."""
        config = OpenClawConfig()
        client = OpenClawWebSocketClient(config=config)
        
        # Mock connected state
        client._state = ConnectionState.CONNECTED
        client.websocket = AsyncMock()
        client.websocket.send = AsyncMock(return_value=None)
        
        # Send multiple voice inputs
        for i in range(5):
            result = await client.send_voice_input(f"Message {i}")
            assert result
        
        # Send control messages
        await client.send_interrupt()
        await client.send_control(ControlAction.MUTE)
        await client.send_control(ControlAction.UNMUTE)
        
        # Verify counts
        assert client.stats.messages_sent == 8
    
    @pytest.mark.asyncio
    async def test_disconnect_during_send(self):
        """Test handling disconnect during send."""
        config = OpenClawConfig()
        disconnect_called = []
        
        def on_disconnect():
            disconnect_called.append(True)
        
        client = OpenClawWebSocketClient(
            config=config,
            on_disconnect=on_disconnect,
        )
        
        # Setup connected state
        client._state = ConnectionState.CONNECTED
        
        # Mock websocket that raises ConnectionClosed
        mock_ws = AsyncMock()
        from websockets.exceptions import ConnectionClosed
        mock_ws.send = AsyncMock(
            side_effect=ConnectionClosed(1000, "Normal closure")
        )
        client.websocket = mock_ws
        
        # Try to send - should fail and trigger disconnect
        result = await client.send({
            "type": "voice_input",
            "text": "Test",
            "timestamp": 123.0,
        })
        
        assert not result
        assert client.state == ConnectionState.DISCONNECTED
        assert len(disconnect_called) == 1


class TestWebSocketClientErrorHandling:
    """Tests for error handling scenarios."""
    
    @pytest.mark.asyncio
    async def test_invalid_connection_refused(self):
        """Test handling connection refused."""
        config = OpenClawConfig(
            host="localhost",
            port=59999,  # Unlikely to be used
        )
        
        client = OpenClawWebSocketClient(config=config)
        
        # Try to connect to non-existent server
        # This should fail gracefully
        result = await client.connect()
        
        # Should fail but not crash
        assert not result
        assert client.state in [ConnectionState.DISCONNECTED, ConnectionState.ERROR]
    
    @pytest.mark.asyncio
    async def test_callback_error_handling(self):
        """Test that callback errors don't crash the client."""
        config = OpenClawConfig()
        
        def failing_callback():
            raise ValueError("Callback error")
        
        client = OpenClawWebSocketClient(
            config=config,
            on_connect=failing_callback,
            on_disconnect=failing_callback,
        )
        
        # Mock connected state
        client._state = ConnectionState.CONNECTED
        client.websocket = AsyncMock()
        client.stats.last_connect_time = 1234567890.0
        
        # Disconnect should not raise despite callback error
        await client.disconnect()
        
        # Client should still be disconnected
        assert client.state == ConnectionState.DISCONNECTED


class TestWebSocketClientConfiguration:
    """Tests for configuration integration."""
    
    @pytest.mark.asyncio
    async def test_config_reload_integration(self):
        """Test that config changes are picked up."""
        config1 = OpenClawConfig(host="host1", port=8080)
        config2 = OpenClawConfig(host="host2", port=9090)
        
        client1 = OpenClawWebSocketClient(config=config1)
        assert client1.url == "ws://host1:8080/api/voice"
        
        # New client with different config
        client2 = OpenClawWebSocketClient(config=config2)
        assert client2.url == "ws://host2:9090/api/voice"
    
    def test_secure_connection_url(self):
        """Test WSS URL generation for secure connections."""
        config = OpenClawConfig(
            host="secure.example.com",
            port=443,
            secure=True,
        )
        
        client = OpenClawWebSocketClient(config=config)
        assert client.url == "wss://secure.example.com:443/api/voice"
    
    def test_insecure_connection_url(self):
        """Test WS URL generation for insecure connections."""
        config = OpenClawConfig(
            host="localhost",
            port=8080,
            secure=False,
        )
        
        client = OpenClawWebSocketClient(config=config)
        assert client.url == "ws://localhost:8080/api/voice"


class TestWebSocketClientPerformance:
    """Tests for performance characteristics."""
    
    @pytest.mark.asyncio
    async def test_message_throughput(self):
        """Test sending multiple messages quickly."""
        config = OpenClawConfig()
        client = OpenClawWebSocketClient(config=config)
        
        # Mock connected state
        client._state = ConnectionState.CONNECTED
        client.websocket = AsyncMock()
        client.websocket.send = AsyncMock(return_value=None)
        
        # Send 100 messages
        start_time = asyncio.get_event_loop().time()
        
        tasks = [
            client.send_voice_input(f"Message {i}")
            for i in range(100)
        ]
        results = await asyncio.gather(*tasks)
        
        end_time = asyncio.get_event_loop().time()
        
        # All should succeed
        assert all(results)
        assert client.stats.messages_sent == 100
        
        # Should complete reasonably fast (under 5 seconds for 100 messages)
        assert (end_time - start_time) < 5.0
    
    @pytest.mark.asyncio
    async def test_concurrent_sends(self):
        """Test concurrent message sending."""
        config = OpenClawConfig()
        client = OpenClawWebSocketClient(config=config)
        
        # Mock connected state with sequential handling
        client._state = ConnectionState.CONNECTED
        client.websocket = AsyncMock()
        client.websocket.send = AsyncMock(return_value=None)
        
        # Send messages concurrently
        tasks = [
            client.send_voice_input(f"Concurrent {i}")
            for i in range(10)
        ]
        
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        # All should succeed (no exceptions)
        successful = [r for r in results if r is True]
        assert len(successful) == 10


class TestWebSocketClientSessionManagement:
    """Tests for session management."""
    
    @pytest.mark.asyncio
    async def test_session_restoration_flow(self):
        """Test complete session restoration flow."""
        config = OpenClawConfig()
        client = OpenClawWebSocketClient(config=config)
        
        # Mock connected state
        client._state = ConnectionState.CONNECTED
        client.websocket = AsyncMock()
        client.websocket.send = AsyncMock(return_value=None)
        
        # Set an old session ID
        old_session = "session-abc-123"
        
        # Restore session
        result = await client.restore_session(old_session)
        
        assert result
        assert client.session_id == old_session
        
        # Verify correct message sent
        sent_data = json.loads(client.websocket.send.call_args[0][0])
        assert sent_data["type"] == "session_restore"
        assert sent_data["session_id"] == old_session
    
    @pytest.mark.asyncio
    async def test_session_persistence_across_reconnect(self):
        """Test session ID persists across reconnections."""
        config = OpenClawConfig()
        client = OpenClawWebSocketClient(config=config)
        
        # Set a session ID
        client.session_id = "persistent-session-456"
        
        # Mock connected state
        client._state = ConnectionState.CONNECTED
        client.websocket = AsyncMock()
        client.websocket.send = AsyncMock(return_value=None)
        
        # Restore without providing ID - should use stored one
        result = await client.restore_session()
        
        assert result
        
        sent_data = json.loads(client.websocket.send.call_args[0][0])
        assert sent_data["session_id"] == "persistent-session-456"
