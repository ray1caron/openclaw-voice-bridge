"""
Integration between OpenClaw Middleware and Response Filter

Connects the middleware's message tagging with the response filter's
decision-making to enable metadata-based filtering.
"""
from typing import Optional, Dict, Any, Callable
import structlog

from bridge.openclaw_middleware import (
    OpenClawMiddleware,
    TaggedMessage,
    MessageMetadata,
    MessageType,
    Speakability,
)
from bridge.response_filter import (
    ResponseFilter,
    ResponseType,
    FilterDecision,
    FilteredMessage,
)

logger = structlog.get_logger()


class MiddlewareResponseFilter:
    """
    Bridge between OpenClawMiddleware and ResponseFilter.
    
    Enables metadata-based filtering where messages tagged by the
    middleware bypass heuristic pattern matching.
    """
    
    def __init__(
        self,
        response_filter: Optional[ResponseFilter] = None,
        middleware: Optional[OpenClawMiddleware] = None,
    ):
        """
        Initialize the integrated filter.
        
        Args:
            response_filter: Existing ResponseFilter instance (or create new)
            middleware: OpenClawMiddleware instance (or create new)
        """
        self.response_filter = response_filter or ResponseFilter()
        self.middleware = middleware or OpenClawMiddleware()
        
        # Statistics
        self.metadata_filtered = 0
        self.heuristic_filtered = 0
        
        logger.info("middleware_response_filter.initialized")
    
    def process_message(self, message: Dict[str, Any]) -> FilteredMessage:
        """
        Process a message using metadata when available, heuristics otherwise.
        
        Args:
            message: Raw message from OpenClaw (may contain metadata)
            
        Returns:
            FilteredMessage with decision
        """
        # Check if message has middleware metadata
        if "metadata" in message:
            return self._process_with_metadata(message)
        else:
            return self._process_with_heuristics(message)
    
    def _process_with_metadata(self, message: Dict[str, Any]) -> FilteredMessage:
        """Process message using middleware metadata."""
        self.metadata_filtered += 1
        
        try:
            metadata_dict = message["metadata"]
            metadata = MessageMetadata.from_dict(metadata_dict)
            content = message.get("content", "")
            
            # Map speakability to filter decision
            decision_map = {
                Speakability.SPEAK: FilterDecision.SPEAK,
                Speakability.SILENT: FilterDecision.SILENT,
                Speakability.CONDITIONAL: FilterDecision.QUEUE,
            }
            
            decision = decision_map.get(
                metadata.speakable, 
                FilterDecision.SILENT
            )
            
            # Map message type to response type
            type_map = {
                MessageType.FINAL: ResponseType.FINAL,
                MessageType.THINKING: ResponseType.THINKING,
                MessageType.TOOL_CALL: ResponseType.TOOL_CALL,
                MessageType.TOOL_RESULT: ResponseType.TOOL_CALL,
                MessageType.PLANNING: ResponseType.PLANNING,
                MessageType.PROGRESS: ResponseType.PROGRESS,
                MessageType.ERROR: ResponseType.ERROR,
            }
            
            response_type = type_map.get(
                metadata.message_type,
                ResponseType.UNKNOWN
            )
            
            logger.debug(
                "metadata_filter.applied",
                message_type=metadata.message_type.value,
                decision=decision.value,
            )
            
            return FilteredMessage(
                original=message,
                response_type=response_type,
                decision=decision,
                confidence=metadata.confidence,
                filtered_text=content if decision == FilterDecision.SPEAK else None,
                speak_score=1.0 if decision == FilterDecision.SPEAK else 0.0,
            )
            
        except Exception as e:
            logger.error("metadata_filter.failed", error=str(e))
            # Fall back to heuristics on metadata error
            return self._process_with_heuristics(message)
    
    def _process_with_heuristics(self, message: Dict[str, Any]) -> FilteredMessage:
        """Process message using existing heuristic filter."""
        self.heuristic_filtered += 1
        return self.response_filter.filter_message(message)
    
    def get_stats(self) -> Dict[str, Any]:
        """Get combined statistics."""
        return {
            "metadata_filtered": self.metadata_filtered,
            "heuristic_filtered": self.heuristic_filtered,
            "total_processed": self.metadata_filtered + self.heuristic_filtered,
            "metadata_ratio": (
                self.metadata_filtered / (self.metadata_filtered + self.heuristic_filtered)
                if (self.metadata_filtered + self.heuristic_filtered) > 0 else 0
            ),
            "filter_stats": self.response_filter.get_stats(),
            "middleware_stats": self.middleware.get_stats(),
        }
    
    def reset_stats(self) -> None:
        """Reset all statistics."""
        self.metadata_filtered = 0
        self.heuristic_filtered = 0
        self.response_filter.reset_stats()
        self.middleware.reset_stats()


# Convenience function for quick filtering
def filter_message(
    message: Dict[str, Any],
    session_id: Optional[str] = None,
) -> FilteredMessage:
    """
    Convenience function to filter a single message.
    
    Creates a MiddlewareResponseFilter and processes the message.
    
    Args:
        message: The message to filter
        session_id: Optional session ID for context
        
    Returns:
        FilteredMessage with decision
    """
    middleware = OpenClawMiddleware(session_id=session_id)
    integrated_filter = MiddlewareResponseFilter(middleware=middleware)
    return integrated_filter.process_message(message)
