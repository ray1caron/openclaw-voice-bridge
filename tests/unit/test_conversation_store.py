"""Unit tests for conversation_store module."""

import pytest
import sqlite3
from pathlib import Path
from datetime import datetime, timedelta

from bridge.conversation_store import (
    ConversationStore,
    get_conversation_store,
    get_session_db_path,
    DB_VERSION,
)


@pytest.fixture
def temp_store(tmp_path):
    """Create a temporary conversation store for testing."""
    db_path = tmp_path / "test_sessions.db"
    store = ConversationStore(db_path=db_path)
    return store


class TestConversationStore:
    """Test ConversationStore functionality."""
    
    def test_initialization(self, temp_store):
        """Test store initializes correctly."""
        assert temp_store.db_path.exists()
        assert temp_store.db_path.is_file()
    
    def test_database_schema_created(self, temp_store):
        """Test database schema is created on init."""
        with temp_store._get_connection() as conn:
            # Check tables exist
            cursor = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
            tables = {row[0] for row in cursor.fetchall()}
            
            assert "schema_version" in tables
            assert "sessions" in tables
            assert "conversation_turns" in tables
            assert "tool_executions" in tables
    
    def test_indexes_created(self, temp_store):
        """Test indexes are created."""
        with temp_store._get_connection() as conn:
            cursor = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='index'"
            )
            indexes = {row[0] for row in cursor.fetchall()}
            
            assert "idx_sessions_uuid" in indexes
            assert "idx_sessions_state" in indexes
            assert "idx_sessions_activity" in indexes
            assert "idx_turns_session" in indexes
            assert "idx_turns_timestamp" in indexes
            assert "idx_turns_index" in indexes
            assert "idx_tools_session" in indexes
            assert "idx_tools_status" in indexes
    
    def test_schema_version_recorded(self, temp_store):
        """Test schema version is recorded."""
        with temp_store._get_connection() as conn:
            cursor = conn.execute(
                "SELECT version FROM schema_version ORDER BY version DESC LIMIT 1"
            )
            version = cursor.fetchone()[0]
            assert version == DB_VERSION
    
    def test_close_stale_sessions(self, temp_store):
        """Test closing stale sessions."""
        with temp_store._get_connection() as conn:
            # Insert a test session
            old_time = (datetime.utcnow() - timedelta(minutes=60)).isoformat()
            conn.execute(
                """INSERT INTO sessions (session_uuid, created_at, last_activity, state)
                   VALUES (?, ?, ?, ?)""",
                ("test-session-1", old_time, old_time, "active")
            )
        
        # Close stale sessions (30 min threshold)
        closed_count = temp_store.close_stale_sessions(timeout_minutes=30)
        
        assert closed_count == 1
        
        # Verify session is closed
        with temp_store._get_connection() as conn:
            cursor = conn.execute(
                "SELECT state FROM sessions WHERE session_uuid = ?",
                ("test-session-1",)
            )
            state = cursor.fetchone()[0]
            assert state == "closed"
    
    def test_cleanup_old_sessions(self, temp_store):
        """Test cleaning up old sessions."""
        with temp_store._get_connection() as conn:
            # Insert old session
            old_time = (datetime.utcnow() - timedelta(days=10)).isoformat()
            conn.execute(
                """INSERT INTO sessions (session_uuid, created_at, last_activity, state)
                   VALUES (?, ?, ?, ?)""",
                ("old-session", old_time, old_time, "closed")
            )
        
        # Clean up sessions older than 7 days
        deleted_count = temp_store.cleanup_old_sessions(max_age_days=7)
        
        assert deleted_count == 1
        
        # Verify session is deleted
        with temp_store._get_connection() as conn:
            cursor = conn.execute(
                "SELECT COUNT(*) FROM sessions WHERE session_uuid = ?",
                ("old-session",)
            )
            count = cursor.fetchone()[0]
            assert count == 0
    
    def test_get_stats(self, temp_store):
        """Test getting database statistics."""
        with temp_store._get_connection() as conn:
            # Insert test data
            now = datetime.utcnow().isoformat()
            conn.execute(
                """INSERT INTO sessions (session_uuid, created_at, last_activity, state)
                   VALUES (?, ?, ?, ?)""",
                ("active-session", now, now, "active")
            )
            conn.execute(
                """INSERT INTO sessions (session_uuid, created_at, last_activity, state)
                   VALUES (?, ?, ?, ?)""",
                ("closed-session", now, now, "closed")
            )
        
        stats = temp_store.get_stats()
        
        assert "sessions_by_state" in stats
        assert stats["sessions_by_state"].get("active", 0) == 1
        assert stats["sessions_by_state"].get("closed", 0) == 1
        assert "total_turns" in stats
        assert "db_size_bytes" in stats
    
    def test_backup(self, temp_store, tmp_path):
        """Test database backup creation."""
        # Create some data first
        with temp_store._get_connection() as conn:
            now = datetime.utcnow().isoformat()
            conn.execute(
                """INSERT INTO sessions (session_uuid, created_at, last_activity, state)
                   VALUES (?, ?, ?, ?)""",
                ("test-session", now, now, "active")
            )
        
        backup_path = temp_store.backup()
        
        assert backup_path is not None
        assert backup_path.exists()
        assert backup_path.suffix == ".db"
        
        # Verify backup is valid
        conn = sqlite3.connect(str(backup_path))
        cursor = conn.execute("SELECT COUNT(*) FROM sessions")
        assert cursor.fetchone()[0] == 1
        conn.close()


class TestGetSessionDbPath:
    """Test get_session_db_path function."""
    
    def test_returns_path_in_voice_bridge_data(self):
        """Test returns path in .voice-bridge/data directory."""
        path = get_session_db_path()
        
        assert ".voice-bridge" in str(path)
        assert "data" in str(path)
        assert path.name == "sessions.db"


class TestGetConversationStore:
    """Test get_conversation_store singleton."""
    
    def test_returns_same_instance(self):
        """Test singleton pattern returns same instance."""
        store1 = get_conversation_store()
        store2 = get_conversation_store()
        
        assert store1 is store2
    
    def test_returns_conversation_store_instance(self):
        """Test returns ConversationStore instance."""
        store = get_conversation_store()
        
        assert isinstance(store, ConversationStore)
