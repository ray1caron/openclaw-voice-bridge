"""
Integration tests for ResponseFilter with WebSocket client.

Tests the integration between response filtering and the WebSocket
message flow, simulating real OpenClaw response patterns.
"""
import asyncio
import pytest
from unittest.mock import Mock, call

from bridge.response_filter import (
    ResponseFilter,
    ResponseFilterManager,
    ResponseType,
    FilterDecision,
)
# Note: Response filter operates independently but integrates with WebSocket messages


class TestFilterWebSocketIntegration:
    """Test filter integration with WebSocket message flow."""
    
    @pytest.mark.asyncio
    async def test_filter_processes_websocket_messages(self):
        """Filter should process messages from WebSocket client."""
        manager = ResponseFilterManager()
        
        # Simulate messages from WebSocket
        messages = [
            {"type": "final", "text": "Hello! How can I help?"},
            {"type": "thinking", "text": "Let me think about this..."},
            {"type": "tool_call", "text": "Getting weather data"},
            {"type": "final", "text": "The weather is sunny."},
        ]
        
        spoken_texts = []
        for msg in messages:
            text = manager.process_message(msg)
            if text:
                spoken_texts.append(text)
        
        assert spoken_texts == [
            "Hello! How can I help?",
            "The weather is sunny.",
        ]
    
    @pytest.mark.asyncio
    async def test_filter_callback_chain(self):
        """Filter should trigger callbacks correctly."""
        spoken_messages = []
        
        def on_speak(text):
            spoken_messages.append(text)
        
        manager = ResponseFilterManager(on_speak=on_speak)
        
        # Process mix of messages
        manager.process_message({"type": "tool_call", "text": "Tool"})
        manager.process_message({"type": "final", "text": "First response"})
        manager.process_message({"type": "thinking", "text": "Thinking"})
        manager.process_message({"type": "final", "text": "Second response"})
        
        assert spoken_messages == ["First response", "Second response"]
    
    def test_filter_with_realistic_openclaw_flow(self):
        """Test with realistic OpenClaw message sequence."""
        filter = ResponseFilter()
        
        # Simulate a real conversation flow
        conversation = [
            # User asks: "What's the weather?"
            {"type": "final", "text": "I'd be happy to check the weather for you."},
            
            # AI thinking
            {"type": "thinking", "text": "Let me get the current weather data."},
            
            # Tool call
            {"type": "tool_call", "tool": "get_weather", "text": "Fetching weather"},
            
            # Tool result (might be spoken if user-facing)
            {"type": "tool_result", "text": "72°F and sunny"},
            
            # Final response
            {"type": "final", "text": "It's 72 degrees and sunny right now."},
        ]
        
        spoken = []
        for msg in conversation:
            result = filter.filter_message(msg)
            if result.decision == FilterDecision.SPEAK:
                spoken.append(result.filtered_text)
        
        # Should only speak the final responses
        assert "I'd be happy to check the weather for you." in spoken
        assert "It's 72 degrees and sunny right now." in spoken
        assert "Let me get the current weather data." not in spoken
    
    def test_filter_multi_step_reasoning(self):
        """Test filtering of multi-step reasoning process."""
        manager = ResponseFilterManager()
        
        # Complex reasoning with multiple steps
        messages = [
            {"text": "I'll solve this step by step."},  # Planning
            {"text": "First, let me identify the variables."},  # Thinking
            {"text": "Then I'll apply the formula."},  # Thinking
            {"text": "Calculating the result now..."},  # Processing
            {"type": "tool_call", "text": "Computing"},
            {"type": "final", "text": "The answer is 42."},  # Final
        ]
        
        results = [manager.process_message(m) for m in messages]
        spoken = [r for r in results if r is not None]
        
        # Only final should be spoken
        assert spoken == ["The answer is 42."]
    
    def test_filter_user_interaction_patterns(self):
        """Test common user interaction patterns."""
        filter = ResponseFilter()
        
        interactions = [
            # Greeting
            {"type": "final", "text": "Hello! How can I help you today?"},
            
            # Clarification
            {"type": "final", "text": "Could you clarify what you're looking for?"},
            
            # Processing
            {"type": "thinking", "text": "Let me search for that..."},
            
            # Multiple results
            {"type": "final", "text": "I found three results for you."},
            {"type": "final", "text": "First result: Option A."},
            {"type": "final", "text": "Second result: Option B."},
        ]
        
        spoken = []
        for msg in interactions:
            result = filter.filter_message(msg)
            if result.decision == FilterDecision.SPEAK:
                spoken.append(result.filtered_text)
        
        # All user-facing finals should be spoken
        assert len(spoken) == 5
        assert "Let me search for that..." not in spoken
    
    def test_filter_handles_edge_sequences(self):
        """Test handling of edge case sequences."""
        manager = ResponseFilterManager()
        
        # Edge case: multiple tool calls followed by final
        sequence = [
            {"type": "tool_call", "text": "Tool 1"},
            {"type": "tool_call", "text": "Tool 2"},
            {"type": "tool_call", "text": "Tool 3"},
            {"type": "final", "text": "Done!"},
        ]
        
        for msg in sequence:
            manager.process_message(msg)
        
        # Queue should still be manageable
        stats = manager.filter.get_stats()
        assert stats["queue_length"] <= 10  # queue_size limit
    
    def test_filter_with_session_context(self):
        """Test filter behavior across session context."""
        filter = ResponseFilter()
        
        # Simulate session with context
        context_msgs = [
            {"type": "final", "text": "Setting context for our conversation."},
            {"type": "thinking", "text": "Analyzing user preferences..."},
            {"type": "tool_call", "text": "Loading profile"},
            {"type": "final", "text": "Got it, I'll remember that."},
        ]
        
        for msg in context_msgs:
            filter.filter_message(msg)
        
        # Check session persistence
        stats = filter.get_stats()
        assert stats["total_messages"] == 4
        assert stats["spoken_messages"] == 2
        assert stats["type_counts"]["final"] == 2
        assert stats["type_counts"]["tool_call"] == 1
        assert stats["type_counts"]["thinking"] == 1


class TestFilterErrorHandling:
    """Test error handling in filter integration."""
    
    def test_filter_handles_malformed_messages(self):
        """Filter should handle malformed messages gracefully."""
        manager = ResponseFilterManager()
        
        malformed = [
            {},  # Empty
            {"type": None},  # None type
            {"text": ""},  # Empty text
            {"content": {"invalid": "structure"}},  # Wrong structure
            None,  # None message (shouldn't happen, but handle)
        ]
        
        # Should not raise exceptions
        for msg in malformed:
            if msg is not None:  # Skip None
                result = manager.process_message(msg)
                # Returns None or filtered text, no crash
    
    def test_filter_recovers_from_errors(self):
        """Filter should continue working after errors."""
        filter = ResponseFilter()
        
        # Process some messages
        filter.filter_message({"type": "final", "text": "First"})
        
        # Simulate error by passing invalid data
        try:
            filter.filter_message({"type": object()})  # Invalid type
        except Exception:
            pass  # May or may not raise
        
        # Should still work after
        result = filter.filter_message({"type": "final", "text": "Second"})
        assert result.decision == FilterDecision.SPEAK


class TestFilterRealWorldPatterns:
    """Test with real-world OpenClaw response patterns."""
    
    def test_chain_of_thought_filtering(self):
        """Test filtering of chain-of-thought responses."""
        filter = ResponseFilter()
        
        chain_of_thought = [
            {"text": "Let me think through this problem."},
            {"text": "I need to consider several factors."},
            {"text": "First, the user's location is relevant."},
            {"text": "Second, the time of day matters."},
            {"text": "Third, I should check for preferences."},
            {"text": "Now let me search for options."},
            {"type": "tool_call", "text": "Searching"},
            {"type": "final", "text": "Based on my research, here are your options."},
        ]
        
        results = [filter.filter_message(m) for m in chain_of_thought]
        spoken = [r for r in results if r.decision == FilterDecision.SPEAK]
        
        # Only the final response should be spoken
        assert len(spoken) == 1
        assert spoken[0].filtered_text == "Based on my research, here are your options."
    
    def test_code_and_explanation_pattern(self):
        """Test with code + explanation responses."""
        manager = ResponseFilterManager()
        
        code_response = [
            {"text": "I'll write that code for you."},
            {"text": "Let me plan the approach first."},
            # Code block would be internal
            {"type": "tool_call", "text": "Generating code"},
            {"type": "final", "text": "Here's the solution with comments explaining each step."},
        ]
        
        spoken = []
        for msg in code_response:
            result = manager.process_message(msg)
            if result:
                spoken.append(result)
        
        # Filter should only speak explanatory final
        assert len(spoken) == 1
    
    def test_confirmation_and_action_pattern(self):
        """Test confirmation -> action -> result pattern."""
        filter = ResponseFilter()
        
        pattern = [
            {"type": "final", "text": "I'll send that email for you."},  # Confirm
            {"type": "thinking", "text": "Composing the message..."},  # Silent
            {"type": "tool_call", "text": "send_email"},  # Silent
            {"type": "final", "text": "Email sent successfully!"},  # Result
        ]
        
        spoken = [filter.filter_message(m) for m in pattern]
        spoken_texts = [s.filtered_text for s in spoken if s.decision == FilterDecision.SPEAK]
        
        # Only confirmations and results should be spoken
        assert "I'll send that email for you." in spoken_texts
        assert "Email sent successfully!" in spoken_texts
        assert "Composing the message..." not in spoken_texts
