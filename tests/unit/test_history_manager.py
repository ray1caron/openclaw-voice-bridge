"""Tests for History Manager.

This file has been updated to fix mock configuration issues.
Tests use _create_mock_conn helper for consistent context manager mocking.
"""

import pytest
import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import Mock, patch, MagicMock

from bridge.history_manager import (
    HistoryManager, ConversationTurn, ConversationSession,
    get_history_manager
)
from bridge.session_manager import Session


class TestConversationTurn:
    """Test ConversationTurn dataclass."""
    
    def test_creation(self):
        """Test basic creation."""
        turn = ConversationTurn(
            id=1,
            session_id=2,
            turn_index=0,
            role="user",
            content="Hello"
        )
        
        assert turn.id == 1
        assert turn.session_id == 2
        assert turn.turn_index == 0
        assert turn.role == "user"
        assert turn.content == "Hello"
        
    def test_from_db_row(self):
        """Test from database row."""
        row = Mock()
        row.__getitem__ = Mock(side_effect=lambda k: {
            'id': 1,
            'session_id': 2,
            'turn_index': 3,
            'timestamp': '2024-01-01T00:00:00',
            'role': 'assistant',
            'content': 'Response',
            'message_type': 'final',
            'speakability': 'speak',
            'tool_calls': json.dumps({"tool": "test"})
        }.get(k))
        
        turn = ConversationTurn.from_db_row(row)
        
        assert turn.id == 1
        assert turn.session_id == 2
        assert turn.turn_index == 3
        assert turn.role == "assistant"
        assert turn.tool_calls == {"tool": "test"}
        
    def test_to_dict(self):
        """Test conversion to dict."""
        turn = ConversationTurn(
            id=1,
            session_id=2,
            turn_index=0,
            timestamp="2024-01-01T00:00:00",
            role="user",
            content="Hello",
            message_type="final",
            speakability="speak"
        )
        
        data = turn.to_dict()
        
        assert data['id'] == 1
        assert data['session_id'] == 2
        assert data['content'] == "Hello"


class TestConversationSession:
    """Test ConversationSession dataclass."""
    
    def test_creation(self):
        """Test basic creation."""
        session = ConversationSession(
            session_uuid="test-uuid",
            created_at="2024-01-01T00:00:00",
            state="active"
        )
        
        assert session.session_uuid == "test-uuid"
        assert session.state == "active"
        assert session.turns == []
        
    def test_to_dict(self):
        """Test serialization."""
        turn = ConversationTurn(
            id=1,
            session_id=2,
            turn_index=0,
            role="user",
            content="Hello"
        )
        session = ConversationSession(
            session_uuid="test-uuid",
            created_at="2024-01-01T00:00:00",
            state="active",
            turns=[turn]
        )
        
        data = session.to_dict()
        
        assert data['session_uuid'] == "test-uuid"
        assert len(data['turns']) == 1


class TestHistoryManager:
    """Test HistoryManager functionality."""
    
    @pytest.fixture
    def mock_session_manager(self):
        """Mock session manager."""
        with patch('bridge.history_manager.get_session_manager') as mock:
            manager = Mock()
            mock.return_value = manager
            yield manager
    
    def _create_mock_conn(self, fetchall_result=None, fetchone_result=None, rowcount=1):
        """Helper to create properly configured mock connection."""
        mock_conn = MagicMock()
        mock_cursor = Mock()
        mock_cursor.fetchall.return_value = fetchall_result or []
        mock_cursor.fetchone.return_value = fetchone_result
        mock_cursor.rowcount = rowcount
        mock_cursor.lastrowid = 1
        mock_conn.execute.return_value = mock_cursor
        mock_conn.__enter__ = Mock(return_value=mock_conn)
        mock_conn.__exit__ = Mock(return_value=False)
        return mock_conn
    
    def test_add_turn(self, mock_session_manager):
        """Test adding conversation turn."""
        manager = HistoryManager()
        
        # Mock the database operation
        mock_conn = self._create_mock_conn(
            fetchone_result={
                'id': 1, 'session_id': 1, 'turn_index': 0,
                'timestamp': '2024-01-01T00:00:00',
                'role': 'user', 'content': 'Hello',
                'message_type': None, 'speakability': None, 'tool_calls': None
            }
        )
        
        with patch.object(manager.store, '_get_connection', return_value=mock_conn):
            turn = manager.add_turn(
                session_id=1,
                role="user",
                content="Hello",
                turn_index=0
            )
            
            assert turn is not None
            # Store should be called
            assert mock_conn.execute.called

    def test_get_turn(self, mock_session_manager):
        """Test retrieving turn by ID."""
        manager = HistoryManager()
        
        mock_row = Mock()
        mock_row.__getitem__ = lambda self, k: {
            'id': 1, 'session_id': 2, 'turn_index': 0,
            'timestamp': '2024-01-01T00:00:00',
            'role': 'user', 'content': 'Hello',
            'message_type': None, 'speakability': None, 'tool_calls': None
        }.get(k, None)
        
        mock_conn = self._create_mock_conn(fetchone_result=mock_row)
        
        with patch.object(manager.store, '_get_connection', return_value=mock_conn):
            turn = manager.get_turn(1)
            
            assert turn is not None
            assert turn.id == 1
            
    def test_get_turn_not_found(self, mock_session_manager):
        """Test retrieving non-existent turn."""
        manager = HistoryManager()
        
        mock_conn = self._create_mock_conn(fetchone_result=None)
        
        with patch.object(manager.store, '_get_connection', return_value=mock_conn):
            turn = manager.get_turn(999)
            
            assert turn is None
            
    def test_get_session_turns(self, mock_session_manager):
        """Test retrieving turns for session."""
        manager = HistoryManager()
        
        # Mock session
        mock_session = Session(session_uuid="test-uuid", id=1)
        mock_session_manager.get_session.return_value = mock_session
        
        # Mock rows
        mock_rows = [
            {'id': 1, 'session_id': 1, 'turn_index': 0, 'timestamp': '2024-01-01T00:00:00',
             'role': 'user', 'content': 'Hello', 'message_type': None, 
             'speakability': None, 'tool_calls': None},
            {'id': 2, 'session_id': 1, 'turn_index': 1, 'timestamp': '2024-01-01T00:00:01',
             'role': 'assistant', 'content': 'Hi', 'message_type': None,
             'speakability': None, 'tool_calls': None}
        ]
        
        mock_conn = self._create_mock_conn(fetchall_result=mock_rows)
        
        with patch.object(manager.store, '_get_connection', return_value=mock_conn):
            turns = manager.get_session_turns("test-uuid")
            
            assert len(turns) == 2
            assert turns[0].role == "user"
            assert turns[1].role == "assistant"
            
    def test_get_recent_turns(self, mock_session_manager):
        """Test getting recent turns."""
        manager = HistoryManager()
        
        mock_session = Session(session_uuid="test-uuid", id=1)
        mock_session_manager.get_session.return_value = mock_session
        
        mock_rows = [
            {'id': 1, 'session_id': 1, 'turn_index': 0, 'timestamp': '2024-01-01T00:00:00',
             'role': 'user', 'content': 'Hello', 'message_type': None, 
             'speakability': None, 'tool_calls': None}
        ]
        
        mock_conn = self._create_mock_conn(fetchall_result=mock_rows)
        
        with patch.object(manager.store, '_get_connection', return_value=mock_conn):
            turns = manager.get_recent_turns("test-uuid", count=3)
            
            assert len(turns) == 1
            assert turns[0].content == "Hello"
            
    def test_search_conversations(self, mock_session_manager):
        """Test searching conversations."""
        manager = HistoryManager()
        
        mock_rows = [
            {'id': 1, 'session_uuid': 'uuid1', 'turn_index': 0,
             'timestamp': '2024-01-01T00:00:00', 'role': 'user',
             'content': 'Hello world', 'created_at': '2024-01-01T00:00:00',
             'state': 'active'}
        ]
        
        mock_conn = self._create_mock_conn(fetchall_result=mock_rows)
        
        with patch.object(manager.store, '_get_connection', return_value=mock_conn):
            results = manager.search_conversations("world")
            
            assert len(results) == 1
            assert "world" in results[0]['content'].lower()
            
    def test_search_with_dates(self, mock_session_manager):
        """Test search with date filters."""
        manager = HistoryManager()
        
        mock_conn = self._create_mock_conn(fetchall_result=[])
        
        with patch.object(manager.store, '_get_connection', return_value=mock_conn):
            results = manager.search_conversations(
                "test",
                start_date="2024-01-01T00:00:00",
                end_date="2024-01-31T23:59:59"
            )
            
            # Search should return empty list (no errors)
            assert results == []
            # Execute was called
            assert mock_conn.execute.called
            
            
    def test_get_conversation_stats(self, mock_session_manager):
        """Test getting conversation stats."""
        manager = HistoryManager()
        
        mock_session = Session(session_uuid="test-uuid", id=1)
        mock_session_manager.get_session.return_value = mock_session
        
        # Create mock cursor with proper fetchone/fetchall behavior
        mock_cursor = Mock()
        mock_cursor.fetchone.side_effect = [
            [5],  # Total turns
            [0]   # Pending tools
        ]
        mock_cursor.fetchall.return_value = [
            ('user', 3), ('assistant', 2)
        ]
        
        mock_conn = MagicMock()
        mock_conn.execute.return_value = mock_cursor
        mock_conn.__enter__ = Mock(return_value=mock_conn)
        mock_conn.__exit__ = Mock(return_value=False)
        
        with patch.object(manager.store, '_get_connection', return_value=mock_conn):
            stats = manager.get_conversation_stats("test-uuid")
            
            assert stats['session_uuid'] == "test-uuid"
            assert stats['total_turns'] == 5
            
    def test_export_session_json(self, mock_session_manager, tmp_path):
        """Test JSON export."""
        manager = HistoryManager()
        
        # Mock session
        from dataclasses import dataclass
        
        @dataclass
        class MockSession:
            session_uuid: str = "test-uuid"
            created_at: str = "2024-01-01T00:00:00"
            state: str = "active"
            metadata: dict = None
            
            def __post_init__(self):
                if self.metadata is None:
                    self.metadata = {}
        
        mock_session_manager.get_session.return_value = MockSession()
        
        mock_rows = [
            {'id': 1, 'session_id': 1, 'turn_index': 0, 'timestamp': '2024-01-01T00:00:00',
             'role': 'user', 'content': 'Hello', 'message_type': None,
             'speakability': None, 'tool_calls': None}
        ]
        
        mock_conn = self._create_mock_conn(fetchall_result=mock_rows)
        
        output_path = tmp_path / "export.json"
        
        with patch.object(manager.store, '_get_connection', return_value=mock_conn):
            success = manager.export_session_json("test-uuid", output_path)
            
            assert success is True
            assert output_path.exists()
            
    def test_export_session_csv(self, mock_session_manager, tmp_path):
        """Test CSV export."""
        manager = HistoryManager()
        
        @dataclass 
        class MockSession:
            session_uuid: str = "test-uuid"
            id: int = 1
            created_at: str = "2024-01-01T00:00:00"
            state: str = "active"
            metadata: dict = None
            
            def __post_init__(self):
                if self.metadata is None:
                    self.metadata = {}
        
        mock_session_manager.get_session.return_value = MockSession()
        
        mock_rows = [
            {'id': 1, 'session_id': 1, 'turn_index': 0, 'timestamp': '2024-01-01T00:00:00',
             'role': 'user', 'content': 'Hello', 'message_type': None,
             'speakability': None, 'tool_calls': None}
        ]
        
        mock_conn = self._create_mock_conn(fetchall_result=mock_rows)
        
        output_path = tmp_path / "export.csv"
        
        with patch.object(manager.store, '_get_connection', return_value=mock_conn):
            success = manager.export_session_csv("test-uuid", output_path)
            
            assert success is True
            assert output_path.exists()


class TestFactory:
    """Test factory functions."""
    
    def test_get_history_manager_returns_manager(self):
        """Factory returns HistoryManager instance."""
        manager = get_history_manager()
        
        assert isinstance(manager, HistoryManager)
