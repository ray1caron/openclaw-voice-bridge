"""
Tests for Middleware-ResponseFilter Integration

Tests the integration between OpenClawMiddleware and ResponseFilter,
ensuring metadata-based filtering works correctly.
"""
import pytest
from unittest.mock import Mock, patch

from bridge.middleware_integration import (
    MiddlewareResponseFilter,
    filter_message,
)
from bridge.openclaw_middleware import (
    OpenClawMiddleware,
    MessageType,
    Speakability,
    TaggedMessage,
)
from bridge.response_filter import (
    ResponseFilter,
    ResponseType,
    FilterDecision,
)


class TestMiddlewareResponseFilter:
    """Test MiddlewareResponseFilter class."""
    
    def test_initialization(self):
        """Test creating integrated filter."""
        middleware = OpenClawMiddleware(session_id="test-123")
        response_filter = ResponseFilter()
        
        integrated = MiddlewareResponseFilter(
            response_filter=response_filter,
            middleware=middleware,
        )
        
        assert integrated.middleware == middleware
        assert integrated.response_filter == response_filter
        assert integrated.metadata_filtered == 0
        assert integrated.heuristic_filtered == 0
    
    def test_process_message_with_metadata(self):
        """Test processing message with middleware metadata."""
        middleware = OpenClawMiddleware()
        integrated = MiddlewareResponseFilter(middleware=middleware)
        
        # Create a tagged message
        tagged = middleware.create_final_message("Hello world", confidence=0.95)
        
        # Simulate message with metadata
        message = {
            "content": tagged.content,
            "metadata": tagged.metadata.to_dict(),
        }
        
        result = integrated.process_message(message)
        
        assert result.decision == FilterDecision.SPEAK
        assert result.response_type == ResponseType.FINAL
        assert result.confidence == 0.95
        assert result.filtered_text == "Hello world"
        assert integrated.metadata_filtered == 1
    
    def test_process_tool_call_with_metadata(self):
        """Test that tool calls are filtered as silent."""
        middleware = OpenClawMiddleware()
        integrated = MiddlewareResponseFilter(middleware=middleware)
        
        # Create a tool call message
        tagged = middleware.create_tool_call_message(
            "web_search",
            {"query": "weather"}
        )
        
        message = {
            "content": tagged.content,
            "metadata": tagged.metadata.to_dict(),
        }
        
        result = integrated.process_message(message)
        
        assert result.decision == FilterDecision.SILENT
        assert result.response_type == ResponseType.TOOL_CALL
        assert integrated.metadata_filtered == 1
    
    def test_process_thinking_with_metadata(self):
        """Test that thinking messages are filtered as silent."""
        middleware = OpenClawMiddleware()
        integrated = MiddlewareResponseFilter(middleware=middleware)
        
        tagged = middleware.create_thinking_message("Let me analyze this...")
        
        message = {
            "content": tagged.content,
            "metadata": tagged.metadata.to_dict(),
        }
        
        result = integrated.process_message(message)
        
        assert result.decision == FilterDecision.SILENT
        assert result.response_type == ResponseType.THINKING
    
    def test_process_error_user_facing(self):
        """Test that user-facing errors are spoken."""
        middleware = OpenClawMiddleware()
        integrated = MiddlewareResponseFilter(middleware=middleware)
        
        tagged = middleware.create_error_message(
            "I couldn't find that information",
            user_facing=True
        )
        
        message = {
            "content": tagged.content,
            "metadata": tagged.metadata.to_dict(),
        }
        
        result = integrated.process_message(message)
        
        assert result.decision == FilterDecision.SPEAK
        assert result.response_type == ResponseType.ERROR
    
    def test_process_error_internal(self):
        """Test that internal errors are silent."""
        middleware = OpenClawMiddleware()
        integrated = MiddlewareResponseFilter(middleware=middleware)
        
        tagged = middleware.create_error_message(
            "Database connection timeout",
            user_facing=False
        )
        
        message = {
            "content": tagged.content,
            "metadata": tagged.metadata.to_dict(),
        }
        
        result = integrated.process_message(message)
        
        assert result.decision == FilterDecision.SILENT
        assert result.response_type == ResponseType.ERROR
    
    def test_fallback_to_heuristics(self):
        """Test that messages without metadata use heuristic filtering."""
        response_filter = ResponseFilter()
        integrated = MiddlewareResponseFilter(response_filter=response_filter)
        
        # Message without metadata - should use heuristics
        message = {
            "text": "Let me search for that information...",
        }
        
        result = integrated.process_message(message)
        
        # Should be detected as thinking and filtered
        assert result.decision == FilterDecision.SILENT
        assert integrated.heuristic_filtered == 1
        assert integrated.metadata_filtered == 0
    
    def test_get_stats(self):
        """Test getting combined statistics."""
        middleware = OpenClawMiddleware(session_id="test-123")
        integrated = MiddlewareResponseFilter(middleware=middleware)
        
        # Process some messages
        final_msg = middleware.create_final_message("Hello")
        integrated.process_message({
            "content": final_msg.content,
            "metadata": final_msg.metadata.to_dict(),
        })
        
        stats = integrated.get_stats()
        
        assert stats["metadata_filtered"] == 1
        assert stats["total_processed"] == 1
        assert "filter_stats" in stats
        assert "middleware_stats" in stats


class TestConvenienceFunction:
    """Test the filter_message convenience function."""
    
    def test_filter_message_with_metadata(self):
        """Test filtering a message with metadata."""
        middleware = OpenClawMiddleware()
        tagged = middleware.create_final_message("The answer is 42", confidence=0.95)
        
        message = {
            "content": tagged.content,
            "metadata": tagged.metadata.to_dict(),
        }
        
        result = filter_message(message, session_id="test-session")
        
        assert result.decision == FilterDecision.SPEAK
        assert result.confidence == 0.95
        assert result.filtered_text == "The answer is 42"
    
    def test_filter_message_without_metadata(self):
        """Test filtering a message without metadata."""
        message = {"text": "Here is the result: 42"}
        
        result = filter_message(message)
        
        # Should use heuristics and detect as final
        assert result.decision == FilterDecision.SPEAK
        assert result.response_type == ResponseType.FINAL


class TestIntegrationScenarios:
    """Test realistic integration scenarios."""
    
    def test_weather_query_flow(self):
        """Test complete flow for weather query."""
        middleware = OpenClawMiddleware(session_id="weather-session")
        integrated = MiddlewareResponseFilter(middleware=middleware)
        
        # 1. Thinking message (silent)
        thinking = middleware.create_thinking_message("Let me check the weather...")
        result = integrated.process_message({
            "content": thinking.content,
            "metadata": thinking.metadata.to_dict(),
        })
        assert result.decision == FilterDecision.SILENT
        
        # 2. Tool call (silent)
        tool_call = middleware.create_tool_call_message(
            "get_weather",
            {"location": "San Francisco"}
        )
        result = integrated.process_message({
            "content": tool_call.content,
            "metadata": tool_call.metadata.to_dict(),
        })
        assert result.decision == FilterDecision.SILENT
        
        # 3. Tool result (silent)
        tool_result = middleware.create_tool_result_message(
            "get_weather",
            {"temp": 72, "condition": "sunny"}
        )
        result = integrated.process_message({
            "content": tool_result.content,
            "metadata": tool_result.metadata.to_dict(),
        })
        assert result.decision == FilterDecision.SILENT
        
        # 4. Final response (spoken)
        final = middleware.create_final_message(
            "It's 72 degrees and sunny in San Francisco",
            confidence=0.95
        )
        result = integrated.process_message({
            "content": final.content,
            "metadata": final.metadata.to_dict(),
        })
        assert result.decision == FilterDecision.SPEAK
        assert result.filtered_text == "It's 72 degrees and sunny in San Francisco"
    
    def test_multi_tool_chain(self):
        """Test handling multiple tools in sequence."""
        middleware = OpenClawMiddleware()
        
        # Simulate: search → summarize → format
        tools = [
            ("search", {"query": "AI news"}),
            ("summarize", {"text": "...long text..."}),
            ("format", {"style": "bullet_points"}),
        ]
        
        for tool_name, params in tools:
            call_msg = middleware.create_tool_call_message(tool_name, params)
            assert call_msg.metadata.speakable == Speakability.SILENT
            assert middleware.is_in_tool_call()
        
        # Complete tools in reverse order
        for tool_name, _ in reversed(tools):
            result_msg = middleware.create_tool_result_message(
                tool_name,
                {"status": "success"}
            )
            assert result_msg.metadata.speakable == Speakability.SILENT
        
        assert not middleware.is_in_tool_call()
        
        # Final response
        final = middleware.create_final_message("Here are the top AI news stories...")
        assert final.metadata.speakable == Speakability.SPEAK
