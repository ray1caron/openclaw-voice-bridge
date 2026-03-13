"""History Manager - Query and export conversation history."""

from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, List, Dict, Any, Iterator
import csv
import json

from bridge.conversation_store import get_conversation_store
from bridge.session_manager import get_session_manager


@dataclass
class ConversationTurn:
    """Represents a single turn in a conversation.
    
    Attributes:
        id: Database ID
        session_id: Parent session ID
        turn_index: Position in conversation
        timestamp: When turn occurred
        role: 'user', 'assistant', or 'system'
        content: Message content
        message_type: Type from OpenClaw middleware
        speakability: Whether this was spoken
        tool_calls: Tool calls made in this turn
    """
    id: Optional[int] = None
    session_id: Optional[int] = None
    turn_index: int = 0
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    role: str = "user"
    content: str = ""
    message_type: Optional[str] = None
    speakability: Optional[str] = None
    tool_calls: Optional[Dict[str, Any]] = None
    
    @classmethod
    def from_db_row(cls, row) -> "ConversationTurn":
        """Create from database row."""
        d = dict(row)
        try:
            tool_calls = json.loads(d['tool_calls']) if d.get('tool_calls') else None
        except (json.JSONDecodeError, ValueError):
            tool_calls = None
        return cls(
            id=d['id'],
            session_id=d['session_id'],
            turn_index=d['turn_index'],
            timestamp=d['timestamp'],
            role=d['role'],
            content=d['content'],
            message_type=d.get('message_type'),
            speakability=d.get('speakability'),
            tool_calls=tool_calls,
        )
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            'id': self.id,
            'session_id': self.session_id,
            'turn_index': self.turn_index,
            'timestamp': self.timestamp,
            'role': self.role,
            'content': self.content,
            'message_type': self.message_type,
            'speakability': self.speakability,
            'tool_calls': self.tool_calls
        }


@dataclass  
class ConversationSession:
    """Complete conversation session with turns.
    
    Attributes:
        session_uuid: Session identifier
        created_at: Session start time
        state: Final session state
        turns: List of conversation turns
        metadata: Session metadata
    """
    session_uuid: str
    created_at: str
    state: str = "active"
    turns: List[ConversationTurn] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert complete conversation to dictionary."""
        return {
            'session_uuid': self.session_uuid,
            'created_at': self.created_at,
            'state': self.state,
            'metadata': self.metadata,
            'turns': [turn.to_dict() for turn in self.turns]
        }


class HistoryManager:
    """Manages querying and exporting conversation history."""
    
    def __init__(self):
        """Initialize history manager."""
        self.store = get_conversation_store()
        self.session_manager = get_session_manager()
    
    def add_turn(
        self,
        session_id: int,
        role: str,
        content: str,
        turn_index: int = 0,
        message_type: Optional[str] = None,
        speakability: Optional[str] = None,
        tool_calls: Optional[Dict[str, Any]] = None
    ) -> ConversationTurn:
        """Add conversation turn to database.
        
        Args:
            session_id: Parent session ID
            role: 'user', 'assistant', or 'system'
            content: Message content
            turn_index: Position in conversation
            message_type: Message type from middleware
            speakability: Speakability flag
            tool_calls: Tool call data
            
        Returns:
            Created ConversationTurn
        """
        with self.store._get_connection() as conn:
            cursor = conn.execute(
                """INSERT INTO conversation_turns 
                    (session_id, turn_index, timestamp, role, content, 
                     message_type, speakability, tool_calls)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    session_id, turn_index, datetime.now(timezone.utc).isoformat(),
                    role, content, message_type, speakability,
                    json.dumps(tool_calls) if tool_calls else None
                )
            )
            turn_id = cursor.lastrowid
        
        return self.get_turn(turn_id)
    
    def get_turn(self, turn_id: int) -> Optional[ConversationTurn]:
        """Get turn by ID.
        
        Args:
            turn_id: Turn database ID
            
        Returns:
            ConversationTurn or None
        """
        with self.store._get_connection() as conn:
            cursor = conn.execute(
                "SELECT * FROM conversation_turns WHERE id = ?",
                (turn_id,)
            )
            row = cursor.fetchone()
            return ConversationTurn.from_db_row(row) if row else None
    
    def get_session_turns(
        self,
        session_uuid: str,
        start_index: int = 0,
        end_index: Optional[int] = None
    ) -> List[ConversationTurn]:
        """Get turns for a session.
        
        Args:
            session_uuid: Session UUID
            start_index: Starting turn index
            end_index: Ending turn index (None for all)
            
        Returns:
            List of turns in order
        """
        session = self.session_manager.get_session(session_uuid)
        if not session:
            return []
        
        with self.store._get_connection() as conn:
            if end_index is not None:
                cursor = conn.execute(
                    """SELECT t.* FROM conversation_turns t
                        JOIN sessions s ON t.session_id = s.id
                        WHERE s.session_uuid = ? 
                        AND t.turn_index BETWEEN ? AND ?
                        ORDER BY t.turn_index""",
                    (session_uuid, start_index, end_index)
                )
            else:
                cursor = conn.execute(
                    """SELECT t.* FROM conversation_turns t
                        JOIN sessions s ON t.session_id = s.id
                        WHERE s.session_uuid = ? 
                        AND t.turn_index >= ?
                        ORDER BY t.turn_index""",
                    (session_uuid, start_index)
                )
            
            return [ConversationTurn.from_db_row(row) for row in cursor.fetchall()]
    
    def get_recent_turns(
        self,
        session_uuid: str,
        count: int = 10
    ) -> List[ConversationTurn]:
        """Get most recent turns.
        
        Args:
            session_uuid: Session UUID
            count: Number of turns
            
        Returns:
            Last N turns from session
        """
        return self.get_session_turns(session_uuid, end_index=count)
    
    def search_conversations(
        self,
        query: str,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        limit: int = 100
    ) -> List[Dict[str, Any]]:
        """Search conversations by content.
        
        Args:
            query: Search string
            start_date: ISO date filter (inclusive)
            end_date: ISO date filter (inclusive)
            limit: Maximum results
            
        Returns:
            List of matching turns with session info
        """
        with self.store._get_connection() as conn:
            sql = """SELECT t.*, s.session_uuid, s.created_at, s.state
                     FROM conversation_turns t
                     JOIN sessions s ON t.session_id = s.id
                     WHERE t.content LIKE ?"""
            params: List[Any] = [f"%{query}%"]
            
            if start_date:
                sql += " AND t.timestamp >= ?"
                params.append(start_date)
            
            if end_date:
                sql += " AND t.timestamp <= ?"
                params.append(end_date)
            
            sql += " ORDER BY t.timestamp DESC LIMIT ?"
            params.append(limit)
            
            cursor = conn.execute(sql, params)
            
            return [
                {
                    'turn_id': row['id'],
                    'session_uuid': row['session_uuid'],
                    'turn_index': row['turn_index'],
                    'timestamp': row['timestamp'],
                    'role': row['role'],
                    'content': row['content'][:200] + "..." if len(row['content']) > 200 else row['content'],
                    'session_created_at': row['created_at'],
                    'session_state': row['state']
                }
                for row in cursor.fetchall()
            ]
    
    def get_conversation_stats(self, session_uuid: str) -> Dict[str, Any]:
        """Get statistics for a conversation.
        
        Args:
            session_uuid: Session UUID
            
        Returns:
            Dictionary with stats
        """
        with self.store._get_connection() as conn:
            # Total turns
            cursor = conn.execute(
                """SELECT COUNT(*) FROM conversation_turns ct
                    JOIN sessions s ON ct.session_id = s.id
                    WHERE s.session_uuid = ?""",
                (session_uuid,)
            )
            total_turns = cursor.fetchone()[0]
            
            # By role
            cursor = conn.execute(
                """SELECT ct.role, COUNT(*) 
                    FROM conversation_turns ct
                    JOIN sessions s ON ct.session_id = s.id
                    WHERE s.session_uuid = ?
                    GROUP BY ct.role""",
                (session_uuid,)
            )
            turns_by_role = dict(cursor.fetchall())
            
            # By message type
            cursor = conn.execute(
                """SELECT ct.message_type, COUNT(*) 
                    FROM conversation_turns ct
                    JOIN sessions s ON ct.session_id = s.id
                    WHERE s.session_uuid = ? AND ct.message_type IS NOT NULL
                    GROUP BY ct.message_type""",
                (session_uuid,)
            )
            by_type = {k: v for k, v in cursor.fetchall() if k}
            
            return {
                'session_uuid': session_uuid,
                'total_turns': total_turns,
                'turns_by_role': turns_by_role,
                'by_message_type': by_type
            }
    
    def export_session_json(
        self,
        session_uuid: str,
        output_path: Path
    ) -> bool:
        """Export session to JSON.
        
        Args:
            session_uuid: Session UUID
            output_path: Output file path
            
        Returns:
            True if exported successfully
        """
        session = self.session_manager.get_session(session_uuid)
        if not session:
            return False
        
        turns = self.get_session_turns(session_uuid)
        
        convo = ConversationSession(
            session_uuid=session_uuid,
            created_at=session.created_at,
            state=session.state,
            turns=turns,
            metadata=session.metadata
        )
        
        output_path.parent.mkdir(parents=True, exist_ok=True)
        
        with open(output_path, 'w') as f:
            json.dump(convo.to_dict(), f, indent=2)
        
        return True
    
    def export_session_csv(
        self,
        session_uuid: str, 
        output_path: Path
    ) -> bool:
        """Export session to CSV.
        
        Args:
            session_uuid: Session UUID
            output_path: Output file path
            
        Returns:
            True if exported successfully
        """
        turns = self.get_session_turns(session_uuid)
        if not turns:
            return False
        
        output_path.parent.mkdir(parents=True, exist_ok=True)
        
        with open(output_path, 'w', newline='') as f:
            writer = csv.writer(f)
            writer.writerow(['turn_index', 'timestamp', 'role', 'content', 
                            'message_type', 'speakability'])
            
            for turn in turns:
                writer.writerow([
                    turn.turn_index,
                    turn.timestamp,
                    turn.role,
                    turn.content,
                    turn.message_type or '',
                    turn.speakability or ''
                ])
        
        return True
    
    def export_all_sessions(
        self,
        output_path: Path,
        format: str = "json"
    ) -> int:
        """Export all sessions.
        
        Args:
            output_path: Output file path
            format: 'json' or 'csv'
            
        Returns:
            Number of sessions exported
        """
        sessions = self.session_manager.list_sessions(limit=10000)
        count = 0
        
        if format == "json":
            all_convos = []
            for session in sessions:
                turns = self.get_session_turns(session.session_uuid)
                convo = ConversationSession(
                    session_uuid=session.session_uuid,
                    created_at=session.created_at,
                    state=session.state,
                    turns=turns,
                    metadata=session.metadata
                )
                all_convos.append(convo.to_dict())
                count += 1
            
            output_path.parent.mkdir(parents=True, exist_ok=True)
            with open(output_path, 'w') as f:
                json.dump(all_convos, f, indent=2)
        
        return count
    
    def delete_turns_for_session(self, session_uuid: str) -> int:
        """Delete all turns for a session.
        
        Args:
            session_uuid: Session UUID
            
        Returns:
            Number of turns deleted
        """
        with self.store._get_connection() as conn:
            cursor = conn.execute(
                """DELETE FROM conversation_turns 
                    WHERE session_id IN (
                        SELECT id FROM sessions WHERE session_uuid = ?
                    )""",
                (session_uuid,)
            )
            return cursor.rowcount


# Global instance
_history_manager: Optional[HistoryManager] = None


def get_history_manager() -> HistoryManager:
    """Get or create global history manager."""
    global _history_manager
    if _history_manager is None:
        _history_manager = HistoryManager()
    return _history_manager
