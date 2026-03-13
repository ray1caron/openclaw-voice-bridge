"""Session Manager - High-level session lifecycle management."""

import json
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Optional, List, Dict, Any
import uuid

from bridge.conversation_store import ConversationStore, get_conversation_store
from bridge.config import get_config


class SessionState:
    """Session state constants."""
    ACTIVE = "active"
    CLOSED = "closed"
    ERROR = "error"


class SessionError(Exception):
    """Exception raised for session management errors."""
    pass


@dataclass
class Session:
    """Represents a conversation session.
    
    Attributes:
        session_uuid: Unique session identifier
        id: Database ID (None until persisted)
        created_at: Session creation timestamp
        last_activity: Last recorded activity
        state: Current session state
        context_window: Recent message context as list
        metadata: Additional session metadata
    """
    session_uuid: str
    id: Optional[int] = None
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    last_activity: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    state: str = SessionState.ACTIVE
    context_window: List[Dict[str, Any]] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)
    
    @classmethod
    def from_db_row(cls, row) -> "Session":
        """Create Session from database row.

        Args:
            row: sqlite3.Row from sessions table

        Returns:
            Session instance
        """
        d = dict(row)
        try:
            context_window = json.loads(d['context_window']) if d.get('context_window') else []
        except (json.JSONDecodeError, ValueError):
            context_window = []
        try:
            metadata = json.loads(d['metadata']) if d.get('metadata') else {}
        except (json.JSONDecodeError, ValueError):
            metadata = {}
        return cls(
            id=d['id'],
            session_uuid=d['session_uuid'],
            created_at=d['created_at'],
            last_activity=d['last_activity'],
            state=d['state'],
            context_window=context_window,
            metadata=metadata,
        )
    
    def to_db_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for database storage."""
        import json
        return {
            'session_uuid': self.session_uuid,
            'created_at': self.created_at,
            'last_activity': self.last_activity,
            'state': self.state,
            'context_window': json.dumps(self.context_window) if self.context_window else None,
            'metadata': json.dumps(self.metadata) if self.metadata else None
        }
    
    def update_activity(self):
        """Update last activity timestamp."""
        self.last_activity = datetime.now(timezone.utc).isoformat()
    
    def add_to_context(self, message: Dict[str, Any], max_size: int = 20):
        """Add message to context window.
        
        Args:
            message: Message to add
            max_size: Maximum context window size
        """
        import json
        
        self.context_window.append(message)
        
        # Prune if necessary
        if len(self.context_window) > max_size:
            # Keep first 5 and last 15 messages (preserves early and recent context)
            if max_size > 5:
                self.context_window = (
                    self.context_window[:5] + 
                    self.context_window[-(max_size-5):]
                )
            else:
                self.context_window = self.context_window[-max_size:]
        
        self.update_activity()
    
    def is_active(self) -> bool:
        """Check if session is currently active."""
        return self.state == SessionState.ACTIVE
    
    def close(self, reason: str = "manual"):
        """Close session gracefully.
        
        Args:
            reason: Reason for closing
        """
        self.state = SessionState.CLOSED
        self.metadata['close_reason'] = reason
        self.metadata['closed_at'] = datetime.now(timezone.utc).isoformat()
        self.update_activity()
    
    def mark_error(self, error: str):
        """Mark session with error.
        
        Args:
            error: Error description
        """
        self.state = SessionState.ERROR
        self.metadata['error'] = error
        self.update_activity()
    
    def age_seconds(self) -> float:
        """Calculate session age in seconds."""
        created = datetime.fromisoformat(self.created_at)
        return (datetime.now(timezone.utc) - created).total_seconds()
    
    def idle_seconds(self) -> float:
        """Calculate time since last activity."""
        last = datetime.fromisoformat(self.last_activity)
        return (datetime.now(timezone.utc) - last).total_seconds()


class SessionManager:
    """Manages session lifecycle and persistence.
    
    Provides operations for creating, retrieving, updating, and
    deleting sessions with automatic persistence to SQLite.
    """
    
    def __init__(self, store: Optional[ConversationStore] = None):
        """Initialize session manager.
        
        Args:
            store: ConversationStore instance (default: global instance)
        """
        self.store = store or get_conversation_store()
        self._active_sessions: Dict[str, Session] = {}
        self._config = get_config()
    
    def generate_uuid(self) -> str:
        """Generate unique session identifier."""
        return str(uuid.uuid4())
    
    def create_session(self, metadata: Optional[Dict[str, Any]] = None) -> Session:
        """Create new session.
        
        Args:
            metadata: Optional session metadata
            
        Returns:
            New Session instance (persisted)
        """
        import json
        
        session = Session(
            session_uuid=self.generate_uuid(),
            metadata=metadata or {}
        )
        
        # Persist to database
        with self.store._get_connection() as conn:
            cursor = conn.execute(
                """INSERT INTO sessions 
                    (session_uuid, created_at, last_activity, state, context_window, metadata)
                    VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    session.session_uuid,
                    session.created_at,
                    session.last_activity,
                    session.state,
                    json.dumps(session.context_window) if session.context_window else None,
                    json.dumps(session.metadata) if session.metadata else None
                )
            )
            session.id = cursor.lastrowid
        
        # Cache active session
        self._active_sessions[session.session_uuid] = session
        
        return session
    
    def get_session(self, session_uuid: str) -> Optional[Session]:
        """Get session by UUID.
        
        Args:
            session_uuid: Session UUID
            
        Returns:
            Session or None if not found
        """
        # Check cache first
        if session_uuid in self._active_sessions:
            return self._active_sessions[session_uuid]
        
        # Load from database
        with self.store._get_connection() as conn:
            cursor = conn.execute(
                "SELECT * FROM sessions WHERE session_uuid = ?",
                (session_uuid,)
            )
            row = cursor.fetchone()
            
            if not row:
                return None
            
            session = Session.from_db_row(row)
            
            # Cache if active
            if session.is_active():
                self._active_sessions[session_uuid] = session
            
            return session
    
    def get_session_by_id(self, session_id: int) -> Optional[Session]:
        """Get session by database ID.
        
        Args:
            session_id: Database ID
            
        Returns:
            Session or None if not found
        """
        with self.store._get_connection() as conn:
            cursor = conn.execute(
                "SELECT * FROM sessions WHERE id = ?",
                (session_id,)
            )
            row = cursor.fetchone()
            
            if not row:
                return None
            
            return Session.from_db_row(row)
    
    def update_session(self, session: Session) -> Session:
        """Update session in database.
        
        Args:
            session: Session to update
            
        Returns:
            Updated session
        """
        import json
        
        if session.id is None:
            raise SessionError("Session must be persisted before update")
        
        session.update_activity()
        
        with self.store._get_connection() as conn:
            conn.execute(
                """UPDATE sessions 
                    SET last_activity = ?, 
                        state = ?, 
                        context_window = ?,
                        metadata = ?
                    WHERE id = ?""",
                (
                    session.last_activity,
                    session.state,
                    json.dumps(session.context_window) if session.context_window else None,
                    json.dumps(session.metadata) if session.metadata else None,
                    session.id
                )
            )
        
        return session
    
    def close_session(self, session_uuid: str, reason: str = "manual") -> bool:
        """Close session.
        
        Args:
            session_uuid: Session UUID
            reason: Reason for closing
            
        Returns:
            True if session was closed, False if not found
        """
        session = self.get_session(session_uuid)
        if not session:
            return False
        
        session.close(reason)
        self.update_session(session)
        
        # Remove from cache
        if session_uuid in self._active_sessions:
            del self._active_sessions[session_uuid]
        
        return True
    
    def delete_session(self, session_uuid: str) -> bool:
        """Delete session (hard delete).
        
        Args:
            session_uuid: Session UUID
            
        Returns:
            True if deleted, False if not found
        """
        with self.store._get_connection() as conn:
            cursor = conn.execute(
                "DELETE FROM sessions WHERE session_uuid = ?",
                (session_uuid,)
            )
            
            if cursor.rowcount == 0:
                return False
        
        # Remove from cache
        if session_uuid in self._active_sessions:
            del self._active_sessions[session_uuid]
        
        return True
    
    def list_sessions(
        self, 
        state: Optional[str] = None,
        limit: int = 100,
        offset: int = 0
    ) -> List[Session]:
        """List sessions.
        
        Args:
            state: Filter by state (optional)
            limit: Maximum results
            offset: Pagination offset
            
        Returns:
            List of Session instances
        """
        with self.store._get_connection() as conn:
            if state:
                cursor = conn.execute(
                    """SELECT * FROM sessions 
                        WHERE state = ? 
                        ORDER BY last_activity DESC 
                        LIMIT ? OFFSET ?""",
                    (state, limit, offset)
                )
            else:
                cursor = conn.execute(
                    """SELECT * FROM sessions 
                        ORDER BY last_activity DESC 
                        LIMIT ? OFFSET ?""",
                    (limit, offset)
                )
            
            return [Session.from_db_row(row) for row in cursor.fetchall()]
    
    def get_active_session_count(self) -> int:
        """Get count of active sessions."""
        with self.store._get_connection() as conn:
            cursor = conn.execute(
                "SELECT COUNT(*) FROM sessions WHERE state = ?",
                (SessionState.ACTIVE,)
            )
            return cursor.fetchone()[0]
    
    def cleanup_stale_sessions(self, timeout_minutes: int = 30) -> int:
        """Close sessions idle for too long.
        
        Args:
            timeout_minutes: Inactivity threshold
            
        Returns:
            Number of sessions closed
        """
        return self.store.close_stale_sessions(timeout_minutes)
    
    @contextmanager
    def session_scope(self, metadata: Optional[Dict[str, Any]] = None):
        """Context manager for session lifecycle.
        
        Automatically creates session and closes on exit.
        
        Args:
            metadata: Optional session metadata
            
        Yields:
            Session instance
        """
        session = None
        try:
            session = self.create_session(metadata)
            yield session
            if session.is_active():
                self.close_session(session.session_uuid, reason="completed")
        except Exception as e:
            if session:
                session.mark_error(str(e))
                self.update_session(session)
            raise
    
    def get_or_create_session(
        self, 
        session_uuid: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None
    ) -> Session:
        """Get existing session or create new one.
        
        Args:
            session_uuid: Optional existing UUID
            metadata: Metadata for new session
            
        Returns:
            Session instance (existing or new)
        """
        if session_uuid:
            existing = self.get_session(session_uuid)
            if existing and existing.is_active():
                return existing
        
        return self.create_session(metadata)
    
    # Alias for convenience
    get_or_create = get_or_create_session


# Global manager instance
_manager: Optional[SessionManager] = None


def get_session_manager() -> SessionManager:
    """Get or create global session manager.
    
    Returns:
        SessionManager instance
    """
    global _manager
    if _manager is None:
        _manager = SessionManager()
    return _manager
