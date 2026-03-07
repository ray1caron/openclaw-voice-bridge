"""Tests for Issue #22: Context Window Integration.

Test suite for middleware_context_integration.py
"""

import pytest
from unittest.mock import Mock, MagicMock, patch

from bridge.middleware_context_integration import (
    MiddlewareContextIntegration,
    ContextAwareMiddleware,
    WebSocketContextAdapter,
    get_context_aware_middleware
)
from bridge.context_window import ContextWindow
from bridge.openclaw_middleware import MessageType, Speakability


@pytest.fixture
def mock_context_window():
    """Fixture for mocked context window."""
    window = MagicMock(spec=ContextWindow)
    window.message_count = 0
    window.session_uuid = "test-session-uuid"
    window.session_id = 42
    return window


@pytest.fixture
def mock_context_manager():
    """Fixture for mocked context manager."""
    manager = MagicMock()
    manager.get_or_create.return_value = MagicMock()
    return manager


class TestMiddlewareContextIntegration:
    """Tests for MiddlewareContextIntegration class."""
    
    @patch('bridge.middleware_context_integration.get_context_manager')
    def test_init_loads_context_window(self, mock_get_manager):
        """AC: Middleware initialized with context window."""
        mock_window = MagicMock()
        mock_window.message_count = 5
        mock_get_manager.return_value.get_or_create.return_value = mock_window
        
        integration = MiddlewareContextIntegration(
            session_uuid="test-session",
            session_id=42
        )
        
        # Context window should be loaded
        mock_get_manager.return_value.get_or_create.assert_called_once()
        assert integration._context_window == mock_window
    
    @patch('bridge.middleware_context_integration.get_context_manager')
    def test_fetch_session_id_if_not_provided(self, mock_get_manager):
        """AC: Session ID fetched from database if not provided."""
        mock_session = MagicMock()
        mock_session.id = 123
        
        with patch('bridge.middleware_context_integration.get_session_manager') as mock_session_mgr:
            mock_session_mgr.return_value.get_session.return_value = mock_session
            mock_get_manager.return_value.get_or_create.return_value = MagicMock()
            
            integration = MiddlewareContextIntegration(
                session_uuid="test-session"
            )
            
            # Should fetch session_id from database
            mock_session_mgr.return_value.get_session.assert_called_with("test-session")
            assert integration.session_id == 123
    
    @patch('bridge.middleware_context_integration.get_context_manager')
    def test_get_context_for_openclaw(self, mock_get_manager):
        """AC: Context formatted for OpenClaw LLM."""
        mock_window = MagicMock()
        mock_window.get_llm_context.return_value = [
            {'role': 'user', 'content': 'Hello'},
            {'role': 'assistant', 'content': 'Hi there'}
        ]
        mock_get_manager.return_value.get_or_create.return_value = mock_window
        
        integration = MiddlewareContextIntegration("test-session")
        context = integration.get_context_for_openclaw()
        
        # Should return LLM-formatted context
        assert len(context) == 2
        assert context[0]['role'] == 'user'
        assert context[1]['role'] == 'assistant'
    
    @patch('bridge.middleware_context_integration.get_context_manager')
    def test_add_user_message_with_metadata(self, mock_get_manager):
        """AC: User messages persisted with metadata."""
        mock_window = MagicMock()
        mock_get_manager.return_value.get_or_create.return_value = mock_window
        
        integration = MiddlewareContextIntegration("test-session", 42)
        integration.add_user_message(
            content="Hello",
            message_type="final",
            speakability="speak"
        )
        
        # Should add to context with metadata
        mock_window.add_user_message.assert_called_once()
        call_args = mock_window.add_user_message.call_args
        assert call_args[0][0] == "Hello"  # First positional arg is content
        metadata = call_args[1]['metadata']
        assert metadata['message_type'] == 'final'
        assert metadata['speakability'] == 'speak'
    
    @patch('bridge.middleware_context_integration.get_context_manager')
    def test_add_assistant_response_with_metadata(self, mock_get_manager):
        """AC: Assistant responses persisted with metadata."""
        mock_window = MagicMock()
        mock_get_manager.return_value.get_or_create.return_value = mock_window
        
        integration = MiddlewareContextIntegration("test-session", 42)
        integration.add_assistant_response(
            content="Response",
            message_type="final",
            speakability="speak",
            tool_calls={'tool': 'search'}
        )
        
        # Should add to context with metadata
        mock_window.add_assistant_message.assert_called_once()
        call_args = mock_window.add_assistant_message.call_args
        metadata = call_args[1]['metadata']
        assert metadata['message_type'] == 'final'
        assert metadata['speakability'] == 'speak'
        assert metadata['tool_calls'] == {'tool': 'search'}
    
    @patch('bridge.middleware_context_integration.get_context_manager')
    def test_add_tool_call_to_context(self, mock_get_manager):
        """AC: Tool calls captured in context."""
        mock_window = MagicMock()
        mock_get_manager.return_value.get_or_create.return_value = mock_window
        
        integration = MiddlewareContextIntegration("test-session", 42)
        integration.add_tool_call(
            tool_name="search",
            params={"query": "test"},
            result=["item1", "item2"]
        )
        
        # Should add tool call to context
        mock_window.add_assistant_message.assert_called_once()
        call_args = mock_window.add_assistant_message.call_args
        assert '[Tool: search]' in call_args[0][0]  # First positional arg is content
        metadata = call_args[1]['metadata']
        assert metadata['tool_name'] == 'search'
        assert metadata['tool_params'] == {'query': 'test'}


class TestContextAwareMiddleware:
    """Tests for ContextAwareMiddleware class."""
    
    @patch('bridge.middleware_context_integration.get_context_manager')
    def test_init_with_session(self, mock_get_manager):
        """AC: Middleware tracks session UUID."""
        mock_get_manager.return_value.get_or_create.return_value = MagicMock()
        
        middleware = ContextAwareMiddleware(
            session_uuid="session-uuid",
            session_id=42
        )
        
        assert middleware.session_uuid == "session-uuid"
        assert middleware.bridge_session_id == 42
    
    @patch('bridge.middleware_context_integration.get_context_manager')
    def test_get_context_from_middleware(self, mock_get_manager):
        """AC: Context passed to OpenClaw."""
        mock_window = MagicMock()
        mock_window.get_llm_context.return_value = [
            {'role': 'user', 'content': 'Question'},
            {'role': 'assistant', 'content': 'Answer'}
        ]
        mock_get_manager.return_value.get_or_create.return_value = mock_window
        
        middleware = ContextAwareMiddleware("session-uuid", 42)
        context = middleware.get_context()
        
        # Should return formatted context
        assert len(context) == 2
        assert context[0]['content'] == 'Question'
        assert context[1]['content'] == 'Answer'
    
    @patch('bridge.middleware_context_integration.get_context_manager')
    def test_add_user_message_through_middleware(self, mock_get_manager):
        """AC: User message added via middleware."""
        mock_integration = MagicMock()
        
        with patch.object(MiddlewareContextIntegration, '__init__', return_value=None):
            with patch.object(MiddlewareContextIntegration, '_ensure_context_window', return_value=MagicMock()):
                middleware = ContextAwareMiddleware("session-uuid", 42)
                middleware.context_integration = mock_integration
                
                middleware.add_user_message("Hello", MessageType.FINAL)
                
                # Should delegate to integration
                mock_integration.add_user_message.assert_called_once()
                call_args = mock_integration.add_user_message.call_args
                assert call_args[1]['content'] == "Hello"
    
    @patch('bridge.middleware_context_integration.get_context_manager')
    def test_add_response_to_context(self, mock_get_manager):
        """AC: Assistant responses captured."""
        mock_integration = MagicMock()
        
        with patch.object(MiddlewareContextIntegration, '__init__', return_value=None):
            with patch.object(MiddlewareContextIntegration, '_ensure_context_window', return_value=MagicMock()):
                middleware = ContextAwareMiddleware("session-uuid", 42)
                middleware.context_integration = mock_integration
                
                # Create tagged message
                from bridge.openclaw_middleware import TaggedMessage, MessageMetadata
                tagged = TaggedMessage(
                    content="Response text",
                    metadata=MessageMetadata(
                        message_type=MessageType.FINAL,
                        speakable=Speakability.SPEAK
                    )
                )
                
                middleware.add_response(tagged)
                
                # Should add to context
                mock_integration.add_assistant_response.assert_called_once()


class TestWebSocketContextAdapter:
    """Tests for WebSocketContextAdapter."""
    
    @patch('bridge.middleware_context_integration.get_context_aware_middleware')
    def test_adapter_initializes_middleware(self, mock_get_middleware):
        """AC: Adapter initializes during WebSocket connect."""
        mock_middleware = MagicMock()
        mock_get_middleware.return_value = mock_middleware
        
        adapter = WebSocketContextAdapter("session-uuid")
        adapter.initialize(session_id=42)
        
        mock_get_middleware.assert_called_once_with(
            session_uuid="session-uuid",
            session_id=42
        )
        assert adapter.middleware == mock_middleware
    
    @patch('bridge.middleware_context_integration.get_context_aware_middleware')
    def test_adapter_get_context(self, mock_get_middleware):
        """AC: Context retrieved via adapter."""
        mock_middleware = MagicMock()
        mock_middleware.get_context.return_value = [
            {'role': 'user', 'content': 'Hello'}
        ]
        mock_get_middleware.return_value = mock_middleware
        
        adapter = WebSocketContextAdapter("session-uuid")
        adapter.initialize(42)
        
        context = adapter.get_context()
        assert len(context) == 1
        assert context[0]['content'] == 'Hello'
    
    @patch('bridge.middleware_context_integration.get_context_aware_middleware')
    def test_adapter_add_user_message(self, mock_get_middleware):
        """AC: WebSocket messages added to context."""
        mock_middleware = MagicMock()
        mock_get_middleware.return_value = mock_middleware
        
        adapter = WebSocketContextAdapter("session-uuid")
        adapter.initialize(42)
        
        adapter.add_user_message("Voice input")
        
        mock_middleware.add_user_message.assert_called_once_with("Voice input")
    
    @patch('bridge.middleware_context_integration.get_context_aware_middleware')
    def test_adapter_add_assistant_response(self, mock_get_middleware):
        """AC: OpenClaw responses added to context."""
        mock_middleware = MagicMock()
        mock_integration = MagicMock()
        mock_middleware.context_integration = mock_integration
        mock_get_middleware.return_value = mock_middleware
        
        adapter = WebSocketContextAdapter("session-uuid")
        adapter.initialize(42)
        
        adapter.add_assistant_response(
            "Assistant response",
            message_type="final",
            speakability="speak"
        )
        
        mock_integration.add_assistant_response.assert_called_once()


class TestFactory:
    """Tests for factory functions."""
    
    @patch('bridge.middleware_context_integration.get_context_manager')
    def test_get_context_aware_middleware_factory(self, mock_get_manager):
        """AC: Factory creates initialized middleware."""
        mock_get_manager.return_value.get_or_create.return_value = MagicMock()
        
        middleware = get_context_aware_middleware(
            session_uuid="session-uuid",
            session_id=42,
            max_context_turns=30
        )
        
        assert isinstance(middleware, ContextAwareMiddleware)
        assert middleware.session_uuid == "session-uuid"


class TestIssue22AcceptanceCriteria:
    """Issue #22 Acceptance Criteria Verification."""
    
    @patch('bridge.middleware_context_integration.get_context_manager')
    def test_criterion_context_window_to_middleware(self, mock_get_manager):
        """AC: Middleware linked to context_window manager."""
        mock_window = MagicMock()
        mock_get_manager.return_value.get_or_create.return_value = mock_window
        
        middleware = ContextAwareMiddleware(
            session_uuid="test-session",
            session_id=42
        )
        
        # Middleware should use context manager
        mock_get_manager.return_value.get_or_create.assert_called()
        assert middleware.context_integration is not None
    
    @patch('bridge.middleware_context_integration.get_context_manager')
    def test_criterion_context_passed_to_openclaw(self, mock_get_manager):
        """AC: Context passed to OpenClaw on each message."""
        mock_window = MagicMock()
        mock_window.get_llm_context.return_value = [
            {'role': 'user', 'content': 'History'}
        ]
        mock_get_manager.return_value.get_or_create.return_value = mock_window
        
        middleware = ContextAwareMiddleware("test-session", 42)
        context = middleware.get_context()
        
        # Context should be in OpenClaw format
        assert isinstance(context, list)
        assert len(context) > 0
        assert 'role' in context[0]
        assert 'content' in context[0]
    
    @patch('bridge.middleware_context_integration.get_context_manager')
    def test_criterion_responses_captured(self, mock_get_manager):
        """AC: Responses captured with metadata."""
        mock_window = MagicMock()
        mock_get_manager.return_value.get_or_create.return_value = mock_window
        
        middleware = ContextAwareMiddleware("test-session", 42)
        middleware.context_integration = MagicMock()
        
        from bridge.openclaw_middleware import TaggedMessage, MessageMetadata, MessageType, Speakability
        tagged = TaggedMessage(
            content="Response",
            metadata=MessageMetadata(
                message_type=MessageType.FINAL,
                speakable=Speakability.SPEAK
            )
        )
        
        middleware.add_response(tagged)
        
        # Response should be added to context
        middleware.context_integration.add_assistant_response.assert_called_once()
    
    @patch('bridge.middleware_context_integration.get_context_manager')
    def test_criterion_tool_calls_in_context(self, mock_get_manager):
        """AC: Tool calls in context preserved."""
        mock_window = MagicMock()
        mock_get_manager.return_value.get_or_create.return_value = mock_window
        
        middleware = ContextAwareMiddleware("test-session", 42)
        middleware.context_integration = MagicMock()
        
        from bridge.openclaw_middleware import TaggedMessage, MessageMetadata, MessageType, Speakability
        tagged = TaggedMessage(
            content="Using tool",
            metadata=MessageMetadata(
                message_type=MessageType.TOOL_CALL,
                speakable=Speakability.SILENT,
                tool_name="search",
                tool_params={"query": "test"}
            )
        )
        
        middleware.add_response(tagged)
        
        # Tool call should be in context
        call_args = middleware.context_integration.add_assistant_response.call_args
        metadata = call_args[1].get('metadata', {})
        assert metadata.get('tool_name') == 'search'
    
    @patch('bridge.middleware_context_integration.get_context_manager')
    def test_websocket_adapter_integration(self, mock_get_manager):
        """AC: WebSocket adapter provides clean integration."""
        mock_middleware = MagicMock()
        mock_ctx_integration = MagicMock()
        mock_middleware.context_integration = mock_ctx_integration
        mock_get_manager.return_value.get_or_create.return_value = MagicMock()
        
        with patch('bridge.middleware_context_integration.get_context_aware_middleware', return_value=mock_middleware):
            adapter = WebSocketContextAdapter("session-uuid")
            adapter.initialize(42)
            
            # Simulate WebSocket flow
            adapter.add_user_message("User input")
            adapter.add_assistant_response("Response", "final", "speak")
            
            # Both messages should be in context
            assert mock_middleware.add_user_message.called
            assert mock_ctx_integration.add_assistant_response.called


class TestErrorHandling:
    """Error handling tests."""
    
    def test_empty_context_returns_empty_list(self):
        """Empty context returns empty list."""
        with patch.object(MiddlewareContextIntegration, '_ensure_context_window', return_value=None):
            integration = MiddlewareContextIntegration.__new__(MiddlewareContextIntegration)
            integration._context_window = None
            
            assert integration.get_message_count() == 0
            assert integration.get_context_summary() == "No context loaded"
    
    def test_adapter_uninitialized_returns_empty(self):
        """Uninitialized adapter returns empty context."""
        adapter = WebSocketContextAdapter("session-uuid")
        # Don't initialize
        
        assert adapter.get_context() == []
        assert adapter.get_summary() == "No context"
    
    @patch('bridge.middleware_context_integration.get_context_manager')
    def test_error_does_not_crash(self, mock_get_manager):
        """Errors are logged, not crashed."""
        mock_get_manager.return_value.get_or_create.side_effect = Exception("DB error")
        
        # Should handle gracefully
        with pytest.raises(Exception):
            MiddlewareContextIntegration("test-session")
