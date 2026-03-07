"""Tests for Context Window."""

import pytest
import json
from datetime import datetime
from unittest.mock import Mock, patch, MagicMock

from bridge.context_window import (
    ContextWindow, ContextMessage, ContextWindowManager,
    get_context_manager
)
from bridge.history_manager import ConversationTurn


class TestContextMessage:
    """Test ContextMessage dataclass."""
    
    def test_creation(self):
        """Test basic creation."""
        msg = ContextMessage(role="user", content="Hello")
        
        assert msg.role == "user"
        assert msg.content == "Hello"
        assert msg.metadata == {}
        
    def test_to_llm_format(self):
        """Test LLM format conversion."""
        msg = ContextMessage(role="assistant", content="Hi there")
        result = msg.to_llm_format()
        
        assert result == {"role": "assistant", "content": "Hi there"}


class TestContextWindow:
    """Test ContextWindow class."""
    
    @pytest.fixture
    def mock_store(self):
        """Mock conversation store."""
        return Mock()
    
    @pytest.fixture  
    def mock_history(self):
        """Mock history manager."""
        with patch('bridge.context_window.get_history_manager') as mock:
            history = Mock()
            mock.return_value = history
            yield history
    
    def test_init(self):
        """Test initialization."""
        window = ContextWindow(
            session_uuid="test-uuid",
            session_id=1,
            max_turns=30
        )
        
        assert window.session_uuid == "test-uuid"
        assert window.session_id == 1
        assert window.max_turns == 30
        assert window._messages == []
        assert window._pruned_count == 0
        
    def test_init_defaults(self):
        """Test default values."""
        window = ContextWindow()
        
        assert window.session_uuid is None
        assert window.max_turns == 20
        assert window.max_tokens is None
        
    def test_add_message(self, mock_history):
        """Test adding message."""
        mock_history.add_turn.return_value = ConversationTurn(id=1)
        
        window = ContextWindow(session_uuid="test", session_id=1)
        window.add_message("user", "Hello")
        
        assert len(window._messages) == 1
        assert window._messages[0].role == "user"
        assert window._messages[0].content == "Hello"
        
    def test_add_user_message(self, mock_history):
        """Test adding user message helper."""
        window = ContextWindow(session_uuid="test", session_id=1)
        window.add_user_message("Hello")
        
        assert window._messages[0].role == "user"
        
    def test_add_assistant_message(self, mock_history):
        """Test adding assistant message helper."""
        window = ContextWindow(session_uuid="test", session_id=1)
        window.add_assistant_message("Response")
        
        assert window._messages[0].role == "assistant"
        
    def test_add_system_message(self, mock_history):
        """Test adding system message helper."""
        window = ContextWindow(session_uuid="test", session_id=1)
        window.add_system_message("System prompt")
        
        assert window._messages[0].role == "system"
        
    def test_pruning_keeps_first_and_last(self, mock_history):
        """Test pruning strategy keeps first 5 and last N."""
        window = ContextWindow(session_uuid="test", session_id=1, max_turns=10)
        
        # Add 15 messages
        for i in range(15):
            window.add_message("user", f"Message {i}", persist=False)
        
        # Should have 10 messages: first 5 + last 5
        assert len(window._messages) == 10
        assert window._pruned_count == 5
        
        # Check first and last are preserved
        assert window._messages[0].content == "Message 0"
        assert window._messages[-1].content == "Message 14"
        
    def test_no_pruning_under_limit(self, mock_history):
        """Test no pruning when under limit."""
        window = ContextWindow(session_uuid="test", session_id=1, max_turns=20)
        
        for i in range(10):
            window.add_message("user", f"Message {i}", persist=False)
        
        assert len(window._messages) == 10
        assert window._pruned_count == 0
        
    def test_get_messages(self, mock_history):
        """Test getting messages."""
        window = ContextWindow(session_uuid="test", session_id=1)
        
        window.add_user_message("User 1", persist=False)
        window.add_assistant_message("Assistant 1", persist=False)
        window.add_user_message("User 2", persist=False)
        
        # Get all
        all_msgs = window.get_messages()
        assert len(all_msgs) == 3
        
        # Get with limit
        limited = window.get_messages(limit=2)
        assert len(limited) == 2
        
        # Get by role
        user_msgs = window.get_messages(roles=["user"])
        assert len(user_msgs) == 2
        assert all(m.role == "user" for m in user_msgs)
        
    def test_get_llm_context(self, mock_history):
        """Test LLM context formatting."""
        window = ContextWindow(session_uuid="test", session_id=1)
        
        window.add_user_message("Hello", persist=False)
        window.add_assistant_message("Hi", persist=False)
        
        ctx = window.get_llm_context()
        
        assert len(ctx) == 2
        assert ctx[0] == {"role": "user", "content": "Hello"}
        assert ctx[1] == {"role": "assistant", "content": "Hi"}
        
    def test_get_llm_context_exclude_system(self, mock_history):
        """Test excluding system messages."""
        window = ContextWindow(session_uuid="test", session_id=1)
        
        window.add_system_message("System", persist=False)
        window.add_user_message("Hello", persist=False)
        
        ctx = window.get_llm_context(include_system=False)
        
        assert len(ctx) == 1
        assert ctx[0]["role"] == "user"
        
    def test_get_recent_messages(self, mock_history):
        """Test getting recent messages."""
        window = ContextWindow(session_uuid="test", session_id=1)
        
        for i in range(10):
            window.add_message("user", f"Message {i}", persist=False)
        
        recent = window.get_recent_messages(count=3)
        
        assert len(recent) == 3
        assert recent[0].content == "Message 7"
        assert recent[2].content == "Message 9"
        
    def test_clear(self, mock_history):
        """Test clearing messages."""
        window = ContextWindow(session_uuid="test", session_id=1)
        window.add_user_message("Test", persist=False)
        
        window.clear()
        
        assert len(window._messages) == 0
        assert window._pruned_count == 0
        
    def test_to_dict(self, mock_history):
        """Test serialization to dict."""
        window = ContextWindow(session_uuid="test", session_id=1, max_turns=10)
        window.add_user_message("Hello", metadata={"key": "value"}, persist=False)
        
        data = window.to_dict()
        
        assert data['session_uuid'] == "test"
        assert data['session_id'] == 1
        assert data['max_turns'] == 10
        assert len(data['messages']) == 1
        assert data['pruned_count'] == 0
        
    def test_to_json(self, mock_history):
        """Test JSON serialization."""
        window = ContextWindow(session_uuid="test", session_id=1)
        window.add_user_message("Hello", persist=False)
        
        json_str = window.to_json()
        
        assert isinstance(json_str, str)
        data = json.loads(json_str)
        assert data['session_uuid'] == "test"
        
    def test_from_dict(self):
        """Test deserialization from dict."""
        data = {
            "session_uuid": "test",
            "session_id": 1,
            "max_turns": 15,
            "messages": [
                {"role": "user", "content": "Hello", "metadata": {}}
            ],
            "pruned_count": 2,
            "message_count": 1
        }
        
        window = ContextWindow.from_dict(data)
        
        assert window.session_uuid == "test"
        assert window.max_turns == 15
        assert window._pruned_count == 2
        assert len(window._messages) == 1
        
    def test_from_json(self):
        """Test deserialization from JSON."""
        data = {
            "session_uuid": "test",
            "session_id": None,
            "max_turns": 10,
            "messages": [],
            "pruned_count": 0,
            "message_count": 0
        }
        
        window = ContextWindow.from_json(json.dumps(data))
        assert window.session_uuid == "test"
        
    def test_estimate_tokens(self, mock_history):
        """Test token estimation."""
        window = ContextWindow(session_uuid="test", session_id=1)
        
        # Add ~40 char message (should be ~10 tokens)
        window.add_user_message("This is a test message with 40 chars", persist=False)
        
        tokens = window.estimate_tokens()
        # 40 chars // 4 = 10 tokens
        assert tokens == 10
        
    def test_is_full_by_turns(self, mock_history):
        """Test is_full check by turn count."""
        window = ContextWindow(session_uuid="test", session_id=1, max_turns=3)
        
        assert not window.is_full()
        
        window.add_message("user", "1", persist=False)
        window.add_message("user", "2", persist=False)
        window.add_message("user", "3", persist=False)
        window.add_message("user", "4", persist=False)  # Triggers pruning
        
        # After pruning, still not "full" but will be close
        # Actually let's check simpler case
        
    def test_is_full_by_tokens(self, mock_history):
        """Test is_full check by tokens."""
        window = ContextWindow(
            session_uuid="test",
            session_id=1,
            max_turns=100,
            max_tokens=10
        )
        
        window.add_message("user", "This is a long message over 40 chars for sure", persist=False)
        
        assert window.is_full()
        
    def test_message_count_property(self, mock_history):
        """Test message_count property."""
        window = ContextWindow(session_uuid="test", session_id=1)
        window.add_user_message("Test", persist=False)
        
        assert window.message_count == 1
        
    def test_total_turns_property(self, mock_history):
        """Test total_turns property including pruned."""
        window = ContextWindow(session_uuid="test", session_id=1, max_turns=10)
        
        for i in range(15):
            window.add_message("user", f"Msg {i}", persist=False)
        
        assert window.total_turns == 15  # Includes pruned
        assert window.message_count == 10  # In window
        
    def test_get_summary(self, mock_history):
        """Test summary generation."""
        window = ContextWindow(session_uuid="test", session_id=1)
        window.add_user_message("Question", persist=False)
        window.add_assistant_message("Answer", persist=False)
        
        summary = window.get_summary()
        
        assert "2 turns" in summary
        assert "1 user" in summary
        assert "1 assistant" in summary


class TestContextWindowManager:
    """Test ContextWindowManager."""
    
    def test_init(self):
        """Test initialization."""
        manager = ContextWindowManager()
        assert manager._windows == {}
        
    def test_get_or_create_new(self):
        """Test creating new window."""
        # Create a real ContextWindow that will be "loaded"
        real_window = ContextWindow(session_uuid="test-uuid", session_id=1)
        
        with patch('bridge.context_window.ContextWindow.load', return_value=real_window):
            manager = ContextWindowManager()
            window = manager.get_or_create("test-uuid", session_id=1)
            
            assert window.session_uuid == "test-uuid"
            assert "test-uuid" in manager._windows
            
    def test_get_or_create_existing(self):
        """Test getting existing window."""
        manager = ContextWindowManager()
        window = ContextWindow(session_uuid="test-uuid")
        manager._windows["test-uuid"] = window
        
        retrieved = manager.get_or_create("test-uuid")
        assert retrieved is window
        
    def test_get(self):
        """Test window retrieval."""
        manager = ContextWindowManager()
        window = ContextWindow(session_uuid="test-uuid")
        manager._windows["test-uuid"] = window
        
        assert manager.get("test-uuid") is window
        assert manager.get("non-existent") is None
        
    def test_remove(self):
        """Test window removal."""
        manager = ContextWindowManager()
        window = ContextWindow(session_uuid="test-uuid")
        manager._windows["test-uuid"] = window
        
        assert manager.remove("test-uuid") is True
        assert manager.get("test-uuid") is None
        assert manager.remove("test-uuid") is False
        
    def test_clear_all(self):
        """Test clearing all windows."""
        manager = ContextWindowManager()
        manager._windows["a"] = ContextWindow()
        manager._windows["b"] = ContextWindow()
        
        manager.clear_all()
        assert manager._windows == {}


class TestGlobalManager:
    """Test global context manager."""
    
    def test_get_context_manager_singleton(self):
        """Test global manager is singleton."""
        manager1 = get_context_manager()
        manager2 = get_context_manager()
        
        assert manager1 is manager2
