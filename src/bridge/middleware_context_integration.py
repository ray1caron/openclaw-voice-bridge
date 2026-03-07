"""Middleware Context Integration - Issue #22

Links OpenClawMiddleware with ContextWindowManager to provide
persistent conversation context to OpenClaw.

This module implements Phase 2 of Sprint 3 integration:
- Middleware receives context from database via ContextWindow
- Context passed to OpenClaw on each message
- Assistant responses captured back to context
"""

from typing import Optional, Dict, Any, List
import structlog

from bridge.openclaw_middleware import OpenClawMiddleware, TaggedMessage, MessageType, Speakability
from bridge.context_window import ContextWindow, ContextMessage, get_context_manager
from bridge.history_manager import get_history_manager
from bridge.session_manager import get_session_manager

logger = structlog.get_logger()


class MiddlewareContextIntegration:
    """Integrates middleware with context window persistence.
    
    Usage:
        # On WebSocket connect
        integration = MiddlewareContextIntegration(session_uuid)
        
        # Before sending to OpenClaw
        context = integration.get_context_for_openclaw()
        
        # After receiving response
        integration.add_assistant_response(response_text, metadata)
    """
    
    def __init__(
        self,
        session_uuid: str,
        session_id: Optional[int] = None,
        max_turns: int = 20
    ):
        """Initialize context integration.
        
        Args:
            session_uuid: Bridge session UUID
            session_id: Database session ID (optional, fetched if not provided)
            max_turns: Max conversation turns to include in context
        """
        self.session_uuid = session_uuid
        self.session_id = session_id
        self.max_turns = max_turns
        self._context_window: Optional[ContextWindow] = None
        
        # Lazy initialization
        self._ensure_context_window()
        
        logger.info(
            "middleware_context.initialized",
            session_uuid=session_uuid,
            max_turns=max_turns
        )
    
    def _ensure_context_window(self) -> ContextWindow:
        """Get or create context window for this session."""
        if self._context_window is not None:
            return self._context_window
        
        # Fetch session_id if not provided
        if self.session_id is None:
            session = get_session_manager().get_session(self.session_uuid)
            if session:
                self.session_id = session.id
        
        # Get context window from manager
        self._context_window = get_context_manager().get_or_create(
            session_uuid=self.session_uuid,
            session_id=self.session_id,
            max_turns=self.max_turns
        )
        
        logger.debug(
            "middleware_context.context_loaded",
            session_uuid=self.session_uuid,
            message_count=self._context_window.message_count
        )
        
        return self._context_window
    
    def get_context_for_openclaw(
        self,
        include_system: bool = True
    ) -> List[Dict[str, str]]:
        """Get context formatted for OpenClaw LLM.
        
        Args:
            include_system: Include system messages in context
            
        Returns:
            List of {'role': ..., 'content': ...} dicts
        """
        window = self._ensure_context_window()
        return window.get_llm_context(include_system=include_system)
    
    def add_user_message(
        self,
        content: str,
        message_type: Optional[str] = None,
        speakability: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None
    ) -> None:
        """Add user message to context and persist to database.
        
        Args:
            content: Message content
            message_type: OpenClaw message type (final, thinking, etc.)
            speakability: Speakability flag (speak, silent, conditional)
            metadata: Additional metadata
        """
        window = self._ensure_context_window()
        
        # Build metadata
        msg_metadata = metadata or {}
        if message_type:
            msg_metadata['message_type'] = message_type
        if speakability:
            msg_metadata['speakability'] = speakability
        
        window.add_user_message(content, metadata=msg_metadata)
        
        logger.debug(
            "middleware_context.user_message_added",
            session_uuid=self.session_uuid,
            content_preview=content[:50] if content else ""
        )
    
    def add_assistant_response(
        self,
        content: str,
        message_type: Optional[str] = None,
        speakability: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        tool_calls: Optional[Dict[str, Any]] = None
    ) -> None:
        """Add assistant response to context and persist.
        
        Args:
            content: Response content
            message_type: OpenClaw message type
            speakability: Speakability flag  
            metadata: Additional metadata
            tool_calls: Tool calls made in this response
        """
        window = self._ensure_context_window()
        
        # Build metadata
        msg_metadata = metadata or {}
        if message_type:
            msg_metadata['message_type'] = message_type
        if speakability:
            msg_metadata['speakability'] = speakability
        if tool_calls:
            msg_metadata['tool_calls'] = tool_calls
        
        window.add_assistant_message(content, metadata=msg_metadata)
        
        logger.debug(
            "middleware_context.assistant_response_added",
            session_uuid=self.session_uuid,
            content_preview=content[:50] if content else "",
            message_type=message_type
        )
    
    def add_tool_call(
        self,
        tool_name: str,
        params: Dict[str, Any],
        result: Optional[Any] = None,
        error: Optional[str] = None
    ) -> None:
        """Add tool call to context.
        
        Args:
            tool_name: Name of tool executed
            params: Tool parameters
            result: Tool result (if successful)
            error: Error message (if failed)
        """
        window = self._ensure_context_window()
        
        # Format tool call as assistant message
        content = f"[Tool: {tool_name}]"
        metadata = {
            'message_type': 'tool_call',
            'speakability': 'silent',
            'tool_name': tool_name,
            'tool_params': params,
        }
        
        if result is not None:
            metadata['tool_result'] = result
        if error:
            metadata['tool_error'] = error
        
        window.add_assistant_message(content, metadata=metadata)
        
        logger.debug(
            "middleware_context.tool_call_added",
            session_uuid=self.session_uuid,
            tool_name=tool_name
        )
    
    def get_message_count(self) -> int:
        """Get number of messages in context."""
        if self._context_window is None:
            return 0
        return self._context_window.message_count
    
    def get_context_summary(self) -> str:
        """Get summary of context."""
        if self._context_window is None:
            return "No context loaded"
        return self._context_window.get_summary()
    
    def to_dict(self) -> Dict[str, Any]:
        """Export context for serialization."""
        if self._context_window is None:
            return {
                'session_uuid': self.session_uuid,
                'session_id': self.session_id,
                'messages': []
            }
        return self._context_window.to_dict()


class ContextAwareMiddleware(OpenClawMiddleware):
    """OpenClawMiddleware with context window integration.
    
    Extends base middleware to automatically manage context
    persistence for the conversation.
    
    Usage:
        middleware = ContextAwareMiddleware(session_uuid)
        middleware.initialize_context()  # Load existing context
        
        # Process message with automatic context
        response = await middleware.process_with_context(user_message)
    """
    
    def __init__(
        self,
        session_uuid: str,
        session_id: Optional[int] = None,
        max_context_turns: int = 20
    ):
        """Initialize context-aware middleware.
        
        Args:
            session_uuid: Bridge session UUID
            session_id: Database session ID
            max_context_turns: Max turns in context window
        """
        super().__init__(session_id=session_uuid)
        
        self.session_uuid = session_uuid
        self.bridge_session_id = session_id
        self.context_integration = MiddlewareContextIntegration(
            session_uuid=session_uuid,
            session_id=session_id,
            max_turns=max_context_turns
        )
    
    def initialize_context(self) -> None:
        """Load existing context from database."""
        self.context_integration._ensure_context_window()
        logger.info(
            "context_middleware.initialized",
            session_uuid=self.session_uuid,
            message_count=self.context_integration.get_message_count()
        )
    
    def get_context(self) -> List[Dict[str, str]]:
        """Get context for OpenClaw."""
        return self.context_integration.get_context_for_openclaw()
    
    def add_user_message(
        self,
        content: str,
        message_type: MessageType = MessageType.FINAL
    ) -> None:
        """Add user message to context with metadata."""
        speakability = Speakability.SPEAK if message_type == MessageType.FINAL else Speakability.SILENT
        self.context_integration.add_user_message(
            content=content,
            message_type=message_type.value,
            speakability=speakability.value
        )
    
    def add_response(
        self,
        tagged_message: TaggedMessage
    ) -> None:
        """Add tagged response to context."""
        metadata = tagged_message.metadata.to_dict()
        self.context_integration.add_assistant_response(
            content=tagged_message.content,
            message_type=metadata.get('message_type'),
            speakability=metadata.get('speakable'),
            metadata=metadata
        )
    
    def process_with_context(
        self,
        user_content: str,
        on_message: Optional[callable] = None
    ) -> TaggedMessage:
        """Process user message with full context.
        
        This is the main entry point for context-aware processing:
        1. Load existing context
        2. Add user message to context
        3. Pass context to OpenClaw
        4. Capture response to context
        
        Args:
            user_content: User's message
            on_message: Optional callback for intermediate messages
            
        Returns:
            TaggedMessage with assistant response
        """
        # Ensure context is loaded
        self.initialize_context()
        
        # Add user message
        self.add_user_message(user_content, MessageType.FINAL)
        
        # Get context for OpenClaw
        context = self.get_context()
        
        logger.info(
            "context_middleware.processing",
            session_uuid=self.session_uuid,
            context_turns=len(context)
        )
        
        # TODO: Call OpenClaw with context
        # For now, create a mock response for integration testing
        # In production, this would integrate with actual OpenClaw API
        
        # Create response (this would come from OpenClaw)
        response = self.create_final_message(
            f"[Processing: {user_content[:50]}...]"
        )
        
        # Add to context
        self.add_response(response)
        
        return response


def get_context_aware_middleware(
    session_uuid: str,
    session_id: Optional[int] = None,
    max_context_turns: int = 20
) -> ContextAwareMiddleware:
    """Factory for context-aware middleware.
    
    Args:
        session_uuid: Bridge session UUID
        session_id: Database session ID (optional)
        max_context_turns: Max turns in context window
        
    Returns:
        ContextAwareMiddleware instance
    """
    middleware = ContextAwareMiddleware(
        session_uuid=session_uuid,
        session_id=session_id,
        max_context_turns=max_context_turns
    )
    middleware.initialize_context()
    return middleware


# Integration points for websocket_client.py
# These functions can be called from the websocket client to integrate
# context management into the message flow

class WebSocketContextAdapter:
    """Adapter for integrating context with WebSocket client.
    
    Usage in websocket_client.py:
        from bridge.middleware_context_integration import WebSocketContextAdapter
        
        class OpenClawWebSocketClient:
            def __init__(self, ...):
                self.context_adapter: Optional[WebSocketContextAdapter] = None
            
            async def connect(self):
                # After session creation
                if self.enable_persistence:
                    self.context_adapter = WebSocketContextAdapter(
                        self.voice_session_id
                    )
                    self.context_adapter.initialize()
            
            def on_message(self, data):
                # Before sending to OpenClaw
                context = self.context_adapter.get_context()
                # ... pass context to OpenClaw
                
                # After receiving response
                self.context_adapter.add_assistant_response(response)
    """
    
    def __init__(self, session_uuid: str):
        """Initialize adapter."""
        self.session_uuid = session_uuid
        self.middleware: Optional[ContextAwareMiddleware] = None
    
    def initialize(self, session_id: Optional[int] = None) -> None:
        """Initialize middleware with context."""
        self.middleware = get_context_aware_middleware(
            session_uuid=self.session_uuid,
            session_id=session_id
        )
    
    def get_context(self) -> List[Dict[str, str]]:
        """Get context for current message."""
        if self.middleware is None:
            return []
        return self.middleware.get_context()
    
    def add_user_message(self, content: str) -> None:
        """Add user message before sending to OpenClaw."""
        if self.middleware:
            self.middleware.add_user_message(content)
    
    def add_assistant_response(
        self,
        content: str,
        message_type: str = "final",
        speakability: str = "speak"
    ) -> None:
        """Add assistant response after receiving from OpenClaw."""
        if self.middleware:
            self.middleware.context_integration.add_assistant_response(
                content=content,
                message_type=message_type,
                speakability=speakability
            )
    
    def get_summary(self) -> str:
        """Get context summary."""
        if self.middleware:
            return self.middleware.context_integration.get_context_summary()
        return "No context"
