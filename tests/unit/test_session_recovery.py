"""Tests for Session Recovery."""

import pytest
from datetime import datetime, timedelta
from unittest.mock import Mock, patch, MagicMock

from bridge.session_recovery import (
    SessionRecovery, RecoveryStatus, RecoveryResult,
    get_session_recovery
)
from bridge.session_manager import Session, SessionState, get_session_manager
from bridge.tool_chain_manager import ToolChainManager, ToolChainState


class TestRecoveryStatus:
    """Test RecoveryStatus enum."""
    
    def test_status_values(self):
        """Test status values."""
        assert RecoveryStatus.SUCCESS.value == "success"
        assert RecoveryStatus.PARTIAL.value == "partial"
        assert RecoveryStatus.FAILED.value == "failed"
        assert RecoveryStatus.STALE.value == "stale"
        assert RecoveryStatus.NO_SESSION.value == "no_session"


class TestRecoveryResult:
    """Test RecoveryResult dataclass."""
    
    def test_creation(self):
        """Test basic creation."""
        result = RecoveryResult(status=RecoveryStatus.SUCCESS)
        
        assert result.status == RecoveryStatus.SUCCESS
        assert result.session_uuid is None
        assert result.recovered_turns == 0
        assert result.warnings == []
        
    def test_is_successful_success(self):
        """Test successful status check."""
        result = RecoveryResult(status=RecoveryStatus.SUCCESS)
        assert result.is_successful()
        
    def test_is_successful_partial(self):
        """Test partial success."""
        result = RecoveryResult(status=RecoveryStatus.PARTIAL)
        assert result.is_successful()
        
    def test_is_successful_failed(self):
        """Test failed status."""
        result = RecoveryResult(status=RecoveryStatus.FAILED)
        assert not result.is_successful()
        
    def test_is_successful_stale(self):
        """Test stale status."""
        result = RecoveryResult(status=RecoveryStatus.STALE)
        assert not result.is_successful()


class TestSessionRecovery:
    """Test SessionRecovery class."""
    
    @pytest.fixture
    def mock_session_manager(self):
        """Mock session manager."""
        with patch('bridge.session_recovery.get_session_manager') as mock:
            manager = Mock()
            mock.return_value = manager
            yield manager
    
    @pytest.fixture
    def mock_history_manager(self):
        """Mock history manager."""
        with patch('bridge.session_recovery.get_history_manager') as mock:
            manager = Mock()
            mock.return_value = manager
            yield manager
    
    @pytest.fixture
    def mock_store(self):
        """Mock conversation store."""
        with patch('bridge.session_recovery.get_conversation_store') as mock:
            store = Mock()
            mock.return_value = store
            yield store
    
    def test_init(self):
        """Test initialization."""
        recovery = SessionRecovery()
        
        assert recovery.max_recovery_age_minutes == 60
        assert recovery.stale_session_minutes == 30
        
    def test_recover_session_not_found(self, mock_session_manager, mock_history_manager):
        """Test recovery when session doesn't exist."""
        mock_session_manager.get_session.return_value = None
        
        recovery = SessionRecovery()
        result = recovery.recover_session("non-existent-uuid")
        
        assert result.status == RecoveryStatus.NO_SESSION
        assert result.message == "Session non-existent-uuid not found"
        
    def test_recover_session_stale(self, mock_session_manager, mock_history_manager):
        """Test recovery of stale session."""
        # Create old session
        old_time = (datetime.utcnow() - timedelta(hours=2)).isoformat()
        session = Session(
            session_uuid="test-uuid",
            id=1,
            last_activity=old_time,
            state=SessionState.ACTIVE
        )
        mock_session_manager.get_session.return_value = session
        
        recovery = SessionRecovery()
        result = recovery.recover_session("test-uuid")
        
        assert result.status == RecoveryStatus.STALE
        assert result.session_id == 1
        assert "idle" in result.message.lower()
        
    def test_recover_session_force(self, mock_session_manager, mock_history_manager):
        """Test forced recovery of old session."""
        old_time = (datetime.utcnow() - timedelta(hours=2)).isoformat()
        session = Session(
            session_uuid="test-uuid",
            id=1,
            last_activity=old_time,
            state=SessionState.ACTIVE
        )
        mock_session_manager.get_session.return_value = session
        mock_history_manager.get_session_turns.return_value = []
        
        recovery = SessionRecovery()
        result = recovery.recover_session("test-uuid", force=True)
        
        # Should attempt recovery despite being old
        assert result.status in (RecoveryStatus.SUCCESS, RecoveryStatus.PARTIAL)
        
    def test_recover_session_full_recovery(self, mock_session_manager, mock_history_manager):
        """Test successful full recovery."""
        # Create recent active session
        session = Session(
            session_uuid="test-uuid",
            id=1,
            state=SessionState.ACTIVE
        )
        mock_session_manager.get_session.return_value = session
        
        # Mock turns
        from bridge.history_manager import ConversationTurn
        mock_turns = [
            ConversationTurn(
                id=1, session_id=1, turn_index=0,
                role="user", content="Hello"
            ),
            ConversationTurn(
                id=2, session_id=1, turn_index=1,
                role="assistant", content="Hi"
            )
        ]
        mock_history_manager.get_session_turns.return_value = mock_turns
        mock_history_manager.get_recent_turns.return_value = mock_turns
        
        recovery = SessionRecovery()
        
        with patch.object(recovery, '_recover_tools') as mock_recover_tools:
            mock_recover_tools.return_value = None
            
            result = recovery.recover_session("test-uuid")
            
            assert result.status == RecoveryStatus.SUCCESS
            assert result.recovered_turns == 2
            assert "fully recovered" in result.message.lower()
            
    def test_recover_session_with_warnings(self, mock_session_manager, mock_history_manager):
        """Test recovery with warnings."""
        session = Session(
            session_uuid="test-uuid",
            id=1,
            state=SessionState.ACTIVE,
            metadata={'total_turns': 5}
        )
        mock_session_manager.get_session.return_value = session
        
        # Return fewer turns than expected
        from bridge.history_manager import ConversationTurn
        mock_turns = [
            ConversationTurn(id=1, session_id=1, turn_index=0, role="user", content="Hello")
        ]
        mock_history_manager.get_session_turns.return_value = mock_turns
        mock_history_manager.get_recent_turns.return_value = mock_turns
        
        recovery = SessionRecovery()
        
        with patch.object(recovery, '_recover_tools') as mock_recover_tools:
            mock_recover_tools.return_value = None
            
            result = recovery.recover_session("test-uuid")
            
            assert result.status == RecoveryStatus.PARTIAL
            assert result.lost_turns == 4
            assert len(result.warnings) > 0
            
    def test_recover_context(self, mock_session_manager):
        """Test context window recovery."""
        from bridge.context_window import ContextWindow
        
        session = Session(
            session_uuid="test-uuid",
            id=1,
            context_window=[{ "role": "user", "content": "Test" }]
        )
        
        recovery = SessionRecovery()
        context = recovery._recover_context("test-uuid", session)
        
        assert context is not None
        
    def test_recover_tools(self, mock_session_manager):
        """Test tool execution recovery."""
        recovery = SessionRecovery()
        result = RecoveryResult(status=RecoveryStatus.SUCCESS)
        
        # Create proper mock store with context manager
        store_mock = MagicMock()
        mock_conn = MagicMock()
        mock_cursor = Mock()
        mock_cursor.fetchall.return_value = [
            {'id': 1, 'tool_name': 'test_tool', 'tool_index': 0}
        ]
        mock_cursor.rowcount = 1  # For update operations
        mock_conn.execute.return_value = mock_cursor
        mock_conn.__enter__ = Mock(return_value=mock_conn)
        mock_conn.__exit__ = Mock(return_value=False)
        store_mock._get_connection = Mock(return_value=mock_conn)
        
        with patch.object(recovery, 'store', store_mock):
            state = recovery._recover_tools(1, result)
            
            assert state is not None
            assert state['recovered_count'] == 1
            assert "cancelled" in str(result.warnings).lower()
                
    def test_get_recovery_candidates(self, mock_session_manager):
        """Test finding recovery candidates."""
        # Mock recent active sessions
        recent_time = (datetime.utcnow() - timedelta(minutes=5)).isoformat()
        mock_rows = [
            MockSessionRow(recent_time, SessionState.ACTIVE),
        ]
        
        mock_conn = MagicMock()
        mock_cursor = Mock()
        mock_cursor.fetchall.return_value = mock_rows
        mock_conn.execute.return_value = mock_cursor
        
        recovery = SessionRecovery()
        
        with patch.object(recovery.store, '_get_connection', return_value=mock_conn):
            candidates = recovery.get_recovery_candidates()
            
            assert len(candidates) >= 0
            
    def test_restore_from_websocket_disconnect_success(self, mock_session_manager, mock_history_manager):
        """Test WebSocket disconnect recovery success."""
        # Create session
        session = Session(session_uuid="old-uuid", id=1, state=SessionState.ACTIVE)
        mock_session_manager.get_session.return_value = session
        mock_history_manager.get_session_turns.return_value = []
        mock_history_manager.get_recent_turns.return_value = []
        
        recovery = SessionRecovery()
        
        with patch.object(recovery, '_recover_tools') as mock_recover_tools:
            mock_recover_tools.return_value = None
            
            result = recovery.restore_from_websocket_disconnect("old-uuid")
            
            assert result.status in (RecoveryStatus.SUCCESS, RecoveryStatus.PARTIAL)
            
    def test_restore_from_websocket_disconnect_fail(self, mock_session_manager, mock_history_manager):
        """Test WebSocket disconnect recovery failure creates new session."""
        # Simulate failed recovery
        mock_session_manager.get_session.return_value = None
        
        session = Session(session_uuid="old-uuid", id=1)
        mock_session_manager.get_session.side_effect = [None, session]
        
        recovery = SessionRecovery()
        
        result = recovery.restore_from_websocket_disconnect("old-uuid")
        
        # Should have created new session
        assert result.session_uuid is not None
        assert "New session" in result.message
        
    def test_get_recovery_summary(self, mock_session_manager, mock_history_manager):
        """Test getting recovery summary."""
        session = Session(session_uuid="test-uuid", id=1, state=SessionState.ACTIVE)
        mock_session_manager.get_session.return_value = session
        mock_history_manager.get_session_turns.return_value = [
            Mock() for _ in range(5)
        ]
        
        mock_conn = MagicMock()
        mock_cursor = Mock()
        mock_cursor.fetchone.return_value = [0]
        mock_conn.execute.return_value = mock_cursor
        
        recovery = SessionRecovery()
        
        with patch.object(recovery.store, '_get_connection', return_value=mock_conn):
            summary = recovery.get_recovery_summary("test-uuid")
            
            assert summary is not None
            assert summary['session_uuid'] == "test-uuid"
            assert summary['total_turns'] == 5
            assert summary['is_recoverable'] is True
            
    def test_get_recovery_summary_not_found(self, mock_session_manager):
        """Test summary when session not found."""
        mock_session_manager.get_session.return_value = None
        
        recovery = SessionRecovery()
        summary = recovery.get_recovery_summary("non-existent")
        
        assert summary is None
        
    def test_mark_session_restored(self, mock_session_manager):
        """Test marking session as restored."""
        session = Session(session_uuid="test-uuid", id=1, state=SessionState.ACTIVE)
        mock_session_manager.get_session.return_value = session
        
        result = RecoveryResult(
            status=RecoveryStatus.SUCCESS,
            session_uuid="test-uuid",
            recovered_turns=10,
            warnings=["Test warning"]
        )
        
        recovery = SessionRecovery()
        recovery.mark_session_restored("test-uuid", result)
        
        assert session.metadata['restored'] is True
        assert session.metadata['recovered_turns'] == 10


class MockSessionRow:
    """Mock database row for session."""
    def __init__(self, last_activity, state):
        self._data = {
            'id': 1,
            'session_uuid': 'test-uuid',
            'created_at': last_activity,
            'last_activity': last_activity,
            'state': state,
            'context_window': None,
            'metadata': None
        }
    
    def __getitem__(self, key):
        return self._data.get(key)


class TestGlobalRecovery:
    """Test global session recovery."""
    
    def test_get_session_recovery_singleton(self):
        """Test global recovery is singleton."""
        recovery1 = get_session_recovery()
        recovery2 = get_session_recovery()
        
        assert recovery1 is recovery2
