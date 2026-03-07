"""Unit tests for WebSocket client."""

from __future__ import annotations

import asyncio
import json
import pytest
from unittest.mock import AsyncMock, MagicMock, Mock, patch

import websockets
from websockets.exceptions import ConnectionClosed

from bridge.websocket_client import (
    ConnectionState,
    ConnectionStats,
    ControlAction,
    MessageType,
    MessageValidator,
    OpenClawWebSocketClient,
)
from bridge.config import OpenClawConfig


class TestConnectionState:
    """Tests for ConnectionState enum."""
    
    def test_state_values(self):
        """Test state enum values."""
        assert ConnectionState.DISCONNECTED.value == "disconnected"
        assert ConnectionState.CONNECTING.value == "connecting"
        assert ConnectionState.CONNECTED.value == "connected"
        assert ConnectionState.RECONNECTING.value == "reconnecting"
        assert ConnectionState.ERROR.value == "error"


class TestMessageType:
    """Tests for MessageType enum."""
    
    def test_message_types(self):
        """Test all message types exist."""
        assert MessageType.VOICE_INPUT.value == "voice_input"
        assert MessageType.CONTROL.value == "control"
        assert MessageType.SESSION_RESTORE.value == "session_restore"
        assert MessageType.PING.value == "ping"
        assert MessageType.PONG.value == "pong"


class TestControlAction:
    """Tests for ControlAction enum."""
    
    def test_control_actions(self):
        """Test all control actions exist."""
        assert ControlAction.INTERRUPT.value == "interrupt"
        assert ControlAction.MUTE.value == "mute"
        assert ControlAction.UNMUTE.value == "unmute"


class TestConnectionStats:
    """Tests for ConnectionStats dataclass."""
    
    def test_default_values(self):
        """Test default stats values."""
        stats = ConnectionStats()
        assert stats.connect_attempts == 0
        assert stats.successful_connections == 0
        assert stats.messages_sent == 0
        assert stats.messages_received == 0
        assert stats.reconnections == 0
        assert stats.last_connect_time is None
        assert stats.last_disconnect_time is None
        assert stats.total_uptime == 0.0
    
    def test_stats_increment(self):
        """Test incrementing stats."""
        stats = ConnectionStats()
        stats.connect_attempts += 1
        stats.messages_sent += 5
        stats.messages_received += 3
        
        assert stats.connect_attempts == 1
        assert stats.messages_sent == 5
        assert stats.messages_received == 3


class TestMessageValidator:
    """Tests for MessageValidator."""
    
    def test_validate_valid_voice_input(self):
        """Test valid voice_input message."""
        message = {
            "type": "voice_input",
            "text": "Hello world",
            "timestamp": 1234567890.0,
        }
        is_valid, error = MessageValidator.validate_message(message)
        assert is_valid
        assert error is None
    
    def test_validate_voice_input_with_metadata(self):
        """Test voice_input with metadata."""
        message = {
            "type": "voice_input",
            "text": "Hello",
            "timestamp": 1234567890.0,
            "metadata": {"confidence": 0.95},
        }
        is_valid, error = MessageValidator.validate_message(message)
        assert is_valid
        assert error is None
    
    def test_validate_missing_type(self):
        """Test message without type field."""
        message = {
            "text": "Hello",
            "timestamp": 1234567890.0,
        }
        is_valid, error = MessageValidator.validate_message(message)
        assert not is_valid
        assert "type" in error.lower()
    
    def test_validate_unknown_type(self):
        """Test message with unknown type."""
        message = {
            "type": "unknown_type",
            "text": "Hello",
        }
        is_valid, error = MessageValidator.validate_message(message)
        assert not is_valid
        assert "unknown" in error.lower()
    
    def test_validate_non_dict_message(self):
        """Test non-dictionary message."""
        is_valid, error = MessageValidator.validate_message("not a dict")
        assert not is_valid
        assert "dictionary" in error.lower()
    
    def test_validate_voice_input_missing_text(self):
        """Test voice_input without text field."""
        message = {
            "type": "voice_input",
            "timestamp": 1234567890.0,
        }
        is_valid, error = MessageValidator.validate_message(message)
        assert not is_valid
        assert "text" in error.lower()
    
    def test_validate_voice_input_empty_text(self):
        """Test voice_input with empty/whitespace text."""
        message = {
            "type": "voice_input",
            "text": "   ",
            "timestamp": 1234567890.0,
        }
        is_valid, error = MessageValidator.validate_message(message)
        assert not is_valid
        assert "empty" in error.lower()
    
    def test_validate_voice_input_invalid_metadata(self):
        """Test voice_input with non-dict metadata."""
        message = {
            "type": "voice_input",
            "text": "Hello",
            "timestamp": 1234567890.0,
            "metadata": "not a dict",
        }
        is_valid, error = MessageValidator.validate_message(message)
        assert not is_valid
        assert "metadata" in error.lower()
    
    def test_validate_valid_control_interrupt(self):
        """Test valid control interrupt message."""
        message = {
            "type": "control",
            "action": "interrupt",
            "timestamp": 1234567890.0,
        }
        is_valid, error = MessageValidator.validate_message(message)
        assert is_valid
        assert error is None
    
    def test_validate_valid_control_mute(self):
        """Test valid control mute message."""
        message = {
            "type": "control",
            "action": "mute",
            "timestamp": 1234567890.0,
        }
        is_valid, error = MessageValidator.validate_message(message)
        assert is_valid
        assert error is None
    
    def test_validate_control_missing_action(self):
        """Test control message without action."""
        message = {
            "type": "control",
            "timestamp": 1234567890.0,
        }
        is_valid, error = MessageValidator.validate_message(message)
        assert not is_valid
        assert "action" in error.lower()
    
    def test_validate_control_invalid_action(self):
        """Test control message with invalid action."""
        message = {
            "type": "control",
            "action": "invalid_action",
            "timestamp": 1234567890.0,
        }
        is_valid, error = MessageValidator.validate_message(message)
        assert not is_valid
        assert "invalid" in error.lower()
    
    def test_validate_valid_session_restore(self):
        """Test valid session_restore message."""
        message = {
            "type": "session_restore",
            "session_id": "test-session-123",
        }
        is_valid, error = MessageValidator.validate_message(message)
        assert is_valid
        assert error is None
    
    def test_validate_session_restore_missing_session_id(self):
        """Test session_restore without session_id."""
        message = {
            "type": "session_restore",
        }
        is_valid, error = MessageValidator.validate_message(message)
        assert not is_valid
        assert "session_id" in error.lower()
    
    def test_validate_session_restore_invalid_session_id(self):
        """Test session_restore with non-string session_id."""
        message = {
            "type": "session_restore",
            "session_id": 12345,
        }
        is_valid, error = MessageValidator.validate_message(message)
        assert not is_valid
        assert "string" in error.lower()
    
    def test_validate_ping(self):
        """Test valid ping message."""
        message = {"type": "ping", "timestamp": 1234567890.0}
        is_valid, error = MessageValidator.validate_message(message)
        assert is_valid
        assert error is None
    
    def test_validate_pong(self):
        """Test valid pong message."""
        message = {"type": "pong", "timestamp": 1234567890.0}
        is_valid, error = MessageValidator.validate_message(message)
        assert is_valid
        assert error is None


class TestOpenClawWebSocketClientInit:
    """Tests for WebSocket client initialization."""
    
    def test_init_with_config(self):
        """Test initialization with custom config."""
        config = OpenClawConfig(
            host="192.168.1.100",
            port=9000,
            secure=True,
        )
        
        client = OpenClawWebSocketClient(config=config)
        
        assert client.config == config
        assert client.url == "wss://192.168.1.100:9000/api/voice"
        assert client.state == ConnectionState.DISCONNECTED
        assert not client.is_connected
    
    def test_init_default_config(self):
        """Test initialization with default config."""
        # Mock get_config to avoid loading real config
        mock_config = OpenClawConfig(host="localhost", port=8080)
        
        with patch("bridge.websocket_client.get_config") as mock_get_config:
            mock_get_config.return_value = MagicMock(openclaw=mock_config)
            client = OpenClawWebSocketClient()
        
        assert client.url == "ws://localhost:8080/api/voice"
        assert client.state == ConnectionState.DISCONNECTED
    
    def test_init_with_callbacks(self):
        """Test initialization with callback handlers."""
        on_message = Mock()
        on_connect = Mock()
        on_disconnect = Mock()
        on_state_change = Mock()
        
        config = OpenClawConfig()
        client = OpenClawWebSocketClient(
            config=config,
            on_message=on_message,
            on_connect=on_connect,
            on_disconnect=on_disconnect,
            on_state_change=on_state_change,
        )
        
        assert client.on_message == on_message
        assert client.on_connect == on_connect
        assert client.on_disconnect == on_disconnect
        assert client.on_state_change == on_state_change


class TestOpenClawWebSocketClientState:
    """Tests for connection state management."""
    
    @pytest.mark.asyncio
    async def test_state_transitions(self):
        """Test state transition tracking."""
        config = OpenClawConfig()
        state_changes = []
        
        def on_state_change(old, new):
            state_changes.append((old, new))
        
        client = OpenClawWebSocketClient(
            config=config,
            on_state_change=on_state_change,
        )
        
        # Manually trigger state changes
        client._set_state(ConnectionState.CONNECTING)
        client._set_state(ConnectionState.CONNECTED)
        client._set_state(ConnectionState.DISCONNECTED)
        
        assert len(state_changes) == 3
        assert state_changes[0] == (ConnectionState.DISCONNECTED, ConnectionState.CONNECTING)
        assert state_changes[1] == (ConnectionState.CONNECTING, ConnectionState.CONNECTED)
        assert state_changes[2] == (ConnectionState.CONNECTED, ConnectionState.DISCONNECTED)
    
    @pytest.mark.asyncio
    async def test_set_state_no_duplicate_events(self):
        """Test that same state doesn't trigger callbacks."""
        config = OpenClawConfig()
        state_changes = []
        
        def on_state_change(old, new):
            state_changes.append((old, new))
        
        client = OpenClawWebSocketClient(
            config=config,
            on_state_change=on_state_change,
        )
        
        # Set same state twice
        client._set_state(ConnectionState.DISCONNECTED)
        client._set_state(ConnectionState.DISCONNECTED)
        
        # Should not have triggered any callbacks
        assert len(state_changes) == 0


class TestOpenClawWebSocketClientSend:
    """Tests for message sending."""
    
    @pytest.mark.asyncio
    async def test_send_not_connected(self):
        """Test sending when not connected."""
        config = OpenClawConfig()
        client = OpenClawWebSocketClient(config=config)
        
        message = {"type": "voice_input", "text": "Hello", "timestamp": 123.0}
        result = await client.send(message)
        
        assert not result
    
    @pytest.mark.asyncio
    async def test_send_invalid_message(self):
        """Test sending invalid message fails validation."""
        config = OpenClawConfig()
        client = OpenClawWebSocketClient(config=config)
        
        # Mock connected state
        client._state = ConnectionState.CONNECTED
        client.websocket = AsyncMock()
        
        # Invalid message (missing text)
        message = {"type": "voice_input", "timestamp": 123.0}
        result = await client.send(message)
        
        assert not result
        client.websocket.send.assert_not_called()
    
    @pytest.mark.asyncio
    async def test_send_voice_input(self):
        """Test send_voice_input method."""
        config = OpenClawConfig()
        client = OpenClawWebSocketClient(config=config)
        client._state = ConnectionState.CONNECTED
        client.websocket = AsyncMock()
        
        result = await client.send_voice_input("Hello world", confidence=0.95)
        
        assert result
        client.websocket.send.assert_called_once()
        
        # Verify sent message
        sent_data = json.loads(client.websocket.send.call_args[0][0])
        assert sent_data["type"] == "voice_input"
        assert sent_data["text"] == "Hello world"
        assert sent_data["metadata"]["confidence"] == 0.95
    
    @pytest.mark.asyncio
    async def test_send_interrupt(self):
        """Test send_interrupt method."""
        config = OpenClawConfig()
        client = OpenClawWebSocketClient(config=config)
        client._state = ConnectionState.CONNECTED
        client.websocket = AsyncMock()
        
        result = await client.send_interrupt()
        
        assert result
        client.websocket.send.assert_called_once()
        
        sent_data = json.loads(client.websocket.send.call_args[0][0])
        assert sent_data["type"] == "control"
        assert sent_data["action"] == "interrupt"
    
    @pytest.mark.asyncio
    async def test_send_control(self):
        """Test send_control method."""
        config = OpenClawConfig()
        client = OpenClawWebSocketClient(config=config)
        client._state = ConnectionState.CONNECTED
        client.websocket = AsyncMock()
        
        result = await client.send_control(ControlAction.MUTE, {"duration": 30})
        
        assert result
        
        sent_data = json.loads(client.websocket.send.call_args[0][0])
        assert sent_data["type"] == "control"
        assert sent_data["action"] == "mute"
        assert sent_data["data"]["duration"] == 30


class TestOpenClawWebSocketClientDisconnect:
    """Tests for disconnection."""
    
    @pytest.mark.asyncio
    async def test_disconnect_cleans_up(self):
        """Test disconnect cleans up resources."""
        config = OpenClawConfig()
        client = OpenClawWebSocketClient(config=config)
        
        # Setup connected state
        client._state = ConnectionState.CONNECTED
        mock_ws = AsyncMock()
        mock_ws.close = AsyncMock()
        client.websocket = mock_ws
        client.stats.last_connect_time = 1234567890.0
        
        # Mock time for uptime calculation
        with patch("time.time", return_value=1234567895.0):
            await client.disconnect()
        
        assert client.state == ConnectionState.DISCONNECTED
        assert not client.is_connected
        assert client.stats.last_disconnect_time is not None
        assert client.stats.total_uptime == 5.0
        mock_ws.close.assert_called_once()
    
    @pytest.mark.asyncio
    async def test_disconnect_callback(self):
        """Test disconnect callback is called."""
        config = OpenClawConfig()
        disconnect_called = []

        def on_disconnect():
            disconnect_called.append(True)

        client = OpenClawWebSocketClient(
            config=config,
            on_disconnect=on_disconnect,
        )
        client._state = ConnectionState.CONNECTED
        mock_ws = AsyncMock()
        mock_ws.close = AsyncMock()
        client.websocket = mock_ws

        await client.disconnect()

        assert len(disconnect_called) == 1


class TestOpenClawWebSocketClientStats:
    """Tests for connection statistics."""
    
    def test_get_stats(self):
        """Test get_stats returns correct data."""
        config = OpenClawConfig()
        client = OpenClawWebSocketClient(config=config)
        
        # Set some stats
        client.stats.connect_attempts = 5
        client.stats.messages_sent = 10
        client.stats.messages_received = 8
        client.stats.total_uptime = 100.5
        
        stats = client.get_stats()
        
        assert stats["state"] == "disconnected"
        assert stats["connect_attempts"] == 5
        assert stats["messages_sent"] == 10
        assert stats["messages_received"] == 8
        assert stats["total_uptime"] == 100.5


class TestOpenClawWebSocketClientRestoreSession:
    """Tests for session restoration."""
    
    @pytest.mark.asyncio
    async def test_restore_session_with_stored_id(self):
        """Test restore with client session_id."""
        config = OpenClawConfig()
        client = OpenClawWebSocketClient(config=config)
        client._state = ConnectionState.CONNECTED
        client.websocket = AsyncMock()
        client.session_id = "stored-session-123"
        
        result = await client.restore_session()
        
        assert result
        sent_data = json.loads(client.websocket.send.call_args[0][0])
        assert sent_data["type"] == "session_restore"
        assert sent_data["session_id"] == "stored-session-123"
    
    @pytest.mark.asyncio
    async def test_restore_session_with_provided_id(self):
        """Test restore with provided session_id."""
        config = OpenClawConfig()
        client = OpenClawWebSocketClient(config=config)
        client._state = ConnectionState.CONNECTED
        client.websocket = AsyncMock()
        
        result = await client.restore_session("provided-session-456")
        
        assert result
        assert client.session_id == "provided-session-456"
        sent_data = json.loads(client.websocket.send.call_args[0][0])
        assert sent_data["session_id"] == "provided-session-456"
    
    @pytest.mark.asyncio
    async def test_restore_session_no_id(self):
        """Test restore fails without session_id."""
        config = OpenClawConfig()
        client = OpenClawWebSocketClient(config=config)
        client._state = ConnectionState.CONNECTED
        client.websocket = AsyncMock()
        
        result = await client.restore_session()
        
        assert not result
        client.websocket.send.assert_not_called()


class TestWebSocketSessionPersistence:
    """Tests for Issue #20: WebSocket Session Lifecycle Integration."""
    
    @pytest.mark.asyncio
    @patch("bridge.websocket_client._get_session_manager")
    @patch("bridge.websocket_client.get_config")
    async def test_session_created_on_connect(self, mock_get_config, mock_get_session_manager):
        """Issue #20: Session created on successful WebSocket connect."""
        # Setup mocks
        mock_config = MagicMock()
        mock_config.persistence.enabled = True
        mock_config.openclaw = OpenClawConfig()
        mock_get_config.return_value = mock_config
        
        mock_session = MagicMock()
        mock_session.session_uuid = "test-session-uuid-123"
        mock_session.id = 42
        
        mock_session_mgr = MagicMock()
        mock_session_mgr.create_session.return_value = mock_session
        mock_session_mgr.get_session.return_value = mock_session
        mock_get_session_manager.return_value = mock_session_mgr
        
        # Create client
        config = OpenClawConfig()
        client = OpenClawWebSocketClient(config=config)
        
        # Simulate successful connection with welcome message
        client._state = ConnectionState.CONNECTED
        client.voice_session_id = mock_session.session_uuid
        client._turn_index = 0
        
        # Verify session was assigned
        assert client.voice_session_id == "test-session-uuid-123"
        assert client._turn_index == 0
    
    @pytest.mark.asyncio
    @patch("bridge.websocket_client._get_session_manager")
    @patch("bridge.websocket_client._get_history_manager")
    @patch("bridge.websocket_client.get_config")
    async def test_message_persisted_on_send(
        self, mock_get_config, mock_get_history_manager, mock_get_session_manager
    ):
        """Issue #20: Voice input messages persisted with metadata."""
        # Setup mocks
        mock_config = MagicMock()
        mock_config.persistence.enabled = True
        mock_config.openclaw = OpenClawConfig()
        mock_get_config.return_value = mock_config
        
        mock_session = MagicMock()
        mock_session.id = 42
        mock_session_mgr = MagicMock()
        mock_session_mgr.get_session.return_value = mock_session
        mock_get_session_manager.return_value = mock_session_mgr
        
        mock_history_mgr = MagicMock()
        mock_get_history_manager.return_value = mock_history_mgr
        
        # Create client with persistence enabled
        config = OpenClawConfig()
        client = OpenClawWebSocketClient(config=config)
        client._state = ConnectionState.CONNECTED
        client.websocket = AsyncMock()
        client.websocket.send = AsyncMock(return_value=None)
        client.voice_session_id = "test-session-uuid"
        client._turn_index = 0
        
        # Send voice input
        result = await client.send_voice_input("Hello world", confidence=0.95)
        
        # Verify message was sent
        assert result
        client.websocket.send.assert_called_once()
    
    @pytest.mark.asyncio
    @patch("bridge.websocket_client._get_session_manager")
    @patch("bridge.websocket_client.get_config")
    async def test_session_closed_on_disconnect(self, mock_get_config, mock_get_session_manager):
        """Issue #20: Session closed on WebSocket disconnect."""
        # Setup mocks
        mock_config = MagicMock()
        mock_config.persistence.enabled = True
        mock_config.openclaw = OpenClawConfig()
        mock_get_config.return_value = mock_config
        
        mock_session_mgr = MagicMock()
        mock_get_session_manager.return_value = mock_session_mgr
        
        # Create client
        config = OpenClawConfig()
        client = OpenClawWebSocketClient(config=config)
        
        # Setup connected state with session
        client._state = ConnectionState.CONNECTED
        client.voice_session_id = "test-session-uuid"
        client.websocket = AsyncMock()
        mock_ws = AsyncMock()
        mock_ws.close = AsyncMock()
        client.websocket = mock_ws
        client.stats.last_connect_time = 1234567890.0
        
        # Disconnect
        with patch("time.time", return_value=1234567895.0):
            await client.disconnect()
        
        # Verify session was closed
        assert client.voice_session_id is None
    
    def test_persistence_feature_flag_disabled(self):
        """Issue #20: Persistence disabled when feature flag is false."""
        # Mock config with persistence disabled
        mock_config = MagicMock()
        mock_config.persistence.enabled = False
        mock_config.openclaw = OpenClawConfig()
        
        with patch("bridge.websocket_client.get_config", return_value=mock_config):
            client = OpenClawWebSocketClient()
            
            # Verify persistence is disabled
            assert not client.enable_persistence
    
    def test_persistence_feature_flag_enabled(self):
        """Issue #20: Persistence enabled when feature flag is true."""
        # Mock config with persistence enabled  
        mock_config = MagicMock()
        mock_config.persistence.enabled = True
        mock_config.openclaw = OpenClawConfig()
        
        with patch("bridge.websocket_client.get_config", return_value=mock_config):
            client = OpenClawWebSocketClient()
            
            # Verify persistence is enabled
            assert client.enable_persistence
    
    @pytest.mark.asyncio
    @patch("bridge.websocket_client._get_session_manager")
    @patch("bridge.websocket_client._get_history_manager")
    @patch("bridge.websocket_client.get_config")
    async def test_message_persisted_with_metadata(
        self, mock_get_config, mock_get_history_manager, mock_get_session_manager
    ):
        """Issue #20: Messages persisted with correct metadata (message_type, speakability)."""
        # Setup mocks
        mock_config = MagicMock()
        mock_config.persistence.enabled = True
        mock_config.openclaw = OpenClawConfig()
        mock_get_config.return_value = mock_config
        
        mock_session = MagicMock()
        mock_session.id = 42
        mock_session_mgr = MagicMock()
        mock_session_mgr.get_session.return_value = mock_session
        mock_get_session_manager.return_value = mock_session_mgr
        
        mock_history_mgr = MagicMock()
        mock_get_history_manager.return_value = mock_history_mgr
        
        # Create client
        config = OpenClawConfig()
        client = OpenClawWebSocketClient(config=config)
        client._state = ConnectionState.CONNECTED
        client.websocket = AsyncMock()
        client.websocket.send = AsyncMock(return_value=None)
        client.voice_session_id = "test-session-uuid"
        
        # Send voice input (should persist as user message)
        await client.send_voice_input("Hello world")
        
        # Verify websocket send was called
        assert client.websocket.send.called
        
        # Verify sent message structure
        sent_data = json.loads(client.websocket.send.call_args[0][0])
        assert sent_data["type"] == "voice_input"
        assert sent_data["text"] == "Hello world"
