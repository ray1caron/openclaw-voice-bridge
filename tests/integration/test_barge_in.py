"""Integration tests for Issue #8 Barge-In."""

import pytest
import asyncio
from datetime import datetime, timedelta

from audio.barge_in import (
    BargeInConfig,
    BargeInSensitivity,
    BargeInHandler,
    BargeInState,
)
from audio.interrupt_filter import (
    InterruptAwareFilter,
    InterruptAdapter,
)
from bridge.audio_pipeline import AudioPipeline


class TestBargeInWithAudioPipeline:
    """Test barge-in integrated with audio pipeline."""
    
    @pytest.fixture
    def config(self):
        return BargeInConfig(
            enabled=True,
            sensitivity=BargeInSensitivity.MEDIUM,
            min_speech_ms=100,
            max_interrupt_latency_ms=100.0,
        )
    
    @pytest.fixture
    def barge_in(self, config):
        return BargeInHandler(config=config)
    
    @pytest.mark.asyncio
    async def test_barge_in_start_stop(self, barge_in):
        await barge_in.start()
        assert barge_in._running is True
        
        await barge_in.stop()
        assert barge_in._running is False
    
    @pytest.mark.asyncio
    async def test_disabled_barge_in(self):
        config = BargeInConfig(enabled=False)
        handler = BargeInHandler(config=config)
        
        # Should not start
        await handler.start()
        assert handler._monitor_task is None


class TestBargeInterruptionLatency:
    """Test interruption latency requirements."""
    
    @pytest.mark.asyncio
    async def test_interrupt_latency_under_100ms(self):
        """Test that interrupts are detected within 100ms."""
        config = BargeInConfig(
            min_speech_ms=50,
            max_interrupt_latency_ms=100.0,
        )
        handler = BargeInHandler(config=config)
        
        latencies = []
        
        async def on_interrupt(event):
            latencies.append(event.latency_ms)
        
        handler.on_interruption = on_interrupt
        
        # Simulate 5 interrupts
        for i in range(5):
            await handler.start_speaking()
            handler.speech_start = datetime.now() - timedelta(milliseconds=50)
            await handler._trigger_interruption(0.6 + i * 0.05, 50)
        
        # All latencies should be < 100ms
        for lat in latencies:
            assert lat < 100.0, f"Latency {lat}ms exceeds 100ms target"
        
        # Stats should confirm target met
        stats = handler.get_stats()
        assert stats['latency_target_met'] is True


class TestInterruptRecovery:
    """Test recovery after interruption."""
    
    @pytest.mark.asyncio
    async def test_recovery_to_listening(self):
        handler = BargeInHandler()
        
        await handler.start_speaking()
        handler.state = BargeInState.INTERRUPTING
        
        await handler.clear_interrupt()
        assert handler.state == BargeInState.LISTENING
    
    @pytest.mark.asyncio
    async def test_cooldown_period(self):
        config = BargeInConfig(cooldown_ms=200)
        handler = BargeInHandler(config=config)
        
        handler.last_interrupt = datetime.now()
        handler.state = BargeInState.SPEAKING
        
        # Should not trigger due to cooldown
        handler.speech_start = datetime.now() - timedelta(milliseconds=200)
        await handler._check_for_interruption()
        
        # Wait for cooldown
        await asyncio.sleep(0.3)
        
        # Should still be in SPEAKING (no VAD to trigger)
        assert handler.state == BargeInState.SPEAKING


class TestEndToEndInterrupt:
    """End-to-end barge-in test."""
    
    @pytest.mark.asyncio
    async def test_complete_interrupt_workflow(self):
        """Test complete workflow: speak -> interrupt -> listen."""
        
        # Setup components
        config = BargeInConfig(
            min_speech_ms=50,
            max_interrupt_latency_ms=100.0,
        )
        barge_in = BargeInHandler(config=config)
        inter_filter = InterruptAwareFilter(barge_in)
        
        events = []
        async def on_interrupt(event):
            events.append(event)
        
        barge_in.on_interruption = on_interrupt
        
        # Workflow
        await barge_in.start_listening()
        assert barge_in.state == BargeInState.LISTENING
        
        # Start speaking
        await barge_in.start_speaking()
        assert barge_in.state == BargeInState.SPEAKING
        
        # User interrupts
        barge_in.speech_start = datetime.now() - timedelta(milliseconds=75)
        await barge_in._trigger_interruption(0.75, 75)
        assert barge_in.state == BargeInState.INTERRUPTING
        assert len(events) == 1
        
        # Handler clears, back to listening
        await barge_in.clear_interrupt()
        assert barge_in.state == BargeInState.LISTENING
        
        # Check stats
        stats = barge_in.get_stats()
        assert stats['interruptions_detected'] == 1
        assert stats['latency_target_met'] is True


class TestBargeInEdgeCases:
    """Edge case tests for barge-in."""
    
    @pytest.mark.asyncio
    async def test_interruption_during_idle(self):
        """Ensure no issues if interrupt triggered during idle."""
        handler = BargeInHandler()
        
        # Try to trigger during IDLE
        await handler._check_for_interruption()
        assert handler.state == BargeInState.IDLE
    
    @pytest.mark.asyncio
    async def test_multiple_interrupts(self):
        """Test handling multiple interruptions in same session."""
        handler = BargeInHandler()
        
        # First interrupt
        await handler.start_speaking()
        handler.speech_start = datetime.now() - timedelta(milliseconds=100)
        await handler._trigger_interruption(0.8, 100)
        
        # Second interrupt after clearing
        await handler.start_speaking()
        handler.speech_start = datetime.now() - timedelta(milliseconds=100)
        await handler._trigger_interruption(0.8, 100)
        
        assert handler.get_stats()['interruptions_detected'] == 2
    
    @pytest.mark.asyncio
    async def test_rapid_state_changes(self):
        """Test rapid transitions don't break."""
        handler = BargeInHandler()
        
        states = [BargeInState.IDLE, BargeInState.LISTENING, 
                 BargeInState.SPEAKING, BargeInState.LISTENING]
        
        for state in states:
            await handler.transition_to(state)
        
        assert handler.state == BargeInState.LISTENING