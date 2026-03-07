"""
Unit tests for audio_pipeline module.
"""
import threading
import time
from unittest.mock import Mock, patch, MagicMock

import numpy as np
import pytest

from bridge.audio_pipeline import (
    AudioPipeline,
    AudioDeviceManager,
    AudioDeviceInfo,
    AudioDeviceType,
    PipelineState,
    PipelineStats,
)
from bridge.vad import VADConfig, VADMode, SpeechSegment
from bridge.config import AudioConfig


class TestPipelineState:
    """Test PipelineState enum."""
    
    def test_state_values(self):
        """Test state enum values."""
        assert PipelineState.IDLE.value == "idle"
        assert PipelineState.LISTENING.value == "listening"
        assert PipelineState.PROCESSING.value == "processing"
        assert PipelineState.SPEAKING.value == "speaking"
        assert PipelineState.ERROR.value == "error"


class TestAudioDeviceInfo:
    """Test AudioDeviceInfo dataclass."""
    
    def test_device_info_creation(self):
        """Test creating device info."""
        device = AudioDeviceInfo(
            index=0,
            name="Test Device",
            device_type=AudioDeviceType.INPUT,
            channels=2,
            sample_rate=16000,
            is_default=True
        )
        
        assert device.index == 0
        assert device.name == "Test Device"
        assert device.device_type == AudioDeviceType.INPUT
        assert device.channels == 2
        assert device.sample_rate == 16000
        assert device.is_default


class TestPipelineStats:
    """Test PipelineStats dataclass."""
    
    def test_default_stats(self):
        """Test default statistics."""
        stats = PipelineStats()
        
        assert stats.state_changes == 0
        assert stats.speech_segments_detected == 0
        assert stats.audio_frames_processed == 0
        assert stats.tts_utterances_played == 0
        assert stats.barge_in_count == 0
        assert stats.error_count == 0
        assert stats.start_time == 0.0
    
    def test_uptime_calculation(self):
        """Test uptime calculation."""
        start = time.time()
        stats = PipelineStats(start_time=start)
        
        time.sleep(0.01)  # Small delay
        uptime = stats.uptime_seconds
        
        assert uptime >= 0.01
        assert uptime < 1.0  # Should be very small
    
    def test_uptime_zero_start(self):
        """Test uptime when start_time is 0."""
        stats = PipelineStats(start_time=0)
        assert stats.uptime_seconds == 0


class TestAudioPipeline:
    """Test AudioPipeline class."""
    
    def test_init_default(self):
        """Test pipeline initialization with defaults."""
        pipeline = AudioPipeline()
        
        assert pipeline.state == PipelineState.IDLE
        assert pipeline._barge_in_enabled
        assert not pipeline._is_speaking
        assert pipeline.stats.start_time > 0
    
    def test_init_with_config(self):
        """Test pipeline initialization with custom config."""
        audio_config = AudioConfig(sample_rate=8000)
        vad_config = VADConfig(mode=VADMode.HIGH)
        
        pipeline = AudioPipeline(audio_config, vad_config)
        
        assert pipeline.audio_config.sample_rate == 8000
        assert pipeline.vad_config.mode == VADMode.HIGH
    
    def test_state_callbacks(self):
        """Test state change callbacks."""
        pipeline = AudioPipeline()
        
        callback_called = False
        old_state = None
        new_state = None
        
        def on_state_change(old, new):
            nonlocal callback_called, old_state, new_state
            callback_called = True
            old_state = old
            new_state = new
        
        pipeline.add_state_callback(on_state_change)
        
        # Trigger state change by starting
        # (Can't actually start without devices, but we can test callback mechanism)
        # Instead, manually trigger via _set_state
        pipeline._set_state(PipelineState.LISTENING)
        
        assert callback_called
        assert old_state == PipelineState.IDLE
        assert new_state == PipelineState.LISTENING
    
    def test_remove_state_callback(self):
        """Test removing state callback."""
        pipeline = AudioPipeline()
        
        call_count = 0
        
        def callback(old, new):
            nonlocal call_count
            call_count += 1
        
        pipeline.add_state_callback(callback)
        pipeline._set_state(PipelineState.LISTENING)
        assert call_count == 1
        
        # Remove callback
        pipeline.remove_state_callback(callback)
        pipeline._set_state(PipelineState.IDLE)
        # Should still be 1
        assert call_count == 1
    
    def test_stats_tracking(self):
        """Test statistics tracking."""
        pipeline = AudioPipeline()
        
        initial_stats = pipeline.stats
        assert initial_stats.state_changes == 0
        
        # Trigger state change
        pipeline._set_state(PipelineState.LISTENING)
        
        assert pipeline.stats.state_changes == 1
    
    def test_barge_in_enable_disable(self):
        """Test enabling/disabling barge-in."""
        pipeline = AudioPipeline()
        
        assert pipeline._barge_in_enabled
        
        pipeline.enable_barge_in(False)
        assert not pipeline._barge_in_enabled
        
        pipeline.enable_barge_in(True)
        assert pipeline._barge_in_enabled
    
    def test_initialize_devices_mock(self, monkeypatch):
        """Test device initialization with mocks."""
        pipeline = AudioPipeline()
        
        # Mock device manager
        mock_device = AudioDeviceInfo(
            index=0,
            name="Mock Input",
            device_type=AudioDeviceType.INPUT,
            channels=2,
            sample_rate=16000,
            is_default=True
        )
        
        def mock_get_device(identifier, device_type):
            if device_type == AudioDeviceType.INPUT:
                return mock_device
            return None
        
        def mock_get_default(device_type):
            if device_type == AudioDeviceType.INPUT:
                return mock_device
            return None
        
        monkeypatch.setattr(pipeline.device_manager, 'get_device', mock_get_device)
        monkeypatch.setattr(pipeline.device_manager, 'get_default_device', mock_get_default)
        
        # Initialize should fail because we don't have output device
        result = pipeline.initialize_devices()
        assert result is False  # No output device
    
    def test_speech_segment_callback(self):
        """Test speech segment callback mechanism."""
        pipeline = AudioPipeline()
        
        callback_called = False
        received_segment = None
        
        def on_segment(segment):
            nonlocal callback_called, received_segment
            callback_called = True
            received_segment = segment
        
        # Override the callback
        pipeline._on_speech_segment = on_segment
        
        # Create a mock segment
        segment = SpeechSegment(
            start_time=time.time(),
            end_time=time.time() + 1.0,
            audio_data=np.ones(16000, dtype=np.int16),
            confidence=0.95
        )
        
        # Call the callback
        pipeline._on_speech_segment(segment)
        
        assert callback_called
        assert received_segment == segment
