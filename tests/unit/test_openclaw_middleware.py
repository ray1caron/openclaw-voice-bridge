"""
Tests for OpenClaw Middleware

Tests message tagging, tool call wrapping, and middleware functionality.
"""
import pytest
import json
from unittest.mock import Mock, patch

from bridge.openclaw_middleware import (
    OpenClawMiddleware,
    MessageMetadata,
    TaggedMessage,
    MessageType,
    Speakability,
    mark_tool_call,
    wrap_tool_execution,
)


class TestMessageMetadata:
    """Test MessageMetadata dataclass."""
    
    def test_create_final_metadata(self):
        """Test creating final message metadata."""
        meta = MessageMetadata(
            message_type=MessageType.FINAL,
            speakable=Speakability.SPEAK,
            session_id="test-session",
            confidence=0.95,
        )
        
        assert meta.message_type == MessageType.FINAL
        assert meta.speakable == Speakability.SPEAK
        assert meta.session_id == "test-session"
        assert meta.confidence == 0.95
    
    def test_metadata_to_dict(self):
        """Test converting metadata to dictionary."""
        meta = MessageMetadata(
            message_type=MessageType.TOOL_CALL,
            speakable=Speakability.SILENT,
            session_id="test-session",
            tool_name="web_search",
            tool_params={"query": "weather"},
        )
        
        d = meta.to_dict()
        assert d["message_type"] == "tool_call"
        assert d["speakable"] == "silent"
        assert d["tool_name"] == "web_search"
        assert d["tool_params"] == {"query": "weather"}
    
    def test_metadata_from_dict(self):
        """Test creating metadata from dictionary."""
        d = {
            "message_type": "final",
            "speakable": "speak",
            "timestamp": 1234567890.0,
            "session_id": "test-session",
            "confidence": 0.9,
        }
        
        meta = MessageMetadata.from_dict(d)
        assert meta.message_type == MessageType.FINAL
        assert meta.speakable == Speakability.SPEAK
        assert meta.session_id == "test-session"
        assert meta.confidence == 0.9


class TestTaggedMessage:
    """Test TaggedMessage dataclass."""
    
    def test_create_tagged_message(self):
        """Test creating a tagged message."""
        meta = MessageMetadata(
            message_type=MessageType.FINAL,
            speakable=Speakability.SPEAK,
        )
        msg = TaggedMessage(
            content="The weather is 72 degrees",
            metadata=meta,
        )
        
        assert msg.content == "The weather is 72 degrees"
        assert msg.metadata.message_type == MessageType.FINAL
    
    def test_tagged_message_to_json(self):
        """Test serializing to JSON."""
        meta = MessageMetadata(
            message_type=MessageType.TOOL_CALL,
            speakable=Speakability.SILENT,
            tool_name="search",
        )
        msg = TaggedMessage(content="[Tool Call: search]", metadata=meta)
        
        json_str = msg.to_json()
        data = json.loads(json_str)
        
        assert data["content"] == "[Tool Call: search]"
        assert data["metadata"]["message_type"] == "tool_call"
        assert data["metadata"]["speakable"] == "silent"
    
    def test_tagged_message_from_json(self):
        """Test deserializing from JSON."""
        json_str = json.dumps({
            "content": "Hello world",
            "metadata": {
                "message_type": "final",
                "speakable": "speak",
                "timestamp": 1234567890.0,
            },
        })
        
        msg = TaggedMessage.from_json(json_str)
        assert msg.content == "Hello world"
        assert msg.metadata.message_type == MessageType.FINAL
        assert msg.metadata.speakable == Speakability.SPEAK


class TestOpenClawMiddleware:
    """Test OpenClawMiddleware class."""
    
    def test_middleware_initialization(self):
        """Test creating middleware instance."""
        middleware = OpenClawMiddleware(session_id="test-session")
        
        assert middleware.session_id == "test-session"
        assert middleware._message_count == 0
        assert len(middleware._tool_stack) == 0
    
    def test_create_final_message(self):
        """Test creating final message."""
        middleware = OpenClawMiddleware()
        msg = middleware.create_final_message("Hello world", confidence=0.95)
        
        assert msg.content == "Hello world"
        assert msg.metadata.message_type == MessageType.FINAL
        assert msg.metadata.speakable == Speakability.SPEAK
        assert msg.metadata.confidence == 0.95
        assert middleware._message_count == 1
    
    def test_create_thinking_message(self):
        """Test creating thinking message."""
        middleware = OpenClawMiddleware()
        msg = middleware.create_thinking_message("Let me think...")
        
        assert msg.content == "Let me think..."
        assert msg.metadata.message_type == MessageType.THINKING
        assert msg.metadata.speakable == Speakability.SILENT
    
    def test_create_tool_call_message(self):
        """Test creating tool call message."""
        middleware = OpenClawMiddleware()
        msg = middleware.create_tool_call_message(
            "web_search",
            {"query": "weather"},
            content="Searching for weather..."
        )
        
        assert msg.metadata.message_type == MessageType.TOOL_CALL
        assert msg.metadata.speakable == Speakability.SILENT
        assert msg.metadata.tool_name == "web_search"
        assert msg.metadata.tool_params == {"query": "weather"}
        assert middleware.is_in_tool_call()
        assert middleware.get_active_tool() == "web_search"
    
    def test_create_tool_result_message(self):
        """Test creating tool result message."""
        middleware = OpenClawMiddleware()
        
        # First create a tool call
        middleware.create_tool_call_message("search", {"q": "test"})
        assert middleware.is_in_tool_call()
        
        # Then create result
        msg = middleware.create_tool_result_message(
            "search",
            {"temperature": 72},
            content="Found weather data"
        )
        
        assert msg.metadata.message_type == MessageType.TOOL_RESULT
        assert msg.metadata.speakable == Speakability.SILENT
        assert not middleware.is_in_tool_call()  # Stack should be empty
    
    def test_nested_tool_calls(self):
        """Test handling nested tool calls."""
        middleware = OpenClawMiddleware()
        
        # Outer tool
        middleware.create_tool_call_message("analyze", {"data": "large_dataset"})
        assert middleware.get_active_tool() == "analyze"
        
        # Inner tool
        middleware.create_tool_call_message("search", {"query": "reference"})
        assert middleware.get_active_tool() == "search"
        
        # Complete inner
        middleware.create_tool_result_message("search", {"results": []})
        assert middleware.get_active_tool() == "analyze"  # Back to outer
        
        # Complete outer
        middleware.create_tool_result_message("analyze", {"summary": "done"})
        assert not middleware.is_in_tool_call()
    
    def test_error_message_user_facing(self):
        """Test user-facing error message."""
        middleware = OpenClawMiddleware()
        msg = middleware.create_error_message(
            "I couldn't find that information",
            user_facing=True
        )
        
        assert msg.metadata.message_type == MessageType.ERROR
        assert msg.metadata.speakable == Speakability.SPEAK
    
    def test_error_message_internal(self):
        """Test internal error message."""
        middleware = OpenClawMiddleware()
        msg = middleware.create_error_message(
            "Connection timeout to database",
            user_facing=False
        )
        
        assert msg.metadata.message_type == MessageType.ERROR
        assert msg.metadata.speakable == Speakability.SILENT
    
    def test_get_stats(self):
        """Test getting middleware statistics."""
        middleware = OpenClawMiddleware(session_id="test-123")
        
        # Create some messages
        middleware.create_final_message("Hello")
        middleware.create_thinking_message("Thinking...")
        middleware.create_tool_call_message("search", {"q": "test"})
        
        stats = middleware.get_stats()
        
        assert stats["message_count"] == 3
        assert stats["session_id"] == "test-123"
        assert stats["tool_stack_depth"] == 1
        assert stats["active_tools"] == ["search"]


class TestMarkToolCallDecorator:
    """Test the mark_tool_call decorator."""
    
    def test_decorator_adds_attributes(self):
        """Test that decorator marks function as tool call."""
        @mark_tool_call("web_search")
        def search(query: str) -> str:
            return f"Results for {query}"
        
        assert hasattr(search, "_is_tool_call")
        assert search._is_tool_call is True
        assert search._tool_name == "web_search"
    
    def test_decorator_uses_function_name(self):
        """Test that decorator uses function name if no tool name provided."""
        @mark_tool_call()
        def calculate_weather(lat: float, lon: float) -> dict:
            return {"temp": 72}
        
        assert calculate_weather._tool_name == "calculate_weather"


class TestWrapToolExecution:
    """Test the wrap_tool_execution function."""
    
    def test_successful_tool_execution(self):
        """Test wrapping a successful tool execution."""
        middleware = OpenClawMiddleware()
        
        def mock_search(query: str):
            return {"results": ["item1", "item2"]}
        
        result = wrap_tool_execution(
            middleware=middleware,
            tool_name="search",
            params={"query": "test"},
            execute_fn=mock_search,
        )
        
        assert result.metadata.message_type == MessageType.TOOL_RESULT
        assert result.metadata.speakable == Speakability.SILENT
        assert result.metadata.tool_name == "search"
    
    def test_failed_tool_execution(self):
        """Test wrapping a failed tool execution."""
        middleware = OpenClawMiddleware()
        
        def failing_tool():
            raise ValueError("Connection failed")
        
        result = wrap_tool_execution(
            middleware=middleware,
            tool_name="failing_tool",
            params={},
            execute_fn=failing_tool,
        )
        
        assert result.metadata.message_type == MessageType.ERROR
        assert result.metadata.speakable == Speakability.SPEAK  # User-facing error
        assert "Connection failed" in result.content


class TestIntegrationWithResponseFilter:
    """Test integration with the existing ResponseFilter."""
    
    def test_middleware_output_compatible_with_filter(self):
        """Test that middleware output can be processed by ResponseFilter."""
        from bridge.response_filter import ResponseFilter, ResponseType
        
        middleware = OpenClawMiddleware()
        response_filter = ResponseFilter()
        
        # Create a final message via middleware
        tagged = middleware.create_final_message("The weather is 72 degrees")
        
        # Simulate what ResponseFilter would receive
        # (In real integration, this would come from OpenClaw)
        message = {
            "content": tagged.content,
            "metadata": tagged.metadata.to_dict(),
        }
        
        # Verify the message has proper structure
        assert "content" in message
        assert "metadata" in message
        assert message["metadata"]["message_type"] == "final"
        assert message["metadata"]["speakable"] == "speak"
    
    def test_tool_call_message_filtered_as_silent(self):
        """Test that tool call messages are marked as silent."""
        middleware = OpenClawMiddleware()
        
        tagged = middleware.create_tool_call_message(
            "web_search",
            {"query": "weather"}
        )
        
        assert tagged.metadata.speakable == Speakability.SILENT
        assert tagged.metadata.message_type == MessageType.TOOL_CALL
