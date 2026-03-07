"""Context Window - Manage conversation context for LLM context windows."""

import json
from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional, Callable

from bridge.conversation_store import get_conversation_store
from bridge.history_manager import get_history_manager, ConversationTurn


@dataclass
class ContextMessage:
    """Message formatted for LLM context."""
    role: str
    content: str
    metadata: Dict[str, Any] = field(default_factory=dict)
    
    def to_llm_format(self) -> Dict[str, str]:
        """Convert to standard LLM format."""
        return {
            'role': self.role,
            'content': self.content
        }


class ContextWindow:
    """Manages context window for conversation history.
    
    Provides:
    - Message management with size limits
    - Smart pruning (preserve early context + recent turns)
    - Token-aware context management
    - Context serialization for session persistence
    """
    
    def __init__(
        self,
        session_uuid: Optional[str] = None,
        session_id: Optional[int] = None,
        max_turns: int = 20,
        max_tokens: Optional[int] = None
    ):
        """Initialize context window.
        
        Args:
            session_uuid: Session identifier
            session_id: Database session ID
            max_turns: Maximum conversation turns to retain
            max_tokens: Optional token limit (approximate)
        """
        self.session_uuid = session_uuid
        self.session_id = session_id
        self.max_turns = max_turns
        self.max_tokens = max_tokens
        self._messages: List[ContextMessage] = []
        self._pruned_count = 0
        
    def _load_from_db(self) -> List[ContextMessage]:
        """Load context from database."""
        if not self.session_uuid:
            return []
        
        history = get_history_manager()
        turns = history.get_recent_turns(self.session_uuid, self.max_turns)
        
        return [
            ContextMessage(
                role=turn.role,
                content=turn.content,
                metadata={
                    'turn_index': turn.turn_index,
                    'message_type': turn.message_type,
                    'speakability': turn.speakability
                }
            )
            for turn in turns
        ]
    
    def load(self) -> "ContextWindow":
        """Load context from database."""
        self._messages = self._load_from_db()
        return self
    
    def add_message(
        self,
        role: str,
        content: str,
        metadata: Optional[Dict[str, Any]] = None,
        persist: bool = True
    ) -> "ContextWindow":
        """Add message to context.
        
        Args:
            role: 'user', 'assistant', or 'system'
            content: Message content
            metadata: Optional metadata
            persist: Whether to persist to database
            
        Returns:
            Self for chaining
        """
        message = ContextMessage(
            role=role,
            content=content,
            metadata=metadata or {}
        )
        
        self._messages.append(message)
        
        # Persist if session exists
        if persist and self.session_id and self.session_uuid:
            history = get_history_manager()
            turn_index = len(self._messages) + self._pruned_count
            history.add_turn(
                session_id=self.session_id,
                role=role,
                content=content,
                turn_index=turn_index,
                message_type=metadata.get('message_type') if metadata else None,
                speakability=metadata.get('speakability') if metadata else None
            )
        
        # Apply pruning if needed
        self._prune_if_needed()
        
        return self
    
    def add_user_message(
        self,
        content: str,
        metadata: Optional[Dict[str, Any]] = None,
        persist: bool = True
    ) -> "ContextWindow":
        """Add user message."""
        return self.add_message('user', content, metadata, persist)
    
    def add_assistant_message(
        self,
        content: str,
        metadata: Optional[Dict[str, Any]] = None,
        persist: bool = True
    ) -> "ContextWindow":
        """Add assistant message."""
        return self.add_message('assistant', content, metadata, persist)
    
    def add_system_message(
        self,
        content: str,
        metadata: Optional[Dict[str, Any]] = None,
        persist: bool = True
    ) -> "ContextWindow":
        """Add system message."""
        return self.add_message('system', content, metadata, persist)
    
    def _prune_if_needed(self):
        """Prune context window if it exceeds limits."""
        if len(self._messages) <= self.max_turns:
            return
        
        # Pruning strategy: Keep first 5 messages (early context) + last N-5
        keep_first = 5
        keep_last = self.max_turns - keep_first
        
        pruned = self._messages[keep_first:-keep_last]
        self._pruned_count += len(pruned)
        
        self._messages = (
            self._messages[:keep_first] + 
            self._messages[-keep_last:]
        )
    
    def get_messages(
        self,
        limit: Optional[int] = None,
        roles: Optional[List[str]] = None
    ) -> List[ContextMessage]:
        """Get messages from context.
        
        Args:
            limit: Maximum messages to return
            roles: Filter by roles
            
        Returns:
            List of messages in order
        """
        messages = self._messages
        
        if roles:
            messages = [m for m in messages if m.role in roles]
        
        if limit:
            messages = messages[-limit:]
        
        return messages
    
    def get_llm_context(self, include_system: bool = True) -> List[Dict[str, str]]:
        """Get messages formatted for LLM.
        
        Args:
            include_system: Include system messages
            
        Returns:
            List of {'role': ..., 'content': ...} dicts
        """
        messages = self._messages
        if not include_system:
            messages = [m for m in messages if m.role != 'system']
        
        return [m.to_llm_format() for m in messages]
    
    def get_recent_messages(self, count: int = 5) -> List[ContextMessage]:
        """Get most recent messages."""
        return self._messages[-count:] if self._messages else []
    
    def clear(self) -> "ContextWindow":
        """Clear all messages."""
        self._messages = []
        self._pruned_count = 0
        return self
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            'session_uuid': self.session_uuid,
            'session_id': self.session_id,
            'max_turns': self.max_turns,
            'max_tokens': self.max_tokens,
            'messages': [
                {
                    'role': m.role,
                    'content': m.content,
                    'metadata': m.metadata
                }
                for m in self._messages
            ],
            'pruned_count': self._pruned_count,
            'message_count': len(self._messages)
        }
    
    def to_json(self) -> str:
        """Serialize to JSON."""
        return json.dumps(self.to_dict())
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ContextWindow":
        """Create from dictionary."""
        window = cls(
            session_uuid=data.get('session_uuid'),
            session_id=data.get('session_id'),
            max_turns=data.get('max_turns', 20),
            max_tokens=data.get('max_tokens')
        )
        
        window._pruned_count = data.get('pruned_count', 0)
        
        for msg_data in data.get('messages', []):
            window._messages.append(ContextMessage(
                role=msg_data['role'],
                content=msg_data['content'],
                metadata=msg_data.get('metadata', {})
            ))
        
        return window
    
    @classmethod
    def from_json(cls, json_str: str) -> "ContextWindow":
        """Create from JSON string."""
        return cls.from_dict(json.loads(json_str))
    
    def estimate_tokens(self) -> int:
        """Rough token estimation.
        
        Uses simple heuristic: ~4 chars per token.
        """
        total_chars = sum(len(m.content) for m in self._messages)
        return total_chars // 4
    
    def is_full(self) -> bool:
        """Check if context window is at capacity."""
        if len(self._messages) >= self.max_turns:
            return True
        if self.max_tokens and self.estimate_tokens() >= self.max_tokens:
            return True
        return False
    
    @property
    def message_count(self) -> int:
        """Number of messages in window."""
        return len(self._messages)
    
    @property
    def total_turns(self) -> int:
        """Total turns including pruned."""
        return len(self._messages) + self._pruned_count
    
    def get_summary(self) -> str:
        """Get short summary of context."""
        turns = self.total_turns
        tokens = self.estimate_tokens()
        user_msgs = sum(1 for m in self._messages if m.role == 'user')
        assistant_msgs = sum(1 for m in self._messages if m.role == 'assistant')
        
        return f"{turns} turns ({user_msgs} user, {assistant_msgs} assistant), ~{tokens} tokens"


class ContextWindowManager:
    """Manages multiple context windows for different sessions."""
    
    def __init__(self):
        """Initialize manager."""
        self._windows: Dict[str, ContextWindow] = {}
    
    def get_or_create(
        self,
        session_uuid: str,
        session_id: Optional[int] = None,
        **kwargs
    ) -> ContextWindow:
        """Get existing or create new context window."""
        if session_uuid in self._windows:
            return self._windows[session_uuid]
        
        window = ContextWindow(
            session_uuid=session_uuid,
            session_id=session_id,
            **kwargs
        ).load()
        
        self._windows[session_uuid] = window
        return window
    
    def get(self, session_uuid: str) -> Optional[ContextWindow]:
        """Get context window by UUID."""
        return self._windows.get(session_uuid)
    
    def remove(self, session_uuid: str) -> bool:
        """Remove context window."""
        if session_uuid in self._windows:
            del self._windows[session_uuid]
            return True
        return False
    
    def clear_all(self):
        """Clear all context windows."""
        self._windows.clear()
    
    def save_all(self):
        """Persist all context windows to database."""
        for window in self._windows.values():
            if window.session_uuid and window.session_id:
                store = get_conversation_store()
                with store._get_connection() as conn:
                    conn.execute(
                        """UPDATE sessions 
                            SET context_window = ?
                            WHERE id = ?""",
                        (window.to_json(), window.session_id)
                    )


# Global manager
_context_manager: Optional[ContextWindowManager] = None


def get_context_manager() -> ContextWindowManager:
    """Get or create global context manager."""
    global _context_manager
    if _context_manager is None:
        _context_manager = ContextWindowManager()
    return _context_manager
