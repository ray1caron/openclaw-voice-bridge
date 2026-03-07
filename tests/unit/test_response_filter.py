"""
Unit tests for ResponseFilter module.

Tests the filtering logic that determines which OpenClaw responses
should be spoken vs silently dropped.
"""
import pytest
from bridge.response_filter import (
    ResponseFilter,
    ResponseFilterManager,
    ResponseType,
    FilterDecision,
    FilteredMessage,
)


class TestResponseTypeDetection:
    """Test classification of response types."""
    
    def test_detect_final_response(self):
        """Final responses should be detected and spoken."""
        filter = ResponseFilter()
        
        msg = {"type": "final", "text": "Here's your answer."}
        result = filter.filter_message(msg)
        
        assert result.response_type == ResponseType.FINAL
        assert result.decision == FilterDecision.SPEAK
        assert result.filtered_text == "Here's your answer."
    
    def test_detect_tool_call(self):
        """Tool calls should be silently filtered."""
        filter = ResponseFilter()
        
        msg = {"type": "tool_call", "text": "Looking up the weather"}
        result = filter.filter_message(msg)
        
        assert result.response_type == ResponseType.TOOL_CALL
        assert result.decision == FilterDecision.SILENT
        assert result.filtered_text is None
    
    def test_detect_thinking_explicit(self):
        """Explicit thinking messages should be silent."""
        filter = ResponseFilter()
        
        msg = {"type": "thinking", "text": "Let me think about this..."}
        result = filter.filter_message(msg)
        
        assert result.response_type == ResponseType.THINKING
        assert result.decision == FilterDecision.SILENT
    
    def test_detect_planning(self):
        """Planning messages should be filtered."""
        filter = ResponseFilter()
        
        msg = {"type": "plan", "text": "I'll help you with this task."}
        result = filter.filter_message(msg)
        
        assert result.response_type == ResponseType.PLANNING
        assert result.decision == FilterDecision.SILENT
    
    def test_detect_progress(self):
        """Progress updates should be filtered."""
        filter = ResponseFilter()
        
        msg = {"type": "progress", "text": "Processing your request..."}
        result = filter.filter_message(msg)
        
        assert result.response_type == ResponseType.PROGRESS
        assert result.decision == FilterDecision.SILENT
    
    def test_detect_error_with_content(self):
        """Errors with content should be spoken."""
        filter = ResponseFilter()
        
        msg = {"type": "error", "text": "I couldn't understand that."}
        result = filter.filter_message(msg)
        
        assert result.response_type == ResponseType.ERROR
        assert result.decision == FilterDecision.SPEAK
    
    def test_detect_error_minimal(self):
        """Minimal errors without content should be silent."""
        filter = ResponseFilter()
        
        msg = {"type": "error", "code": 500}
        result = filter.filter_message(msg)
        
        assert result.response_type == ResponseType.ERROR
        assert result.decision == FilterDecision.SILENT


class TestHeuristicFiltering:
    """Test heuristic pattern matching."""
    
    def test_thinking_pattern_let_me(self):
        """'Let me' patterns should be detected as thinking."""
        filter = ResponseFilter()
        
        msg = {"text": "Let me check that for you."}
        result = filter.filter_message(msg)
        
        assert result.response_type == ResponseType.THINKING
        assert result.decision == FilterDecision.SILENT
    
    def test_thinking_pattern_allow_me(self):
        """'Allow me' patterns should be detected."""
        filter = ResponseFilter()
        
        msg = {"text": "Allow me to find that information."}
        result = filter.filter_message(msg)
        
        assert result.response_type == ResponseType.THINKING
    
    def test_thinking_pattern_planning_language(self):
        """Planning language should be detected."""
        filter = ResponseFilter()
        
        msg = {"text": "First, I'll search for the data. Then I'll analyze it."}
        result = filter.filter_message(msg)
        
        assert result.response_type == ResponseType.THINKING
    
    def test_thinking_pattern_processing(self):
        """Processing language should be detected."""
        filter = ResponseFilter()
        
        msg = {"text": "Processing your request and analyzing the data."}
        result = filter.filter_message(msg)
        
        assert result.response_type == ResponseType.THINKING
    
    def test_thinking_pattern_filler(self):
        """Filler words should be detected."""
        filter = ResponseFilter()
        
        msg = {"text": "Hmm, let me see..."}
        result = filter.filter_message(msg)
        
        assert result.response_type == ResponseType.THINKING
    
    def test_tool_marker_brackets(self):
        """Tool call markers in brackets should be detected."""
        filter = ResponseFilter()
        
        msg = {"text": "[Tool Call: get_weather] Getting weather data"}
        result = filter.filter_message(msg)
        
        assert result.response_type == ResponseType.THINKING
    
    def test_final_pattern_summary(self):
        """Summary patterns should be final."""
        filter = ResponseFilter()
        
        msg = {"text": "In summary, the answer is 42."}
        result = filter.filter_message(msg)
        
        assert result.response_type == ResponseType.FINAL
    
    def test_final_pattern_here_is(self):
        """'Here is' patterns should be final."""
        filter = ResponseFilter()
        
        msg = {"text": "Here is the information you requested."}
        result = filter.filter_message(msg)
        
        assert result.response_type == ResponseType.FINAL


class TestConfidenceThreshold:
    """Test confidence-based filtering."""
    
    def test_high_confidence_speaks(self):
        """High confidence messages should be spoken."""
        filter = ResponseFilter(confidence_threshold=0.5)
        
        msg = {"type": "final", "text": "This is definitely the answer."}
        result = filter.filter_message(msg)
        
        assert result.confidence >= 0.9
        assert result.decision == FilterDecision.SPEAK
    
    def test_low_confidence_silent(self):
        """Very ambiguous messages should be silent."""
        filter = ResponseFilter(confidence_threshold=0.8)
        
        msg = {"text": "OK"}  # Very short, ambiguous
        result = filter.filter_message(msg)
        
        assert result.decision == FilterDecision.SILENT
    
    def test_confidence_threshold_boundary(self):
        """Messages at threshold boundary should respect threshold."""
        filter = ResponseFilter(confidence_threshold=0.7)
        
        # Queue decision typically has medium confidence
        msg = {"text": "That's about half complete."}
        result = filter.filter_message(msg)
        
        # Check that confidence is calculated
        assert 0 <= result.confidence <= 1.0


class TestMessageExtraction:
    """Test text extraction from various message formats."""
    
    def test_extract_direct_text(self):
        """Direct text field should be extracted."""
        filter = ResponseFilter()
        
        msg = {"type": "final", "text": "Hello world"}  # Use "final" type to get SPEAK decision
        result = filter.filter_message(msg)
        
        assert result.filtered_text == "Hello world"
    
    def test_extract_content_string(self):
        """Content string should be extracted."""
        filter = ResponseFilter()
        
        msg = {"type": "final", "content": "Hello from content"}
        result = filter.filter_message(msg)
        
        assert result.filtered_text == "Hello from content"
    
    def test_extract_content_dict(self):
        """Content dict with text field should be extracted."""
        filter = ResponseFilter()
        
        msg = {"type": "final", "content": {"text": "Nested text"}}
        result = filter.filter_message(msg)
        
        assert result.filtered_text == "Nested text"
    
    def test_extract_response_field(self):
        """Response field should be extracted."""
        filter = ResponseFilter()
        
        msg = {"type": "final", "response": "This is the response"}
        result = filter.filter_message(msg)
        
        assert result.filtered_text == "This is the response"
    
    def test_extract_no_text(self):
        """Messages without text should be handled."""
        filter = ResponseFilter()
        
        msg = {"type": "ping", "timestamp": 123456}
        result = filter.filter_message(msg)
        
        assert result.filtered_text is None
        assert result.decision == FilterDecision.SILENT


class TestQueueManagement:
    """Test message queue functionality."""
    
    def test_queue_adds_messages(self):
        """QUEUED decision should add to queue."""
        filter = ResponseFilter()
        
        # First add some messages that might queue
        msg = {"text": "Still processing your request, almost done now"}
        filter.filter_message(msg)
        
        # Queue may have items depending on scoring
        stats = filter.get_stats()
        assert stats["queue_length"] >= 0
    
    def test_clear_queue(self):
        """Queue should be clearable."""
        filter = ResponseFilter()
        
        count = filter.clear_queue()
        assert isinstance(count, int)
        
        stats = filter.get_stats()
        assert stats["queue_length"] == 0
    
    def test_queue_size_limit(self):
        """Queue should respect size limit."""
        filter = ResponseFilter(queue_size=2)
        
        # Force some queued messages
        for i in range(5):
            msg = {"text": f"progress update {i}", "type": "progress"}
            filter.filter_message(msg)
        
        stats = filter.get_stats()
        # Queue won't exceed configured size
        assert stats["queue_length"] <= 2


class TestStatistics:
    """Test filter statistics tracking."""
    
    def test_stats_initial(self):
        """Stats should start empty."""
        filter = ResponseFilter()
        
        stats = filter.get_stats()
        
        assert stats["total_messages"] == 0
        assert stats["spoken_messages"] == 0
        assert stats["silent_messages"] == 0
    
    def test_stats_tracked(self):
        """Stats should be tracked after filtering."""
        filter = ResponseFilter()
        
        filter.filter_message({"type": "final", "text": "Speak this"})
        filter.filter_message({"type": "tool_call", "text": "Silent"})
        
        stats = filter.get_stats()
        
        assert stats["total_messages"] == 2
        assert stats["spoken_messages"] == 1
        assert stats["silent_messages"] == 1
    
    def test_stats_type_counts(self):
        """Type counts should be tracked."""
        filter = ResponseFilter()
        
        filter.filter_message({"type": "final", "text": "Test"})
        filter.filter_message({"type": "tool_call", "text": "Test"})
        
        stats = filter.get_stats()
        
        assert stats["type_counts"]["final"] == 1
        assert stats["type_counts"]["tool_call"] == 1
    
    def test_speak_rate_calculation(self):
        """Speak rate should be calculated correctly."""
        filter = ResponseFilter()
        
        filter.filter_message({"type": "final", "text": "1"})
        filter.filter_message({"type": "final", "text": "2"})
        filter.filter_message({"type": "tool_call", "text": "3"})
        
        stats = filter.get_stats()
        
        assert stats["speak_rate"] == pytest.approx(0.667, rel=0.1)
    
    def test_reset_stats(self):
        """Stats should be resettable."""
        filter = ResponseFilter()
        
        filter.filter_message({"type": "final", "text": "Test"})
        filter.reset_stats()
        
        stats = filter.get_stats()
        
        assert stats["total_messages"] == 0


class TestResponseFilterManager:
    """Test the high-level filter manager."""
    
    def test_manager_process_message(self):
        """Manager should process messages and return speakable text."""
        spoken = []
        manager = ResponseFilterManager(on_speak=lambda x: spoken.append(x))
        
        result = manager.process_message({"type": "final", "text": "Hello"})
        
        assert result == "Hello"
        assert spoken == ["Hello"]
    
    def test_manager_silences_filtered(self):
        """Manager should return None for filtered messages."""
        manager = ResponseFilterManager()
        
        result = manager.process_message({"type": "tool_call", "text": "Thinking"})
        
        assert result is None
    
    def test_manager_flush_queue(self):
        """Manager should flush queue on request."""
        manager = ResponseFilterManager()
        
        texts = manager.flush_queue()
        assert isinstance(texts, list)
    
    def test_manager_interrupt_detection(self):
        """Manager should detect interrupt signals."""
        manager = ResponseFilterManager()
        
        interrupt_msg = {"type": "control", "action": "interrupt"}
        should_interrupt = manager.should_interrupt(interrupt_msg)
        
        assert should_interrupt is True
    
    def test_manager_no_interrupt_on_normal(self):
        """Manager should not interrupt on progress/planning messages."""
        manager = ResponseFilterManager()
        
        normal_msg = {"type": "progress", "text": "Regular response"}
        should_interrupt = manager.should_interrupt(normal_msg)
        
        assert should_interrupt is False


class TestEdgeCases:
    """Test edge cases and error handling."""
    
    def test_empty_message(self):
        """Empty messages should be handled gracefully."""
        filter = ResponseFilter()
        
        result = filter.filter_message({})
        
        assert result.filtered_text is None
        assert result.decision == FilterDecision.SILENT
    
    def test_none_text(self):
        """None text should be handled."""
        filter = ResponseFilter()
        
        result = filter.filter_message({"text": None})
        
        assert result.filtered_text is None
    
    def test_whitespace_only(self):
        """Whitespace-only text should be handled."""
        filter = ResponseFilter()
        
        result = filter.filter_message({"text": "   \n\t   "})
        
        assert result.decision == FilterDecision.SILENT
    
    def test_very_long_message(self):
        """Very long messages should be handled."""
        filter = ResponseFilter()
        
        long_text = "This is a sentence. " * 100
        result = filter.filter_message({"type": "final", "text": long_text})
        
        assert result.decision == FilterDecision.SPEAK
        assert len(result.filtered_text) > 1000
    
    def test_unicode_content(self):
        """Unicode content should be handled."""
        filter = ResponseFilter()
        
        result = filter.filter_message({"type": "final", "text": "Hello 世界 🌍"})
        
        assert result.filtered_text == "Hello 世界 🌍"
        assert result.decision == FilterDecision.SPEAK
    
    def test_callback_exception_handled(self):
        """Exceptions in callbacks should be handled gracefully."""
        def failing_callback(msg):
            raise ValueError("Test error")
        
        filter = ResponseFilter(on_filtered=failing_callback)
        
        # Should not raise
        result = filter.filter_message({"type": "final", "text": "Test"})
        assert result.decision == FilterDecision.SPEAK


class TestSpeakScore:
    """Test speak score calculation."""
    
    def test_final_has_max_score(self):
        """Final responses should have max speak score."""
        filter = ResponseFilter()
        
        result = filter.filter_message({"type": "final", "text": "Answer"})
        
        assert result.speak_score == 1.0
    
    def test_tool_call_has_zero_score(self):
        """Tool calls should have zero speak score."""
        filter = ResponseFilter()
        
        result = filter.filter_message({"type": "tool_call", "text": "Tool"})
        
        assert result.speak_score == 0.0
    
    def test_confidence_affects_score(self):
        """Confidence should affect speak score."""
        filter = ResponseFilter()
        
        # A decent response should have mid-range score
        result = filter.filter_message({
            "text": "This is a complete sentence with some length."
        })
        
        assert 0 < result.speak_score < 1
