"""Conversation Store - SQLite persistence for voice sessions and conversation history."""

import json
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from bridge.config import get_config


DB_VERSION = 1


@dataclass
class DatabaseConfig:
    """Configuration for conversation database."""
    db_path: Path
    session_timeout_minutes: int = 30
    max_sessions: int = 100
    backup_enabled: bool = True


def get_session_db_path() -> Path:
    """Get the path to the session database.
    
    Returns:
        Path to SQLite database file
    """
    config = get_config()
    data_dir = Path.home() / ".voice-bridge" / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    return data_dir / "sessions.db"


class ConversationStore:
    """SQLite-based conversation and session persistence.
    
    Provides atomic operations for session management and conversation
    history storage with proper indexing and cleanup.
    """
    
    def __init__(self, db_path: Optional[Path] = None):
        """Initialize conversation store.
        
        Args:
            db_path: Path to SQLite database (default: ~/.voice-bridge/data/sessions.db)
        """
        self.db_path = db_path or get_session_db_path()
        self._ensure_db_exists()
    
    @contextmanager
    def _get_connection(self):
        """Context manager for database connections."""
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
    
    def _ensure_db_exists(self):
        """Initialize database if it doesn't exist."""
        if self.db_path.exists():
            return
        
        with self._get_connection() as conn:
            self._init_schema(conn)
    
    def _init_schema(self, conn: sqlite3.Connection):
        """Initialize database schema.
        
        Creates tables:
        - schema_version: Tracks database migrations
        - sessions: Session metadata and state
        - conversation_turns: Individual messages in conversations
        - tool_executions: Tool call tracking for recovery
        """
        # Schema version tracking
        conn.execute("""
            CREATE TABLE IF NOT EXISTS schema_version (
                version INTEGER PRIMARY KEY,
                applied_at TEXT NOT NULL
            )
        """)
        
        # Sessions table
        conn.execute("""
            CREATE TABLE IF NOT EXISTS sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_uuid TEXT UNIQUE NOT NULL,
                created_at TEXT NOT NULL,
                last_activity TEXT NOT NULL,
                state TEXT NOT NULL CHECK(state IN ('active', 'closed', 'error')),
                context_window TEXT,
                metadata TEXT
            )
        """)
        
        # Indexes for sessions
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_sessions_uuid 
            ON sessions(session_uuid)
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_sessions_state 
            ON sessions(state)
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_sessions_activity 
            ON sessions(last_activity)
        """)
        
        # Conversation turns table
        conn.execute("""
            CREATE TABLE IF NOT EXISTS conversation_turns (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id INTEGER NOT NULL,
                turn_index INTEGER NOT NULL,
                timestamp TEXT NOT NULL,
                role TEXT NOT NULL CHECK(role IN ('user', 'assistant', 'system')),
                content TEXT NOT NULL,
                message_type TEXT,
                speakability TEXT,
                tool_calls TEXT,
                FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE
            )
        """)
        
        # Indexes for turns
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_turns_session 
            ON conversation_turns(session_id)
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_turns_timestamp 
            ON conversation_turns(timestamp)
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_turns_index 
            ON conversation_turns(session_id, turn_index)
        """)
        
        # Tool executions for recovery
        conn.execute("""
            CREATE TABLE IF NOT EXISTS tool_executions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id INTEGER NOT NULL,
                tool_index INTEGER NOT NULL,
                tool_name TEXT NOT NULL,
                status TEXT NOT NULL CHECK(status IN ('pending', 'running', 'completed', 'error', 'cancelled')),
                started_at TEXT,
                completed_at TEXT,
                parameters TEXT,
                result TEXT,
                error_message TEXT,
                FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE
            )
        """)
        
        # Indexes for tools
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_tools_session 
            ON tool_executions(session_id)
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_tools_status 
            ON tool_executions(status)
        """)
        
        # Record schema version
        conn.execute(
            "INSERT OR REPLACE INTO schema_version (version, applied_at) VALUES (?, ?)",
            (DB_VERSION, datetime.now(timezone.utc).isoformat())
        )
    
    def migrate(self, target_version: int = DB_VERSION):
        """Run database migrations.
        
        Args:
            target_version: Target schema version
        """
        with self._get_connection() as conn:
            cursor = conn.execute("SELECT MAX(version) FROM schema_version")
            current_version = cursor.fetchone()[0] or 0
            
            if current_version < target_version:
                for version in range(current_version + 1, target_version + 1):
                    self._migrate_to_version(conn, version)
    
    def _migrate_to_version(self, conn: sqlite3.Connection, version: int):
        """Migrate database to specific version.
        
        Args:
            conn: Database connection
            version: Target version
        """
        if version == 1:
            # Initial schema - already created in _init_schema
            pass
        
        conn.execute(
            "INSERT INTO schema_version (version, applied_at) VALUES (?, ?)",
            (version, datetime.now(timezone.utc).isoformat())
        )
    
    def backup(self) -> Optional[Path]:
        """Create backup of database.
        
        Returns:
            Path to backup file or None if disabled
        """
        config = get_config()
        if not getattr(config, 'backup_sessions', True):
            return None
        
        if not self.db_path.exists():
            return None
        
        backup_dir = self.db_path.parent / "backups"
        backup_dir.mkdir(exist_ok=True)
        
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_path = backup_dir / f"sessions_{timestamp}.db"
        
        with self._get_connection() as src:
            dest = sqlite3.connect(str(backup_path))
            src.backup(dest)
            dest.close()
        
        return backup_path
    
    def cleanup_old_sessions(self, max_age_days: int = 7) -> int:
        """Remove sessions older than specified days.
        
        Args:
            max_age_days: Maximum age in days
            
        Returns:
            Number of sessions removed
        """
        cutoff = (datetime.now(timezone.utc) - timedelta(days=max_age_days)).isoformat()
        
        with self._get_connection() as conn:
            cursor = conn.execute(
                "DELETE FROM sessions WHERE last_activity < ?",
                (cutoff,)
            )
            return cursor.rowcount
    
    def close_stale_sessions(self, timeout_minutes: int = 30) -> int:
        """Mark sessions inactive for too long as closed.
        
        Args:
            timeout_minutes: Inactivity threshold in minutes
            
        Returns:
            Number of sessions closed
        """
        cutoff = (datetime.now(timezone.utc) - timedelta(minutes=timeout_minutes)).isoformat()
        
        with self._get_connection() as conn:
            cursor = conn.execute(
                """UPDATE sessions 
                    SET state = 'closed', 
                        metadata = json_set(COALESCE(metadata, '{}'), '$.close_reason', 'stale')
                    WHERE state = 'active' 
                    AND last_activity < ?""",
                (cutoff,)
            )
            return cursor.rowcount
    
    def get_stats(self) -> dict:
        """Get database statistics.
        
        Returns:
            Dictionary with session counts and sizes
        """
        with self._get_connection() as conn:
            stats = {}
            
            # Session counts by state
            cursor = conn.execute(
                "SELECT state, COUNT(*) FROM sessions GROUP BY state"
            )
            stats['sessions_by_state'] = dict(cursor.fetchall())
            
            # Total turns
            cursor = conn.execute("SELECT COUNT(*) FROM conversation_turns")
            stats['total_turns'] = cursor.fetchone()[0]
            
            # Database size
            stats['db_size_bytes'] = self.db_path.stat().st_size if self.db_path.exists() else 0
            
            # Schema version
            cursor = conn.execute("SELECT MAX(version) FROM schema_version")
            stats['schema_version'] = cursor.fetchone()[0] or 0
            
            return stats


# Global store instance
_store: Optional[ConversationStore] = None


def get_conversation_store() -> ConversationStore:
    """Get or create global conversation store.
    
    Returns:
        ConversationStore instance
    """
    global _store
    if _store is None:
        _store = ConversationStore()
    return _store
