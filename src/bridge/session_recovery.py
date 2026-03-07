"""Session Recovery - Restore session state after disconnects and failures."""

import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Optional, List, Dict, Any

from bridge.conversation_store import get_conversation_store
from bridge.session_manager import (
    get_session_manager, SessionManager, Session, SessionState
)
from bridge.history_manager import get_history_manager, ConversationTurn
from bridge.context_window import ContextWindow
from bridge.tool_chain_manager import (
    get_tool_chain_manager, ToolChainManager, ToolChainState
)


class RecoveryStatus(Enum):
    """Session recovery status."""
    SUCCESS = "success"
    PARTIAL = "partial"
    FAILED = "failed"
    STALE = "stale"  # Session too old to recover
    NO_SESSION = "no_session"


@dataclass
class RecoveryResult:
    """Result of session recovery attempt."""
    status: RecoveryStatus
    session_uuid: Optional[str] = None
    session_id: Optional[int] = None
    recovered_turns: int = 0
    lost_turns: int = 0
    recovered_tools: int = 0
    message: str = ""
    warnings: List[str] = field(default_factory=list)
    
    def is_successful(self) -> bool:
        """Check if recovery was successful."""
        return self.status in (RecoveryStatus.SUCCESS, RecoveryStatus.PARTIAL)


class SessionRecovery:
    """Manages session recovery after disconnects and failures.
    
    Provides:
    - Session state reconstruction from database
    - Tool chain recovery for interrupted operations
    - Context window restoration
    - Recovery validation and reporting
    """
    
    def __init__(
        self,
        session_manager: Optional[SessionManager] = None,
        tool_chain_manager: Optional[ToolChainManager] = None
    ):
        """Initialize session recovery.
        
        Args:
            session_manager: Session manager instance
            tool_chain_manager: Tool chain manager for tool recovery
        """
        self.session_manager = session_manager or get_session_manager()
        self.tool_chain_manager = tool_chain_manager
        self.history = get_history_manager()
        self.store = get_conversation_store()
        
        # Recovery configuration
        self.max_recovery_age_minutes = 60
        self.stale_session_minutes = 30
    
    def recover_session(
        self,
        session_uuid: str,
        force: bool = False
    ) -> RecoveryResult:
        """Attempt to restore session from database.
        
        Args:
            session_uuid: Session UUID to recover
            force: Force recovery even if session appears stale
            
        Returns:
            RecoveryResult with status and details
        """
        result = RecoveryResult(
            session_uuid=session_uuid,
            status=RecoveryStatus.FAILED
        )
        
        # Check if session exists
        session = self.session_manager.get_session(session_uuid)
        if not session:
            result.status = RecoveryStatus.NO_SESSION
            result.message = f"Session {session_uuid} not found"
            return result
        
        result.session_id = session.id
        
        # Check if session is too old
        idle_minutes = session.idle_seconds() / 60
        if idle_minutes > self.max_recovery_age_minutes and not force:
            result.status = RecoveryStatus.STALE
            result.message = f"Session idle for {idle_minutes:.1f} minutes (max: {self.max_recovery_age_minutes})"
            return result
        
        # Load conversation history
        turns = self.history.get_session_turns(session_uuid)
        result.recovered_turns = len(turns)
        
        # Check for inconsistent state (more turns than expected)
        expected_turns = session.metadata.get('total_turns', 0)
        if expected_turns > 0 and len(turns) < expected_turns:
            result.lost_turns = expected_turns - len(turns)
            result.warnings.append(
                f"Expected {expected_turns} turns, found {len(turns)} (lost {result.lost_turns})"
            )
        
        # Recover context window
        try:
            context = self._recover_context(session_uuid, session)
            if context:
                session.context_window = context.to_dict().get('messages', [])
        except Exception as e:
            result.warnings.append(f"Failed to recover context window: {e}")
        
        # Check for interrupted tool chains
        tool_state = self._recover_tools(session.id, result)
        if tool_state:
            result.recovered_tools = tool_state.get('recovered_count', 0)
        
        # Re-activate session
        session.state = SessionState.ACTIVE
        session.update_activity()
        session.metadata['recovered_at'] = datetime.now(timezone.utc).isoformat()
        session.metadata['recovery_turns'] = len(turns)
        
        self.session_manager.update_session(session)
        
        # Determine final status
        if result.lost_turns == 0 and not result.warnings:
            result.status = RecoveryStatus.SUCCESS
            result.message = f"Session fully recovered with {len(turns)} turns"
        else:
            result.status = RecoveryStatus.PARTIAL
            result.message = f"Session partially recovered with {len(turns)} turns"
        
        return result
    
    def _recover_context(
        self,
        session_uuid: str,
        session: Session
    ) -> Optional[ContextWindow]:
        """Restore context window for session.
        
        Args:
            session_uuid: Session identifier
            session: Session object
            
        Returns:
            Restored ContextWindow or None
        """
        # Try to restore from stored context
        if session.context_window:
            try:
                return ContextWindow.from_dict({
                    'session_uuid': session_uuid,
                    'session_id': session.id,
                    'messages': session.context_window
                })
            except Exception:
                pass
        
        # Fall back to loading from history
        context = ContextWindow(
            session_uuid=session_uuid,
            session_id=session.id
        ).load()
        
        return context if context.message_count > 0 else None
    
    def _recover_tools(
        self,
        session_id: int,
        result: RecoveryResult
    ) -> Optional[Dict[str, Any]]:
        """Attempt to recover interrupted tool chains.
        
        Args:
            session_id: Database session ID
            result: Recovery result to update
            
        Returns:
            Tool recovery state or None
        """
        with self.store._get_connection() as conn:
            cursor = conn.execute(
                """SELECT * FROM tool_executions 
                    WHERE session_id = ? AND status IN ('running', 'pending')
                    ORDER BY tool_index""",
                (session_id,)
            )
            tools = cursor.fetchall()
        
        if not tools:
            return None
        
        # Cancel interrupted tools
        recovered = 0
        for tool in tools:
            conn.execute(
                """UPDATE tool_executions 
                    SET status = 'cancelled', completed_at = ?
                    WHERE id = ?""",
                (datetime.now(timezone.utc).isoformat(), tool['id'])
            )
            recovered += 1
        
        result.warnings.append(
            f"Cancelled {len(tools)} interrupted tool executions"
        )
        
        return {
            'recovered_count': recovered,
            'cancelled_tools': [t['tool_name'] for t in tools]
        }
    
    def get_recovery_candidates(
        self,
        max_age_minutes: Optional[int] = None
    ) -> List[Session]:
        """Find sessions eligible for recovery.
        
        Args:
            max_age_minutes: Maximum session age for recovery
            
        Returns:
            List of sessions that could be recovered
        """
        max_age = max_age_minutes or self.max_recovery_age_minutes
        cutoff = (datetime.now(timezone.utc) - timedelta(minutes=max_age)).isoformat()
        
        # Find sessions that were active recently
        with self.store._get_connection() as conn:
            cursor = conn.execute(
                """SELECT * FROM sessions 
                    WHERE last_activity > ? 
                    AND state IN ('active', 'error')
                    ORDER BY last_activity DESC""",
                (cutoff,)
            )
            return [Session.from_db_row(row) for row in cursor.fetchall()]
    
    def restore_from_websocket_disconnect(
        self,
        previous_session_uuid: str,
        last_message_timestamp: Optional[str] = None
    ) -> RecoveryResult:
        """Specialized recovery for WebSocket disconnects.
        
        Args:
            previous_session_uuid: Previous session UUID
            last_message_timestamp: Last known message timestamp
            
        Returns:
            RecoveryResult
        """
        result = self.recover_session(previous_session_uuid)
        
        if not result.is_successful():
            # If full recovery failed, create new session with context hint
            session = self.session_manager.get_session(previous_session_uuid)
            if session:
                # Create new session with reference to previous
                new_session = self.session_manager.create_session({
                    'parent_session': previous_session_uuid,
                    'disconnected_at': datetime.now(timezone.utc).isoformat(),
                    'recovery_attempted': True,
                    'previous_turns': result.recovered_turns
                })
                
                result.session_uuid = new_session.session_uuid
                result.session_id = new_session.id
                result.message += f". New session created: {new_session.session_uuid}"
        
        return result
    
    def get_recovery_summary(self, session_uuid: str) -> Optional[Dict[str, Any]]:
        """Get summary of what can be recovered.
        
        Args:
            session_uuid: Session to check
            
        Returns:
            Recovery summary or None if session not found
        """
        session = self.session_manager.get_session(session_uuid)
        if not session:
            return None
        
        turns = self.history.get_session_turns(session_uuid)
        
        # Check for interrupted tools
        with self.store._get_connection() as conn:
            cursor = conn.execute(
                """SELECT COUNT(*) FROM tool_executions 
                    WHERE session_id = ? AND status IN ('running', 'pending')""",
                (session.id,)
            )
            pending_tools = cursor.fetchone()[0]
        
        idle_minutes = session.idle_seconds() / 60
        
        return {
            'session_uuid': session_uuid,
            'session_state': session.state,
            'created_at': session.created_at,
            'last_activity': session.last_activity,
            'idle_minutes': idle_minutes,
            'total_turns': len(turns),
            'pending_tools': pending_tools,
            'is_recoverable': (
                session.state != SessionState.CLOSED and
                idle_minutes < self.max_recovery_age_minutes
            ),
            'recovery_eligible': idle_minutes < self.max_recovery_age_minutes
        }
    
    def mark_session_restored(self, session_uuid: str, recovery_result: RecoveryResult):
        """Mark session as successfully restored.
        
        Args:
            session_uuid: Session UUID
            recovery_result: Recovery result to record
        """
        session = self.session_manager.get_session(session_uuid)
        if not session:
            return
        
        session.metadata.update({
            'restored': True,
            'restored_at': datetime.now(timezone.utc).isoformat(),
            'recovery_status': recovery_result.status.value,
            'recovered_turns': recovery_result.recovered_turns,
            'recovery_warnings': recovery_result.warnings
        })
        
        self.session_manager.update_session(session)


# Global instance
_recovery: Optional[SessionRecovery] = None


def get_session_recovery() -> SessionRecovery:
    """Get or create global session recovery."""
    global _recovery
    if _recovery is None:
        _recovery = SessionRecovery()
    return _recovery
