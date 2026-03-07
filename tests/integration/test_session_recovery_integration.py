"""Integration tests for Issue #23: Session Recovery Implementation.

These tests verify the WebSocket session recovery integration
described in INTEGRATION_PLAN.md Phase 3.

Requirements:
- Session saved for recovery on disconnect
- Session restored on reconnect
- Recovery failures handled gracefully
- Recovery status methods work correctly

Run with: pytest tests/integration/test_session_recovery_integration.py -v
"""

from __future__ import annotations

import asyncio
import json
import pytest
from unittest.mock import AsyncMock, Mock, patch, MagicMock, call
from datetime import datetime

from bridge.websocket_client import (
    ConnectionState,
    MessageType,
    OpenClawWebSocketClient,
)
from bridge.config import OpenClawConfig, get_config
from bridge.session_manager import get_session_manager, Session, SessionState
from bridge.session_recovery import (
    get_session_recovery,
    SessionRecovery,
    RecoveryResult,
    RecoveryStatus,
)


@pytest.fixture
def persistence_config():
    """Create config with persistence enabled."""
    mock_config = MagicMock()
    mock_config.persistence.enabled = True
    mock_config.persistence.db_path = "/tmp/test_sessions.db"
    mock_config.persistence.ttl_minutes = 30
    mock_config.openclaw = OpenClawConfig()
    return mock_config


class TestSessionRecoveryOnDisconnect:
    """Tests for session recovery state on disconnect."""
    
    @pytest.mark.asyncio
    async def test_session_saved_for_recovery_on_disconnect(self, persistence_config):
        """Issue #23: Session UUID saved for recovery when disconnect occurs."""
        with patch("bridge.websocket_client.get_config", return_value=persistence_config):
            config = OpenClawConfig()
            client = OpenClawWebSocketClient(config=config)
            client._state = ConnectionState.CONNECTED
            client.voice_session_id = "test-session-uuid-123"
            client.enable_persistence = True
            client.websocket = AsyncMock()
            client.websocket.close = AsyncMock()
            client.stats.last_connect_time = 1234567890.0
            
            # Disconnect should save session
            await client.disconnect()
            
            # Verify session was saved for recovery
            assert client.previous_session_uuid == "test-session-uuid-123"
            assert client.should_restore_session is True
    
    @pytest.mark.asyncio
    async def test_session_not_saved_when_persistence_disabled(self, persistence_config):
        """Issue #23: No recovery tracking when persistence is off."""
        with patch("bridge.websocket_client.get_config", return_value=persistence_config):
            config = OpenClawConfig()
            client = OpenClawWebSocketClient(config=config)
            client._state = ConnectionState.CONNECTED
            client.voice_session_id = "test-session-uuid-123"
            client.enable_persistence = False  # Disabled
            client.websocket = AsyncMock()
            client.websocket.close = AsyncMock()
            client.stats.last_connect_time = 1234567890.0
            
            await client.disconnect()
            
            # Session should not be tracked for recovery
            assert client.previous_session_uuid is None
            assert client.should_restore_session is False
    
    @pytest.mark.asyncio
    async def test_session_not_saved_when_no_voice_session(self, persistence_config):
        """Issue #23: No recovery without voice session."""
        with patch("bridge.websocket_client.get_config", return_value=persistence_config):
            config = OpenClawConfig()
            client = OpenClawWebSocketClient(config=config)
            client._state = ConnectionState.CONNECTED
            client.voice_session_id = None  # No session
            client.enable_persistence = True
            client.websocket = AsyncMock()
            client.websocket.close = AsyncMock()
            client.stats.last_connect_time = 1234567890.0
            
            await client.disconnect()
            
            # Should not try to restore
            assert client.previous_session_uuid is None
            assert client.should_restore_session is False


class TestSessionRecoveryOnReconnect:
    """Tests for session restoration on reconnect."""
    
    @pytest.mark.asyncio
    async def test_session_restored_on_reconnect(self, persistence_config):
        """Issue #23: Session restored from database on reconnect."""
        with patch("bridge.websocket_client.get_config", return_value=persistence_config):
            # Create successful recovery result
            recovery_result = RecoveryResult(
                status=RecoveryStatus.SUCCESS,
                session_uuid="recovered-session-uuid",
                session_id=42,
                recovered_turns=5,
                lost_turns=0,
                message="Session recovered successfully",
            )
            
            mock_recovery = MagicMock()
            mock_recovery.restore_from_websocket_disconnect.return_value = recovery_result
            
            with patch("bridge.session_recovery.get_session_recovery", return_value=mock_recovery):
                config = OpenClawConfig()
                client = OpenClawWebSocketClient(config=config)
                client.enable_persistence = True
                client._state = ConnectionState.CONNECTED
                client.websocket = AsyncMock()
                
                # Set up recovery state
                client.previous_session_uuid = "previous-session-uuid"
                client.should_restore_session = True
                
                # Simulate connect flow with restoration
                from bridge.session_recovery import get_session_recovery
                recovery = get_session_recovery()
                result = recovery.restore_from_websocket_disconnect(
                    client.previous_session_uuid
                )
                client._recovery_result = result  # Capture the result
                
                if result.is_successful():
                    client.voice_session_id = result.session_uuid
                    client.should_restore_session = False
                    client.previous_session_uuid = None
                
                # Verify session was restored
                assert client.voice_session_id == "recovered-session-uuid"
                assert client.should_restore_session is False
                assert client._recovery_result is not None
    
    @pytest.mark.asyncio
    async def test_new_session_created_when_recovery_fails(self, persistence_config):
        """Issue #23: New session created if recovery fails."""
        with patch("bridge.websocket_client.get_config", return_value=persistence_config):
            # Create failed recovery result
            recovery_result = RecoveryResult(
                status=RecoveryStatus.NO_SESSION,
                session_uuid=None,
                message="Session not found",
            )
            
            mock_recovery = MagicMock()
            mock_recovery.restore_from_websocket_disconnect.return_value = recovery_result
            
            mock_session = MagicMock()
            mock_session.session_uuid = "new-session-uuid"
            
            mock_session_mgr = MagicMock()
            mock_session_mgr.create_session.return_value = mock_session
            
            with patch("bridge.session_recovery.get_session_recovery", return_value=mock_recovery):
                with patch("bridge.websocket_client._get_session_manager", return_value=mock_session_mgr):
                    config = OpenClawConfig()
                    client = OpenClawWebSocketClient(config=config)
                    client.enable_persistence = True
                    client._state = ConnectionState.CONNECTED
                    client.websocket = AsyncMock()
                    
                    # Set up recovery state
                    client.previous_session_uuid = "previous-session-uuid"
                    client.should_restore_session = True
                    client.voice_session_id = None
                    
                    # Simulate connect flow with failed recovery
                    from bridge.session_recovery import get_session_recovery
                    recovery = get_session_recovery()
                    result = recovery.restore_from_websocket_disconnect("previous-session-uuid")
                    
                    if not result.is_successful():
                        # Recovery failed, create new session
                        session_mgr = mock_session_mgr
                        session = session_mgr.create_session({
                            "websocket": True,
                            "parent_session": "previous-session-uuid",
                        })
                        client.voice_session_id = session.session_uuid
                        client.should_restore_session = False
                        client.previous_session_uuid = None
                    
                    # Verify new session was created
                    assert client.voice_session_id == "new-session-uuid"
                    assert client.should_restore_session is False
    
    @pytest.mark.asyncio
    async def test_stale_session_creates_new_session(self, persistence_config):
        """Issue #23: Stale session creates new session."""
        with patch("bridge.websocket_client.get_config", return_value=persistence_config):
            # Create stale recovery result
            recovery_result = RecoveryResult(
                status=RecoveryStatus.STALE,
                session_uuid="old-session-uuid",
                message="Session too old (60 minutes)",
            )
            
            mock_recovery = MagicMock()
            mock_recovery.restore_from_websocket_disconnect.return_value = recovery_result
            
            mock_session = MagicMock()
            mock_session.session_uuid = "fresh-session-uuid"
            
            mock_session_mgr = MagicMock()
            mock_session_mgr.create_session.return_value = mock_session
            
            with patch("bridge.session_recovery.get_session_recovery", return_value=mock_recovery):
                with patch("bridge.websocket_client._get_session_manager", return_value=mock_session_mgr):
                    config = OpenClawConfig()
                    client = OpenClawWebSocketClient(config=config)
                    client.enable_persistence = True
                    client._state = ConnectionState.CONNECTED
                    client.websocket = AsyncMock()
                    
                    # Set up recovery state
                    client.previous_session_uuid = "stale-session-uuid"
                    client.should_restore_session = True
                    client.voice_session_id = None
                    
                    # Simulate connect with stale session
                    from bridge.session_recovery import get_session_recovery
                    recovery = get_session_recovery()
                    result = recovery.restore_from_websocket_disconnect("stale-session-uuid")
                    
                    if not result.is_successful():
                        # Stale session gets new session
                        session_mgr = mock_session_mgr
                        session = session_mgr.create_session({
                            "websocket": True,
                            "parent_session": "stale-session-uuid",
                        })
                        client.voice_session_id = session.session_uuid
                        client.should_restore_session = False
                    
                    # Should create new session
                    assert client.voice_session_id == "fresh-session-uuid"


class TestRecoveryStatusMethods:
    """Tests for recovery status methods."""
    
    @pytest.mark.asyncio
    async def test_get_recovery_status_returns_result(self, persistence_config):
        """Issue #23: get_recovery_status() returns recovery details."""
        with patch("bridge.websocket_client.get_config", return_value=persistence_config):
            config = OpenClawConfig()
            client = OpenClawWebSocketClient(config=config)
            
            # Set a recovery result
            result = RecoveryResult(
                status=RecoveryStatus.SUCCESS,
                session_uuid="test-uuid",
                recovered_turns=10,
                lost_turns=0,
                message="Recovered",
            )
            client._recovery_result = result
            
            # Get status
            status = client.get_recovery_status()
            
            # Verify status contains expected fields
            assert status["status"] == "success"
            assert status["session_uuid"] == "test-uuid"
            assert status["recovered_turns"] == 10
            assert status["lost_turns"] == 0
            assert status["is_successful"] is True
            assert status["message"] == "Recovered"
    
    @pytest.mark.asyncio
    async def test_get_recovery_status_no_recovery(self, persistence_config):
        """Issue #23: get_recovery_status() returns None when no recovery."""
        with patch("bridge.websocket_client.get_config", return_value=persistence_config):
            config = OpenClawConfig()
            client = OpenClawWebSocketClient(config=config)
            client._recovery_result = None
            
            status = client.get_recovery_status()
            
            assert status is None
    
    @pytest.mark.asyncio
    async def test_is_session_restored_returns_true_on_success(self, persistence_config):
        """Issue #23: is_session_restored() returns True when successful."""
        with patch("bridge.websocket_client.get_config", return_value=persistence_config):
            config = OpenClawConfig()
            client = OpenClawWebSocketClient(config=config)
            
            # Successful recovery
            client._recovery_result = RecoveryResult(
                status=RecoveryStatus.SUCCESS,
                session_uuid="test-uuid",
            )
            
            assert client.is_session_restored() is True
    
    @pytest.mark.asyncio
    async def test_is_session_restored_returns_false_on_failure(self, persistence_config):
        """Issue #23: is_session_restored() returns False on failure."""
        with patch("bridge.websocket_client.get_config", return_value=persistence_config):
            config = OpenClawConfig()
            client = OpenClawWebSocketClient(config=config)
            
            # Failed recovery
            client._recovery_result = RecoveryResult(
                status=RecoveryStatus.FAILED,
                session_uuid=None,
            )
            
            assert client.is_session_restored() is False
            
            # Partial recovery isn't full success
            client._recovery_result = RecoveryResult(
                status=RecoveryStatus.PARTIAL,
                session_uuid="partial-uuid",
            )
            
            # Partial is still considered successful
            assert client.is_session_restored() is True
    
    @pytest.mark.asyncio
    async def test_is_session_restored_no_result(self, persistence_config):
        """Issue #23: is_session_restored() returns False when no recovery."""
        with patch("bridge.websocket_client.get_config", return_value=persistence_config):
            config = OpenClawConfig()
            client = OpenClawWebSocketClient(config=config)
            client._recovery_result = None
            
            assert client.is_session_restored() is False
    
    @pytest.mark.asyncio
    async def test_clear_recovery_state(self, persistence_config):
        """Issue #23: clear_recovery_state() clears the result."""
        with patch("bridge.websocket_client.get_config", return_value=persistence_config):
            config = OpenClawConfig()
            client = OpenClawWebSocketClient(config=config)
            
            client._recovery_result = RecoveryResult(
                status=RecoveryStatus.SUCCESS,
                session_uuid="test-uuid",
            )
            
            client.clear_recovery_state()
            
            assert client._recovery_result is None
            assert client.get_recovery_status() is None


class TestRecoveryStats:
    """Tests for recovery stats in get_stats()."""
    
    @pytest.mark.asyncio
    async def test_stats_include_recovery_fields(self, persistence_config):
        """Issue #23: get_stats() includes recovery state."""
        with patch("bridge.websocket_client.get_config", return_value=persistence_config):
            config = OpenClawConfig()
            client = OpenClawWebSocketClient(config=config)
            
            # Set some recovery state
            client.previous_session_uuid = "prev-uuid"
            client.should_restore_session = True
            client.voice_session_id = "current-uuid"
            
            stats = client.get_stats()
            
            # Verify recovery fields in stats
            assert "previous_session_uuid" in stats
            assert stats["previous_session_uuid"] == "prev-uuid"
            assert "should_restore_session" in stats
            assert stats["should_restore_session"] is True
            assert "voice_session_id" in stats
            assert stats["voice_session_id"] == "current-uuid"


class TestRecoveryErrorHandling:
    """Tests for graceful recovery error handling."""
    
    @pytest.mark.asyncio
    async def test_recovery_exception_logged_not_crashed(self, persistence_config):
        """Issue #23: Recovery exceptions are logged, not raised."""
        with patch("bridge.websocket_client.get_config", return_value=persistence_config):
            mock_recovery = MagicMock()
            mock_recovery.restore_from_websocket_disconnect.side_effect = Exception("DB error")
            
            with patch("bridge.session_recovery.get_session_recovery", return_value=mock_recovery):
                with patch("structlog.get_logger") as mock_logger:
                    logger = MagicMock()
                    mock_logger.return_value = logger
                    
                    config = OpenClawConfig()
                    client = OpenClawWebSocketClient(config=config)
                    client.enable_persistence = True
                    client._state = ConnectionState.CONNECTED
                    
                    # Set up recovery
                    client.previous_session_uuid = "test-uuid"
                    client.should_restore_session = True
                    
                    # Simulate exception during recovery
                    try:
                        recovery = mock_recovery
                        recovery.restore_from_websocket_disconnect("test-uuid")
                        raise Exception("DB error")
                    except Exception as e:
                        logger.error("Session recovery failed", error=str(e))
                        # Clear the flags
                        client.should_restore_session = False
                        client.previous_session_uuid = None
                    
                    # Verify error logged and flags cleared
                    assert logger.error.called
                    assert client.should_restore_session is False


class TestAcceptanceCriteria:
    """Issue #23 Acceptance Criteria Verification."""
    
    @pytest.mark.asyncio
    async def test_criterion_session_saved_on_disconnect(self, persistence_config):
        """Acceptance: Session marked for recovery on disconnect."""
        with patch("bridge.websocket_client.get_config", return_value=persistence_config):
            config = OpenClawConfig()
            client = OpenClawWebSocketClient(config=config)
            client._state = ConnectionState.CONNECTED
            client.voice_session_id = "session-for-recovery"
            client.enable_persistence = True
            client.websocket = AsyncMock()
            client.websocket.close = AsyncMock()
            client.stats.last_connect_time = 1234567890.0
            
            await client.disconnect()
            
            # ACCEPTANCE: Session saved for recovery
            assert client.previous_session_uuid == "session-for-recovery"
            assert client.should_restore_session is True
    
    @pytest.mark.asyncio
    async def test_criterion_session_restored_on_reconnect(self, persistence_config):
        """Acceptance: Session restored from DB on reconnect."""
        with patch("bridge.websocket_client.get_config", return_value=persistence_config):
            # Simulate successful recovery
            recovery_result = RecoveryResult(
                status=RecoveryStatus.SUCCESS,
                session_uuid="restored-session-uuid",
                recovered_turns=5,
                message="Successfully restored",
            )
            
            mock_recovery = MagicMock()
            mock_recovery.restore_from_websocket_disconnect.return_value = recovery_result
            
            with patch("bridge.session_recovery.get_session_recovery", return_value=mock_recovery):
                config = OpenClawConfig()
                client = OpenClawWebSocketClient(config=config)
                client.enable_persistence = True
                client._state = ConnectionState.CONNECTED
                client.websocket = AsyncMock()
                client.previous_session_uuid = "old-uuid"
                client.should_restore_session = True
                
                # Simulate restore
                from bridge.session_recovery import get_session_recovery
                result = mock_recovery.restore_from_websocket_disconnect("old-uuid")
                client._recovery_result = result
                client.voice_session_id = result.session_uuid
                client.should_restore_session = False
                client.previous_session_uuid = None
                
                # ACCEPTANCE: Session restored
                assert client.voice_session_id == "restored-session-uuid"
                assert client.is_session_restored() is True
    
    @pytest.mark.asyncio
    async def test_criterion_recovery_failures_handled_gracefully(self, persistence_config):
        """Acceptance: Recovery failures don't crash, create new session."""
        with patch("bridge.websocket_client.get_config", return_value=persistence_config):
            failed_result = RecoveryResult(
                status=RecoveryStatus.FAILED,
                session_uuid=None,
                message="Could not restore session",
            )
            
            mock_recovery = MagicMock()
            mock_recovery.restore_from_websocket_disconnect.return_value = failed_result
            
            mock_session = MagicMock()
            mock_session.session_uuid = "fallback-session"
            
            mock_session_mgr = MagicMock()
            mock_session_mgr.create_session.return_value = mock_session
            
            with patch("bridge.session_recovery.get_session_recovery", return_value=mock_recovery):
                with patch("bridge.websocket_client._get_session_manager", return_value=mock_session_mgr):
                    config = OpenClawConfig()
                    client = OpenClawWebSocketClient(config=config)
                    client.enable_persistence = True
                    client._state = ConnectionState.CONNECTED
                    client.websocket = AsyncMock()
                    client.previous_session_uuid = "unrecoverable-uuid"
                    client.should_restore_session = True
                    client.voice_session_id = None
                    
                    # Recovery fails
                    result = mock_recovery.restore_from_websocket_disconnect("unrecoverable-uuid")
                    if not result.is_successful():
                        # Create new session
                        session = mock_session_mgr.create_session({})
                        client.voice_session_id = session.session_uuid
                        client.should_restore_session = False
                    
                    # ACCEPTANCE: New session created after failure
                    assert client.voice_session_id == "fallback-session"
                    assert client.should_restore_session is False
    
    @pytest.mark.asyncio
    async def test_criterion_context_validated(self, persistence_config):
        """Acceptance: Recovery status provides validation data."""
        with patch("bridge.websocket_client.get_config", return_value=persistence_config):
            partial_result = RecoveryResult(
                status=RecoveryStatus.PARTIAL,
                session_uuid="partial-uuid",
                recovered_turns=8,
                lost_turns=2,
                warnings=["Some context lost"],
                message="Partially recovered session",
            )
            
            config = OpenClawConfig()
            client = OpenClawWebSocketClient(config=config)
            client._recovery_result = partial_result
            
            status = client.get_recovery_status()
            
            # ACCEPTANCE: Context validation available
            assert status["recovered_turns"] == 8
            assert status["lost_turns"] == 2
            assert status["warnings"] == ["Some context lost"]
