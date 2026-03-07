"""
Integration tests for Bug Tracker GitHub issue creation.

Tests the automatic GitHub issue generation when errors occur.
"""

import pytest
import json
import os
from datetime import datetime
from unittest.mock import Mock, patch, MagicMock
from pathlib import Path

from bridge.bug_tracker import (
    BugTracker, BugReport, BugSeverity, BugStatus, SystemSnapshot
)


class TestBugTrackerGitHubIntegration:
    """Test GitHub issue creation from bug reports."""
    
    @pytest.fixture
    def mock_github_response(self):
        """Mock successful GitHub API response."""
        mock_response = Mock()
        mock_response.status_code = 201
        mock_response.json.return_value = {
            "number": 42,
            "url": "https://github.com/ray1caron/voice-openclaw-bridge-v2/issues/42",
            "title": "Test Issue",
            "state": "open"
        }
        return mock_response
    
    @pytest.fixture
    def mock_github_failure(self):
        """Mock failed GitHub API response."""
        mock_response = Mock()
        mock_response.status_code = 401
        mock_response.text = "Bad credentials"
        return mock_response
    
    @pytest.fixture
    def sample_bug_report(self):
        """Create a sample bug report for testing."""
        return BugReport(
            id=1,
            timestamp=datetime.now().isoformat(),
            severity=BugSeverity.HIGH.value,
            component="websocket",
            title="WebSocket connection failed",
            description="Connection dropped during voice session",
            stack_trace="Traceback (most recent call last):\n  ...",
            system_state={
                "python_version": "3.12.3",
                "platform": "Linux",
                "memory_available": 8589934592,
            },
            user_context="User was speaking when error occurred",
            status=BugStatus.NEW.value,
            created_at=datetime.now().isoformat(),
            updated_at=datetime.now().isoformat(),
            github_issue=None
        )
    
    @pytest.mark.integration
    def test_github_issue_formatting(self, sample_bug_report):
        """Test that bug reports format correctly for GitHub."""
        github_data = sample_bug_report.to_github_issue()
        
        assert "title" in github_data
        assert "body" in github_data
        assert "labels" in github_data
        assert sample_bug_report.title in github_data["title"]
        assert sample_bug_report.severity in github_data["labels"]
        assert sample_bug_report.component in github_data["labels"]
        assert json.dumps(sample_bug_report.system_state) in github_data["body"]
        assert sample_bug_report.stack_trace in github_data["body"]
    
    @pytest.mark.integration
    @patch('bridge.bug_tracker.requests.post')
    def test_create_github_issue_success(self, mock_post, sample_bug_report, mock_github_response):
        """Test successful GitHub issue creation."""
        mock_post.return_value = mock_github_response
        
        tracker = BugTracker.get_instance()
        tracker.github_token = "test_token_123"
        tracker.github_repo = "ray1caron/voice-openclaw-bridge-v2"
        
        with patch.object(tracker, 'update_bug_github_issue') as mock_update:
            issue_number = tracker.create_github_issue(sample_bug_report)
            
            assert issue_number == 42
            mock_post.assert_called_once()
            
            # Verify the API call
            call_args = mock_post.call_args
            assert "api.github.com" in call_args[0][0]
            assert "Authorization" in call_args[1]["headers"]
            assert "token test_token_123" in call_args[1]["headers"]["Authorization"]
    
    @pytest.mark.integration
    @patch('bridge.bug_tracker.requests.post')
    def test_create_github_issue_failure(self, mock_post, sample_bug_report, mock_github_failure):
        """Test GitHub issue creation failure handling."""
        mock_post.return_value = mock_github_failure
        
        tracker = BugTracker.get_instance()
        tracker.github_token = "invalid_token"
        
        issue_number = tracker.create_github_issue(sample_bug_report)
        
        assert issue_number is None
    
    @pytest.mark.integration
    @patch('bridge.bug_tracker.requests.post')
    def test_create_github_issue_no_token(self, mock_post, sample_bug_report):
        """Test that issues aren't created without token."""
        tracker = BugTracker.get_instance()
        tracker.github_token = None
        
        issue_number = tracker.create_github_issue(sample_bug_report)
        
        assert issue_number is None
        mock_post.assert_not_called()
    
    @pytest.mark.integration
    @patch('bridge.bug_tracker.requests.post')
    def test_auto_create_on_critical_bug(self, mock_post, mock_github_response):
        """Test automatic GitHub issue creation for critical bugs."""
        mock_post.return_value = mock_github_response
        
        with patch.dict(os.environ, {"GITHUB_TOKEN": "test_token"}):
            tracker = BugTracker.get_instance()
            tracker.github_token = "test_token"
            tracker.auto_upload_enabled = True
            tracker.github_repo = "ray1caron/voice-openclaw-bridge-v2"
            
            with patch.object(tracker, 'create_github_issue') as mock_create:
                mock_create.return_value = 42
                with patch.object(tracker, 'update_bug_github_issue'):
                    tracker.capture_exception(
                        exception=Exception("Critical crash"),
                        severity=BugSeverity.CRITICAL,
                        component="audio_pipeline",
                        title="Critical audio crash",
                        auto_create_github=True
                    )
                    
                    mock_create.assert_called_once()
    
    @pytest.mark.integration
    def test_github_labels_by_severity(self, sample_bug_report):
        """Test correct label assignment based on severity."""
        github_data = sample_bug_report.to_github_issue()
        
        expected_labels = ["bug", "high", "component:websocket", "auto-generated"]
        assert all(label in github_data["labels"] for label in expected_labels)
    
    @pytest.mark.integration
    def test_update_github_issue_link(self):
        """Test updating bug record with GitHub issue number."""
        tracker = BugTracker.get_instance()
        
        # Create a test bug first
        try:
            raise ValueError("Test error")
        except Exception as e:
            bug_id = tracker.capture_error(
                error=e,
                component="test",
                severity=BugSeverity.LOW,
                title="Test bug"
            )
        
        # Update with GitHub issue number
        result = tracker.update_bug_github_issue(bug_id, 42)
        assert result is True
        
        # Verify the bug was updated
        bug = tracker.get_bug(bug_id)
        assert bug is not None
        assert bug.github_issue == 42


class TestBugTrackerE2EScenarios:
    """End-to-end scenarios for bug tracking with GitHub."""
    
    @pytest.mark.integration
    @pytest.mark.slow
    def test_end_to_end_error_capture_and_github_creation(self):
        """
        Test full flow: Error occurs → Captured → GitHub issue created.
        
        This test simulates a real error scenario where:
        1. An exception occurs in the voice bridge
        2. Bug tracker captures system state
        3. GitHub issue is automatically created
        4. Issue number is linked to local bug record
        """
        with patch('bridge.bug_tracker.requests.post') as mock_post:
            mock_response = Mock()
            mock_response.status_code = 201
            mock_response.json.return_value = {"number": 99}
            mock_post.return_value = mock_response
            
            tracker = BugTracker.get_instance()
            tracker.github_token = "ghp_test_token"
            tracker.github_repo = "ray1caron/voice-openclaw-bridge-v2"
            
            try:
                raise ValueError("Simulated voice processing error")
            except Exception as e:
                bug_id = tracker.capture_exception(
                    exception=e,
                    severity=BugSeverity.HIGH,
                    component="stt",
                    title="STT processing failed during voice session",
                    context={"session_id": "test-123", "user_was_speaking": True},
                    auto_create_github=True
                )
                
                # Verify bug was created locally
                bug = tracker.get_bug(bug_id)
                assert bug is not None
                assert bug.severity == "high"
                assert bug.component == "stt"
                assert bug.github_issue == 99
                
                # Verify GitHub API was called
                mock_post.assert_called_once()
                call_args = mock_post.call_args
                assert "issues" in call_args[0][0]
    
    @pytest.mark.integration
    def test_network_failure_recovery(self):
        """Test that bug tracker handles GitHub API failures gracefully."""
        with patch('bridge.bug_tracker.requests.post') as mock_post:
            mock_post.side_effect = Exception("Network unreachable")
            
            tracker = BugTracker.get_instance()
            tracker.github_token = "valid_token"
            
            report = BugReport(
                id=None,
                timestamp=datetime.now().isoformat(),
                severity=BugSeverity.MEDIUM.value,
                component="tts",
                title="TTS timeout",
                description="Timeout during speech generation",
                stack_trace=None,
                system_state={},
                user_context=None,
                status=BugStatus.NEW.value,
                created_at=datetime.now().isoformat(),
                updated_at=datetime.now().isoformat(),
                github_issue=None
            )
            
            # Should not raise exception
            issue_number = tracker.create_github_issue(report)
            
            assert issue_number is None
            # Bug should still be stored locally


class TestBugTrackerRateLimiting:
    """Test GitHub API rate limiting and backoff."""
    
    @pytest.mark.integration
    @patch('bridge.bug_tracker.requests.post')
    @patch('time.sleep')
    def test_rate_limit_backoff(self, mock_sleep, mock_post):
        """Test exponential backoff on rate limiting."""
        rate_limited_response = Mock()
        rate_limited_response.status_code = 429
        rate_limited_response.headers = {"Retry-After": "2"}
        
        success_response = Mock()
        success_response.status_code = 201
        success_response.json.return_value = {"number": 42}
        
        mock_post.side_effect = [rate_limited_response, success_response]
        
        tracker = BugTracker.get_instance()
        tracker.github_token = "test_token"
        report = BugReport(
            id=None, timestamp=datetime.now().isoformat(),
            severity=BugSeverity.LOW.value, component="test",
            title="Test", description="Test", stack_trace=None,
            system_state={}, user_context=None, status=BugStatus.NEW.value,
            created_at=datetime.now().isoformat(),
            updated_at=datetime.now().isoformat(), github_issue=None
        )
        
        issue_number = tracker.create_github_issue(report)
        
        assert issue_number == 42
        mock_sleep.assert_called_once()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
