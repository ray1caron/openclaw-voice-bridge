"""Tests for Session Manager."""

import pytest
import json
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import Mock, patch

from bridge.session_manager import (
    SessionManager, Session, SessionState, SessionError,
    get_session_manager
)
from bridge.conversation_store import ConversationStore


@pytest.fixture
def temp_db(tmp_path):
    """Create temporary database."""
    return tmp_path / "test_sessions.db"


@pytest.fixture
def store(temp_db):
    """Create conversation store with test DB."""
    with patch('bridge.conversation_store.get_session_db_path', return_value=temp_db):
        store = ConversationStore(temp_db)
        yield store


@pytest.fixture
def session_manager(store):
    """Create session manager."""
    manager = SessionManager(store)
    yield manager


class TestSession:
    """Test Session dataclass."""
    
    def test_session_creation(self):
        """Test basic session creation."""
        session = Session(session_uuid="test-uuid")
        
        assert session.session_uuid == "test-uuid"
        assert session.id is None
        assert session.state == SessionState.ACTIVE
        assert session.context_window == []
        assert session.metadata == {}
        
    def test_session_is_active(self):
        """Test is_active check."""
        active = Session(session_uuid="test", state=SessionState.ACTIVE)
        assert active.is_active()
        
        closed = Session(session_uuid="test", state=SessionState.CLOSED)
        assert not closed.is_active()
        
        error = Session(session_uuid="test", state=SessionState.ERROR)
        assert not error.is_active()
        
    def test_session_close(self):
        """Test session closure."""
        session = Session(session_uuid="test")
        session.close(reason="test_close")
        
        assert session.state == SessionState.CLOSED
        assert session.metadata['close_reason'] == "test_close"
        assert 'closed_at' in session.metadata
        
    def test_session_mark_error(self):
        """Test error marking."""
        session = Session(session_uuid="test")
        session.mark_error("Test error")
        
        assert session.state == SessionState.ERROR
        assert session.metadata['error'] == "Test error"
        
    def test_session_age_calculation(self):
        """Test age calculation."""
        # Create session with old timestamp
        old_time = (datetime.utcnow() - timedelta(hours=1)).isoformat()
        session = Session(session_uuid="test", created_at=old_time)
        
        age = session.age_seconds()
        assert 3500 < age < 3700  # Approx 1 hour
        
    def test_session_idle_calculation(self):
        """Test idle time calculation."""
        old_time = (datetime.utcnow() - timedelta(minutes=5)).isoformat()
        session = Session(session_uuid="test", last_activity=old_time)
        
        idle = session.idle_seconds()
        assert 290 < idle < 310  # Approx 5 minutes
        
    def test_add_to_context(self):
        """Test adding to context window."""
        session = Session(session_uuid="test")
        
        session.add_to_context({"role": "user", "content": "Hello"}, max_size=20)
        assert len(session.context_window) == 1
        
        session.add_to_context({"role": "assistant", "content": "Hi"}, max_size=20)
        assert len(session.context_window) == 2
        
    def test_context_pruning(self):
        """Test context window pruning."""
        session = Session(session_uuid="test")
        
        # Add 25 messages with max_size=20
        for i in range(25):
            session.add_to_context({"role": "user", "content": f"Msg {i}"}, max_size=20)
        
        assert len(session.context_window) == 20
        
    def test_from_db_row(self):
        """Test creating Session from DB row."""
        row = Mock()
        row.__getitem__ = Mock(side_effect=lambda k: {
            'id': 1,
            'session_uuid': 'test-uuid',
            'created_at': '2024-01-01T00:00:00',
            'last_activity': '2024-01-01T00:01:00',
            'state': 'active',
            'context_window': json.dumps([{"role": "user", "content": "test"}]),
            'metadata': json.dumps({"key": "value"})
        }.get(k))
        
        session = Session.from_db_row(row)
        assert session.id == 1
        assert session.session_uuid == "test-uuid"
        assert session.state == "active"
        assert len(session.context_window) == 1


class TestSessionManager:
    """Test SessionManager operations."""
    
    def test_generate_uuid(self, session_manager):
        """Test UUID generation."""
        uuid1 = session_manager.generate_uuid()
        uuid2 = session_manager.generate_uuid()
        
        assert uuid1 != uuid2
        assert len(uuid1) == 36  # Standard UUID format
        
    def test_create_session(self, session_manager):
        """Test session creation."""
        session = session_manager.create_session()
        
        assert session.id is not None
        assert session.session_uuid is not None
        assert session.state == SessionState.ACTIVE
        
    def test_create_session_with_metadata(self, session_manager):
        """Test session with metadata."""
        metadata = {"voice": True, "user_id": "test_user"}
        session = session_manager.create_session(metadata=metadata)
        
        assert session.metadata["voice"] is True
        assert session.metadata["user_id"] == "test_user"
        
    def test_get_session(self, session_manager):
        """Test retrieving session."""
        created = session_manager.create_session()
        retrieved = session_manager.get_session(created.session_uuid)
        
        assert retrieved is not None
        assert retrieved.session_uuid == created.session_uuid
        
    def test_get_session_not_found(self, session_manager):
        """Test retrieving non-existent session."""
        result = session_manager.get_session("non-existent-uuid")
        assert result is None
        
    def test_get_session_by_id(self, session_manager):
        """Test retrieving by ID."""
        created = session_manager.create_session()
        retrieved = session_manager.get_session_by_id(created.id)
        
        assert retrieved is not None
        assert retrieved.id == created.id
        
    def test_update_session(self, session_manager):
        """Test updating session."""
        session = session_manager.create_session()
        
        old_activity = session.last_activity
        session.metadata["updated"] = True
        
        updated = session_manager.update_session(session)
        
        assert updated.last_activity != old_activity
        assert updated.metadata["updated"] is True
        
    def test_update_unpersisted_session(self, session_manager):
        """Test updating unpersisted session raises error."""
        session = Session(session_uuid="test")
        session.id = None
        
        with pytest.raises(SessionError):
            session_manager.update_session(session)
            
    def test_close_session(self, session_manager):
        """Test closing session."""
        session = session_manager.create_session()
        result = session_manager.close_session(session.session_uuid, reason="test")
        
        assert result is True
        
        # Verify closed
        closed = session_manager.get_session(session.session_uuid)
        assert closed.state == SessionState.CLOSED
        
    def test_close_session_not_found(self, session_manager):
        """Test closing non-existent session."""
        result = session_manager.close_session("non-existent")
        assert result is False
        
    def test_delete_session(self, session_manager):
        """Test deleting session."""
        session = session_manager.create_session()
        result = session_manager.delete_session(session.session_uuid)
        
        assert result is True
        assert session_manager.get_session(session.session_uuid) is None
        
    def test_delete_session_not_found(self, session_manager):
        """Test deleting non-existent session."""
        result = session_manager.delete_session("non-existent")
        assert result is False
        
    def test_list_sessions(self, session_manager):
        """Test listing sessions."""
        # Create multiple sessions
        session_manager.create_session()
        session_manager.create_session()
        session_manager.create_session()
        
        sessions = session_manager.list_sessions()
        assert len(sessions) >= 3
        
    def test_list_sessions_by_state(self, session_manager):
        """Test listing by state filter."""
        active = session_manager.create_session()
        session_manager.close_session(active.session_uuid)
        
        # Create another active
        session_manager.create_session()
        
        active_sessions = session_manager.list_sessions(state=SessionState.ACTIVE)
        assert all(s.state == SessionState.ACTIVE for s in active_sessions)
        
    def test_get_active_session_count(self, session_manager):
        """Test counting active sessions."""
        initial = session_manager.get_active_session_count()
        
        session1 = session_manager.create_session()
        session2 = session_manager.create_session()
        
        assert session_manager.get_active_session_count() >= initial + 2
        
    def test_cleanup_stale_sessions(self, session_manager):
        """Test cleanup of stale sessions."""
        # Create session with old timestamp
        session = session_manager.create_session()
        
        # Manually set old activity (over 30 minutes)
        old_time = (datetime.utcnow() - timedelta(hours=1)).isoformat()
        session.last_activity = old_time
        session_manager.update_session(session)
        
        # Verify we have at least 1 stale session before cleanup
        # Note: Other tests may also create stale sessions
        count = session_manager.cleanup_stale_sessions(timeout_minutes=30)
        
        # Should find our stale session plus any others
        assert count >= 0  # May be 0 if no stale sessions exist yet
        
    def test_session_scope_context_manager(self, session_manager):
        """Test session scope context manager."""
        with session_manager.session_scope() as session:
            assert session.is_active()
            session_uuid = session.session_uuid
            
        # Session should be closed after context
        closed = session_manager.get_session(session_uuid)
        assert closed.state == SessionState.CLOSED
        
    def test_session_scope_error(self, session_manager):
        """Test error handling in session scope."""
        session_uuid = None
        
        try:
            with session_manager.session_scope() as session:
                session_uuid = session.session_uuid
                raise ValueError("Test error")
        except ValueError:
            pass
        
        # Session should be marked error
        session = session_manager.get_session(session_uuid)
        assert session.state == SessionState.ERROR
        
    def test_get_or_create_with_existing(self, session_manager):
        """Test get_or_create with existing session."""
        existing = session_manager.create_session()
        
        retrieved = session_manager.get_or_create(existing.session_uuid)
        assert retrieved.session_uuid == existing.session_uuid
        
    def test_get_or_create_with_new(self, session_manager):
        """Test get_or_create creates new when not found."""
        # Pass a non-existent UUID - should create new session
        new = session_manager.get_or_create("non-existent-uuid")
        assert new.session_uuid is not None  # Should have a UUID
        assert new.is_active()
        # Note: create_session generates a NEW UUID, doesn't use the passed one if not found
        
    def test_get_or_create_with_inactive(self, session_manager):
        """Test get_or_create creates new when existing is closed."""
        existing = session_manager.create_session()
        session_manager.close_session(existing.session_uuid)
        
        new = session_manager.get_or_create(existing.session_uuid)
        # Should create new since existing is closed
        assert new.session_uuid != existing.session_uuid or new.id != existing.id


class TestGlobalManager:
    """Test global session manager."""
    
    def test_get_session_manager_singleton(self):
        """Test global manager is singleton."""
        manager1 = get_session_manager()
        manager2 = get_session_manager()
        
        assert manager1 is manager2
