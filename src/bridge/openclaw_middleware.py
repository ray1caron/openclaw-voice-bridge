"""
OpenClaw Middleware for Voice Bridge

Marks tool calls and internal processing as silent/non-speakable,
ensuring only final user-facing responses reach TTS.
"""
import enum
import json
import time
from dataclasses import dataclass, field, asdict
from typing import Optional, Dict, Any, Callable, List
from functools import wraps

import structlog

logger = structlog.get_logger()


class MessageType(enum.Enum):
    """Message types for OpenClaw protocol."""
    FINAL = "final"              # User-facing response (speak)
    THINKING = "thinking"          # Chain of thought (silent)
    TOOL_CALL = "tool_call"        # Tool execution start (silent)
    TOOL_RESULT = "tool_result"    # Tool execution result (silent)
    PLANNING = "planning"          # Internal planning (silent)
    PROGRESS = "progress"          # Progress update (silent)
    ERROR = "error"                # Error message (conditional)
    INTERRUPT = "interrupt"        # Interruption signal (silent)


class Speakability(enum.Enum):
    """Whether a message should be spoken."""
    SPEAK = "speak"                # Pass to TTS
    SILENT = "silent"              # Filter out
    CONDITIONAL = "conditional"    # Depends on content


@dataclass
class MessageMetadata:
    """Metadata attached to OpenClaw messages."""
    message_type: MessageType
    speakable: Speakability
    timestamp: float = field(default_factory=time.time)
    session_id: Optional[str] = None
    tool_name: Optional[str] = None
    tool_params: Optional[Dict] = None
    confidence: float = 1.0
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert metadata to dictionary."""
        return {
            "message_type": self.message_type.value,
            "speakable": self.speakable.value,
            "timestamp": self.timestamp,
            "session_id": self.session_id,
            "tool_name": self.tool_name,
            "tool_params": self.tool_params,
            "confidence": self.confidence,
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "MessageMetadata":
        """Create metadata from dictionary."""
        return cls(
            message_type=MessageType(data.get("message_type", "final")),
            speakable=Speakability(data.get("speakable", "speak")),
            timestamp=data.get("timestamp", time.time()),
            session_id=data.get("session_id"),
            tool_name=data.get("tool_name"),
            tool_params=data.get("tool_params"),
            confidence=data.get("confidence", 1.0),
        )


@dataclass
class TaggedMessage:
    """A message with metadata tagging."""
    content: str
    metadata: MessageMetadata
    
    def to_json(self) -> str:
        """Serialize to JSON."""
        return json.dumps({
            "content": self.content,
            "metadata": self.metadata.to_dict(),
        })
    
    @classmethod
    def from_json(cls, json_str: str) -> "TaggedMessage":
        """Deserialize from JSON."""
        data = json.loads(json_str)
        return cls(
            content=data["content"],
            metadata=MessageMetadata.from_dict(data["metadata"]),
        )


class OpenClawMiddleware:
    """
    Middleware for marking OpenClaw messages with speakability metadata.
    
    Wraps tool calls and internal processing as silent, ensuring only
    final user-facing responses are marked for TTS.
    """
    
    def __init__(self, session_id: Optional[str] = None):
        """Initialize middleware."""
        self.session_id = session_id
        self._tool_stack: List[str] = []  # Track nested tool calls
        self._message_count = 0
        logger.info("openclaw_middleware.initialized", session_id=session_id)
    
    def create_final_message(self, content: str, confidence: float = 1.0) -> TaggedMessage:
        """Create a final user-facing message (will be spoken)."""
        self._message_count += 1
        metadata = MessageMetadata(
            message_type=MessageType.FINAL,
            speakable=Speakability.SPEAK,
            session_id=self.session_id,
            confidence=confidence,
        )
        return TaggedMessage(content=content, metadata=metadata)
    
    def create_thinking_message(self, content: str) -> TaggedMessage:
        """Create a thinking/planning message (silent)."""
        self._message_count += 1
        metadata = MessageMetadata(
            message_type=MessageType.THINKING,
            speakable=Speakability.SILENT,
            session_id=self.session_id,
        )
        return TaggedMessage(content=content, metadata=metadata)
    
    def create_tool_call_message(
        self, 
        tool_name: str, 
        params: Dict[str, Any],
        content: Optional[str] = None
    ) -> TaggedMessage:
        """Create a tool call message (silent)."""
        self._message_count += 1
        self._tool_stack.append(tool_name)
        
        if content is None:
            content = f"[Tool Call: {tool_name}]"
        
        metadata = MessageMetadata(
            message_type=MessageType.TOOL_CALL,
            speakable=Speakability.SILENT,
            session_id=self.session_id,
            tool_name=tool_name,
            tool_params=params,
        )
        return TaggedMessage(content=content, metadata=metadata)
    
    def create_tool_result_message(
        self, 
        tool_name: str, 
        result: Any,
        content: Optional[str] = None
    ) -> TaggedMessage:
        """Create a tool result message (silent)."""
        self._message_count += 1
        
        # Pop from tool stack if this was the active tool
        if self._tool_stack and self._tool_stack[-1] == tool_name:
            self._tool_stack.pop()
        
        if content is None:
            content = f"[Tool Result: {tool_name}]"
        
        metadata = MessageMetadata(
            message_type=MessageType.TOOL_RESULT,
            speakable=Speakability.SILENT,
            session_id=self.session_id,
            tool_name=tool_name,
        )
        return TaggedMessage(content=content, metadata=metadata)
    
    def create_progress_message(self, content: str) -> TaggedMessage:
        """Create a progress update message (silent)."""
        self._message_count += 1
        metadata = MessageMetadata(
            message_type=MessageType.PROGRESS,
            speakable=Speakability.SILENT,
            session_id=self.session_id,
        )
        return TaggedMessage(content=content, metadata=metadata)
    
    def create_error_message(
        self, 
        content: str, 
        user_facing: bool = True
    ) -> TaggedMessage:
        """
        Create an error message.
        
        Args:
            content: Error message content
            user_facing: If True, will be spoken; if False, silent
        """
        self._message_count += 1
        speakable = Speakability.SPEAK if user_facing else Speakability.SILENT
        metadata = MessageMetadata(
            message_type=MessageType.ERROR,
            speakable=speakable,
            session_id=self.session_id,
        )
        return TaggedMessage(content=content, metadata=metadata)
    
    def is_in_tool_call(self) -> bool:
        """Check if currently inside a tool call chain."""
        return len(self._tool_stack) > 0
    
    def get_active_tool(self) -> Optional[str]:
        """Get the currently active tool name, if any."""
        return self._tool_stack[-1] if self._tool_stack else None
    
    def get_stats(self) -> Dict[str, Any]:
        """Get middleware statistics."""
        return {
            "message_count": self._message_count,
            "tool_stack_depth": len(self._tool_stack),
            "active_tools": self._tool_stack.copy(),
            "session_id": self.session_id,
        }


# Decorator for wrapping tool functions
def mark_tool_call(tool_name: Optional[str] = None):
    """
    Decorator to mark a function as a tool call.
    
    Usage:
        @mark_tool_call("web_search")
        def search(query: str) -> str:
            return results
    """
    def decorator(func):
        func._is_tool_call = True
        func._tool_name = tool_name or func.__name__
        return func
    return decorator


def wrap_tool_execution(
    middleware: OpenClawMiddleware,
    tool_name: str,
    params: Dict[str, Any],
    execute_fn: Callable,
) -> TaggedMessage:
    """
    Wrap a tool execution with middleware tagging.
    
    Args:
        middleware: OpenClawMiddleware instance
        tool_name: Name of the tool being called
        params: Tool parameters
        execute_fn: Function that executes the tool
        
    Returns:
        TaggedMessage with tool result
    """
    # Create tool call message (silent)
    call_msg = middleware.create_tool_call_message(tool_name, params)
    logger.debug("tool_call.marked", tool=tool_name, params=params)
    
    try:
        # Execute the tool
        result = execute_fn(**params)
        
        # Create tool result message (silent)
        result_msg = middleware.create_tool_result_message(
            tool_name, 
            result,
            content=f"[Tool Result: {tool_name}]"
        )
        logger.debug("tool_result.marked", tool=tool_name)
        return result_msg
        
    except Exception as e:
        # Create error message (user-facing)
        error_msg = middleware.create_error_message(
            f"Error executing {tool_name}: {str(e)}",
            user_facing=True
        )
        logger.error("tool_execution.failed", tool=tool_name, error=str(e))
        return error_msg