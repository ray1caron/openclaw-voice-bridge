"""Tests for Issue #8: Barge-In / Interruption."""

import pytest
import asyncio
from datetime import datetime, timedelta

from audio.barge_in import (
    BargeInState,
    BargeInConfig,
    BargeInSensitivity,
    BargeInHandler,
    InterruptionEvent,
)
from audio.interrupt_filter import (
    InterruptAwareFilter,
    InterruptMessage,
    InterruptAdapter,
)
from bridge.openclaw_middleware import (
    TaggedMessage,
    MessageType,
    Speakability,
)


class TestBargeInConfig:
    """Test barge-in configuration."""
    
    def test_default_config(self):
        config = BargeInConfig()
        assert config.enabled is True
        assert config.sensitivity == BargeInSensitivity.MEDIUM
        assert config.min_speech_ms == 250
        assert config.cooldown_ms == 500
        assert config.max_interrupt_latency_ms == 100.0
    
    def test_custom_config(self):
        config = BargeInConfig(
            enabled=False,
            sensitivity=BargeInSensitivity.HIGH,
            min_speech_ms=500,
            max_interrupt_latency_ms=50.0,
        )
        assert config.enabled is False
        assert config.sensitivity == BargeInSensitivity.HIGH
        assert config.sensitivity.value == 0.3
        assert config.min_speech_ms == 500
        assert config.max_interrupt_latency_ms == 50.0
    
    @pytest.mark.parametrize("sensitivity,threshold", [
        (BargeInSensitivity.LOW, 0.7),
        (BargeInSensitivity.MEDIUM, 0.5),
        (BargeInSensitivity.HIGH, 0.3),
    ])
    def test_sensitivity_thresholds(self, sensitivity, threshold):
        config = BargeInConfig(sensitivity=sensitivity)
        assert config.sensitivity.value == threshold



class TestBargeInStateMachine:
    """Test barge-in state transitions."""
    
    @pytest.fixture
    def handler(self):
        return BargeInHandler()
    
    @pytest.mark.asyncio
    async def test_initial_state(self, handler):
        assert handler.state == BargeInState.IDLE
    
    @pytest.mark.asyncio
    async def test_transition_to_listening(self, handler):
        await handler.start_listening()
        assert handler.state == BargeInState.LISTENING
    
    @pytest.mark.asyncio
    async def test_transition_to_speaking(self, handler):
        await handler.start_speaking()
        assert handler.state == BargeInState.SPEAKING
    
    @pytest.mark.asyncio
    async def test_transition_to_idle(self, handler):
        await handler.start_speaking()
        await handler.go_idle()
        assert handler.state == BargeInState.IDLE
    
    @pytest.mark.asyncio
    async def test_speech_detection_reset(self, handler):
        await handler.start_speaking()
        handler.speech_start = datetime.now()
        
        await handler.start_listening()
        assert handler.speech_start is None


class TestBargeInCallbacks:
    """Test barge-in callback system."""
    
    @pytest.fixture
    def handler(self):
        config = BargeInConfig(min_speech_ms=100, cooldown_ms=100)
        return BargeInHandler(config=config)
    
    @pytest.mark.asyncio
    async def test_state_change_callback(self, handler):
        state_changes = []
        
        async def on_change(old, new):
            state_changes.append((old, new))
        
        handler.on_state_change = on_change
        
        await handler.transition_to(BargeInState.SPEAKING)
        await handler.transition_to(BargeInState.LISTENING)
        
        assert len(state_changes) == 2
        assert state_changes[0] == (BargeInState.IDLE, BargeInState.SPEAKING)
        assert state_changes[1] == (BargeInState.SPEAKING, BargeInState.LISTENING)
    
    @pytest.mark.asyncio
    async def test_interrupt_callback(self, handler):
        events = []
        
        async def on_interrupt(event):
            events.append(event)
        
        handler.on_interruption = on_interrupt
        
        # Simulate interruption
        handler.speech_start = datetime.now() - timedelta(milliseconds=150)
        handler.state = BargeInState.SPEAKING
        
        await handler._trigger_interruption(0.8, 150)
        
        assert len(events) == 1
        assert events[0].vad_energy == 0.8
        assert events[0].confidence > 0.5


class TestBargeInVAD:
    """Test VAD-based interruption detection."""
    
    @pytest.fixture
    def handler(self):
        config = BargeInConfig(
            sensitivity=BargeInSensitivity.MEDIUM,
            min_speech_ms=100,
        )
        return BargeInHandler(config=config)
    
    @pytest.mark.asyncio
    async def test_cooldown_prevents_interrupt(self, handler):
        handler.state = BargeInState.SPEAKING
        handler.last_interrupt = datetime.now()
        
        handler.speech_start = datetime.now() - timedelta(milliseconds=200)
        
        # Should not trigger due to cooldown
        await handler._check_for_interruption()
        assert handler.state == BargeInState.SPEAKING
    
    @pytest.mark.asyncio
    async def test_speaking_state_required(self, handler):
        handler.state = BargeInState.LISTENING
        
        await handler._check_for_interruption()
        # Should not change state
        assert handler.state == BargeInState.LISTENING


class TestBargeInStats:
    """Test barge-in statistics."""
    
    @pytest.fixture
    def handler(self):
        return BargeInHandler()
    
    @pytest.mark.asyncio
    async def test_initial_stats(self, handler):
        stats = handler.get_stats()
        assert stats['interruptions_detected'] == 0
        assert stats['avg_latency_ms'] == 0.0
        assert stats['current_state'] == 'IDLE'
    
    @pytest.mark.asyncio
    async def test_stats_after_interrupt(self, handler):
        handler.speech_start = datetime.now() - timedelta(milliseconds=50)
        handler.state = BargeInState.SPEAKING
        
        await handler._trigger_interruption(0.6, 50)
        
        stats = handler.get_stats()
        assert stats['interruptions_detected'] == 1
        assert stats['avg_latency_ms'] > 0
        assert stats['interrupt_count'] == 1
    
    @pytest.mark.asyncio
    async def test_latency_target_met(self, handler):
        handler.config.max_interrupt_latency_ms = 500.0
        
        # First interrupt
        handler.speech_start = datetime.now() - timedelta(milliseconds=50)
        handler.state = BargeInState.SPEAKING
        await handler._trigger_interruption(0.6, 50)
        
        stats = handler.get_stats()
        assert stats['latency_target_met'] is True
    
    @pytest.mark.asyncio
    async def test_latency_target_not_met(self, handler):
        handler.config.max_interrupt_latency_ms = 10.0
        
        # Slow interrupt
        handler.speech_start = datetime.now() - timedelta(milliseconds=500)
        handler.state = BargeInState.SPEAKING
        await handler._trigger_interruption(0.6, 500)
        
        stats = handler.get_stats()
        assert stats['latency_target_met'] is False


class TestInterruptAwareFilter:
    """Test interrupt-aware response filter."""
    
    @pytest.fixture
    def filter(self):
        handler = BargeInHandler()
        return InterruptAwareFilter(handler)
    
    def test_process_non_final_message(self, filter):
        from bridge.openclaw_middleware import MessageMetadata
        msg = TaggedMessage(
            content="Thinking...",
            metadata=MessageMetadata(
                message_type=MessageType.THINKING,
                speakable=Speakability.SILENT,
            ),
        )
        result = filter.process_message(msg)
        assert result == msg
    
    def test_process_final_message(self, filter):
        from bridge.openclaw_middleware import MessageMetadata
        msg = TaggedMessage(
            content="Hello",
            metadata=MessageMetadata(
                message_type=MessageType.FINAL,
                speakable=Speakability.SPEAK,
            ),
        )
        result = filter.process_message(msg)
        assert result is not None
        assert filter.get_buffered_response() == "Hello"
    
    @pytest.mark.asyncio
    async def test_interrupted_message(self, filter):
        # Simulate interruption
        filter._interrupted = True
        
        from bridge.openclaw_middleware import MessageMetadata
        msg = TaggedMessage(
            content="Hello",
            metadata=MessageMetadata(
                message_type=MessageType.FINAL,
                speakable=Speakability.SPEAK,
            ),
        )
        result = filter.process_message(msg)
        assert result is None
        assert filter.is_interrupted() is False  # Reset after read
    
    @pytest.mark.asyncio
    async def test_interrupt_callback(self, filter):
        events = []
        
        async def on_interrupt(ev):
            events.append(ev)
        
        filter.on_interrupt = on_interrupt
        
        event = InterruptionEvent(
            timestamp=datetime.now(),
            vad_energy=0.8,
            confidence=0.9,
            latency_ms=50.0,
        )
        
        await filter._on_interruption(event)
        
        assert len(events) == 1
        assert events[0].latency_ms == 50.0
        assert filter._interrupted is True


class TestInterruptMessage:
    """Test interrupt message serialization."""
    
    def test_to_dict(self):
        event = InterruptionEvent(
            timestamp=datetime(2026, 2, 26, 10, 0, 0),
            vad_energy=0.7,
            confidence=0.95,
            latency_ms=45.5,
        )
        
        msg = InterruptMessage(event)
        data = msg.to_dict()
        
        assert data['type'] == 'interruption'
        assert '2026-02-26T10:00:00' in data['timestamp']
        assert data['vad_energy'] == 0.7
        assert data['confidence'] == 0.95
        assert data['latency_ms'] == 45.5


class TestInterruptAdapter:
    """Test interrupt adapter for WebSocket."""
    
    def test_attach_barge_in(self):
        adapter = InterruptAdapter()
        handler = BargeInHandler()
        
        adapter.attach_barge_in(handler)
        
        assert adapter.barge_in == handler
        assert handler.on_interruption is not None


class TestBargeInIntegration:
    """Integration tests for barge-in system."""
    
    @pytest.mark.asyncio
    async def test_full_interrupt_flow(self):
        """Test complete interruption flow."""
        config = BargeInConfig(
            min_speech_ms=50,
            cooldown_ms=0,
        )
        handler = BargeInHandler(config=config)
        
        interrupt_received = []
        
        async def on_interrupt(event):
            interrupt_received.append(event)
        
        handler.on_interruption = on_interrupt
        
        # Start speaking
        await handler.start_speaking()
        
        # Simulate speech detection (would come from VAD)
        handler.speech_start = datetime.now() - timedelta(milliseconds=100)
        handler.speech_start = datetime.now() - timedelta(milliseconds=100)
        await handler._trigger_interruption(0.8, 100)
        
        assert len(interrupt_received) == 1
        assert interrupt_received[0].latency_ms < 150
    
    @pytest.mark.asyncio
    async def test_state_transitions_full_cycle(self):
        """Test complete state cycle."""
        handler = BargeInHandler()
        states = []
        
        async def on_change(old, new):
            states.append((old.name, new.name))
        
        handler.on_state_change = on_change
        
        await handler.start_listening()
        await handler.start_speaking()
        await handler.start_listening()
        await handler.go_idle()
        
        assert states == [
            ('IDLE', 'LISTENING'),
            ('LISTENING', 'SPEAKING'),
            ('SPEAKING', 'LISTENING'),
            ('LISTENING', 'IDLE'),
        ]


@pytest.mark.performance
class TestBargeInPerformance:
    """Performance tests for barge-in."""
    
    @pytest.mark.parametrize("latency_ms", [50, 75, 100])
    @pytest.mark.asyncio
    async def test_interrupt_latency_target(self, latency_ms):
        """Test that interrupt latency stays under target."""
        config = BargeInConfig(
            max_interrupt_latency_ms=latency_ms,
            min_speech_ms=10,
        )
        handler = BargeInHandler(config=config)
        
        await handler.start_speaking()
        
        handler.speech_start = datetime.now() - timedelta(milliseconds=5)
        start_time = datetime.now()
        await handler._trigger_interruption(0.6, 5)
        elapsed = (datetime.now() - start_time).total_seconds() * 1000
        
        assert elapsed < 10  # Should be very fast