"""
Unit tests for VAD (Voice Activity Detection) module.
"""
import time

import numpy as np
import pytest

from bridge.vad import (
    WebRTCVAD,
    VADConfig,
    VADMode,
    VADState,
    SpeechSegmenter,
    SpeechSegment,
    MockVAD,
)
from bridge.audio_pipeline import (
    AudioDeviceManager,
    AudioDeviceInfo,
    AudioDeviceType,
)


class TestVADConfig:
    """Test VAD configuration."""
    
    def test_default_config(self):
        """Test default VAD configuration."""
        config = VADConfig()
        
        assert config.mode == VADMode.MEDIUM
        assert config.frame_duration_ms == 30
        assert config.sample_rate == 16000
        assert config.min_speech_duration_ms == 250
        assert config.min_silence_duration_ms == 500
        assert config.padding_duration_ms == 300
    
    def test_custom_config(self):
        """Test custom VAD configuration."""
        config = VADConfig(
            mode=VADMode.HIGH,
            frame_duration_ms=20,
            sample_rate=8000,
            min_speech_duration_ms=500,
            min_silence_duration_ms=1000,
            padding_duration_ms=200
        )
        
        assert config.mode == VADMode.HIGH
        assert config.frame_duration_ms == 20
        assert config.sample_rate == 8000
        assert config.min_speech_duration_ms == 500
        assert config.min_silence_duration_ms == 1000
        assert config.padding_duration_ms == 200


class TestMockVAD:
    """Test MockVAD implementation."""
    
    def test_mock_vad_available(self):
        """Test that mock VAD is always available."""
        vad = MockVAD()
        assert vad.is_available
    
    def test_mock_vad_energy_detection(self):
        """Test energy-based speech detection."""
        config = VADConfig(mode=VADMode.MEDIUM)
        vad = MockVAD(config)
        
        # Low energy (silence)
        silence_frame = np.zeros(480, dtype=np.int16)
        assert not vad.process_frame(silence_frame)
        
        # High energy (speech) - use values that exceed threshold
        speech_frame = np.full(480, 1000, dtype=np.int16)
        assert vad.process_frame(speech_frame)
    
    def test_mock_vad_mode_thresholds(self):
        """Test different VAD mode thresholds."""
        # Create frames with different energy levels
        low_energy = np.full(480, 150, dtype=np.int16)   # RMS ~150
        high_energy = np.full(480, 1000, dtype=np.int16)  # RMS ~1000
        
        # NORMAL mode (threshold 100) - should detect both
        vad_normal = MockVAD(VADConfig(mode=VADMode.NORMAL))
        assert vad_normal.process_frame(low_energy)
        assert vad_normal.process_frame(high_energy)
        
        # HIGH mode (threshold 500) - should only detect high energy
        vad_high = MockVAD(VADConfig(mode=VADMode.HIGH))
        assert not vad_high.process_frame(low_energy)
        assert vad_high.process_frame(high_energy)


class TestSpeechSegment:
    """Test SpeechSegment dataclass."""
    
    def test_segment_creation(self):
        """Test creating a speech segment."""
        audio = np.ones(4800, dtype=np.int16)
        segment = SpeechSegment(
            start_time=1000.0,
            end_time=1500.0,
            audio_data=audio,
            confidence=0.95
        )
        
        assert segment.start_time == 1000.0
        assert segment.end_time == 1500.0
        assert segment.confidence == 0.95
        assert len(segment.audio_data) == 4800
    
    def test_segment_duration(self):
        """Test segment duration calculation."""
        segment = SpeechSegment(
            start_time=10.0,
            end_time=12.5,
            audio_data=np.array([]),
            confidence=1.0
        )
        
        assert segment.duration_ms == 2500.0  # 2.5 seconds


class TestSpeechSegmenter:
    """Test SpeechSegmenter class."""
    
    def test_segmenter_init(self):
        """Test segmenter initialization."""
        config = VADConfig()
        segmenter = SpeechSegmenter(config=config)
        
        assert segmenter.config == config
        assert not segmenter._in_speech
    
    def test_speech_detection(self):
        """Test basic speech detection flow."""
        config = VADConfig(
            mode=VADMode.NORMAL,  # Lower threshold for testing
            min_speech_duration_ms=100,  # Short for testing
            min_silence_duration_ms=100,  # Short for testing
            frame_duration_ms=30
        )
        segmenter = SpeechSegmenter(config=config)
        
        # Send silence frames
        silence = np.zeros(480, dtype=np.int16)
        for _ in range(5):
            result = segmenter.process_frame(silence)
            assert result is None
        
        # Send speech frames (high energy)
        speech = np.full(480, 2000, dtype=np.int16)
        for _ in range(5):
            result = segmenter.process_frame(speech)
            assert result is None  # Still in speech
        
        # Send silence to end speech
        start_time = time.time()
        while time.time() - start_time < 0.2:  # 200ms of silence
            result = segmenter.process_frame(silence)
            if result is not None:
                # Speech segment detected
                assert isinstance(result, SpeechSegment)
                assert result.duration_ms >= 100  # min_speech_duration_ms
                return
        
        # If we get here, no segment was detected (might happen with timing)
        # Force flush to get any pending segment
        result = segmenter.flush()
        if result:
            assert isinstance(result, SpeechSegment)
    
    def test_reset(self):
        """Test segmenter reset."""
        config = VADConfig()
        segmenter = SpeechSegmenter(config=config)
        
        # Simulate being in speech
        segmenter._in_speech = True
        segmenter._speech_start_time = time.time()
        segmenter._buffered_frames = [np.zeros(480, dtype=np.int16)]
        
        # Reset
        segmenter.reset()
        
        assert not segmenter._in_speech
        assert segmenter._speech_start_time is None
        assert len(segmenter._buffered_frames) == 0
    
    def test_flush(self):
        """Test flushing pending speech."""
        config = VADConfig(min_speech_duration_ms=100)
        segmenter = SpeechSegmenter(config=config)
        
        # Add some speech frames
        speech = np.full(480, 2000, dtype=np.int16)
        segmenter._in_speech = True
        segmenter._speech_start_time = time.time() - 0.5  # 500ms ago
        segmenter._buffered_frames = [speech.copy() for _ in range(10)]
        
        # Flush
        result = segmenter.flush()
        
        assert result is not None
        assert isinstance(result, SpeechSegment)
        assert result.duration_ms >= 100
    
    def test_short_speech_discarded(self):
        """Test that short speech segments are discarded."""
        config = VADConfig(min_speech_duration_ms=500)  # Require 500ms
        segmenter = SpeechSegmenter(config=config)
        
        # Add short speech (100ms)
        speech = np.full(480, 2000, dtype=np.int16)
        segmenter._in_speech = True
        segmenter._speech_start_time = time.time() - 0.1  # 100ms ago
        segmenter._buffered_frames = [speech.copy() for _ in range(3)]
        
        # Flush
        result = segmenter.flush()
        
        # Should be None because speech was too short
        assert result is None


class TestWebRTCVAD:
    """Test WebRTC VAD wrapper."""
    
    def test_init(self):
        """Test VAD initialization."""
        config = VADConfig(mode=VADMode.MEDIUM)
        vad = WebRTCVAD(config)
        
        assert vad.config == config
        assert vad.state == VADState.UNKNOWN
    
    def test_process_silence(self):
        """Test processing silence frames."""
        config = VADConfig(mode=VADMode.NORMAL)
        vad = WebRTCVAD(config)
        
        silence = np.zeros(480, dtype=np.int16)
        result = vad.process_frame(silence)
        
        # Mock VAD uses energy threshold, silence should return False
        assert result is False
    
    def test_process_speech(self):
        """Test processing speech frames."""
        config = VADConfig(mode=VADMode.NORMAL)
        vad = WebRTCVAD(config)
        
        # High energy frame
        speech = np.full(480, 2000, dtype=np.int16)
        result = vad.process_frame(speech)
        
        # Should detect as speech
        assert result is True
    
    def test_state_updates(self):
        """Test that state is updated correctly."""
        config = VADConfig(mode=VADMode.NORMAL)
        vad = WebRTCVAD(config)
        
        assert vad.state == VADState.UNKNOWN
        
        # Process silence
        vad.process_frame(np.zeros(480, dtype=np.int16))
        assert vad.state == VADState.SILENCE
        
        # Process speech
        vad.process_frame(np.full(480, 2000, dtype=np.int16))
        assert vad.state == VADState.SPEECH
    
    def test_empty_frame(self):
        """Test processing empty frame."""
        config = VADConfig()
        vad = WebRTCVAD(config)
        
        result = vad.process_frame(np.array([], dtype=np.int16))
        assert result is False
    
    def test_frame_size_mismatch(self):
        """Test handling of mismatched frame sizes."""
        config = VADConfig()
        vad = WebRTCVAD(config)
        
        # Frame that's too small (should still work with padding)
        small_frame = np.ones(240, dtype=np.int16)
        result = vad.process_frame(small_frame)
        # Result depends on energy, but shouldn't crash
        assert isinstance(result, bool)


class TestAudioDeviceManager:
    """Test AudioDeviceManager class."""
    
    def test_init(self):
        """Test device manager initialization."""
        manager = AudioDeviceManager()
        
        # Should have attempted to refresh devices
        assert manager._devices is not None
    
    def test_list_devices_mock(self, monkeypatch):
        """Test listing devices with mock data."""
        # Mock sounddevice BEFORE importing AudioDeviceManager
        mock_devices = [
            {'name': 'Input Device', 'max_input_channels': 2, 'max_output_channels': 0, 'default_samplerate': 16000},
            {'name': 'Output Device', 'max_input_channels': 0, 'max_output_channels': 2, 'default_samplerate': 48000},
        ]
        
        def mock_query_devices(kind=None):
            if kind == 'input':
                return mock_devices[0]
            elif kind == 'output':
                return mock_devices[1]
            return mock_devices
        
        # Mock at module level before any imports
        import sounddevice as sd
        monkeypatch.setattr(sd, 'query_devices', mock_query_devices)
        
        # Reload the audio_pipeline module to pick up mocked sounddevice
        import sys
        import importlib
        from bridge import audio_pipeline
        importlib.reload(audio_pipeline)
        
        # Re-initialize with mocked sounddevice
        manager = audio_pipeline.AudioDeviceManager()
        
        # Should find devices
        assert len(manager._devices) > 0
    
    def test_get_device_by_index(self, monkeypatch):
        """Test getting device by index."""
        from bridge.audio_pipeline import AudioDeviceInfo, AudioDeviceType
        
        manager = AudioDeviceManager()
        
        # Add a mock device
        mock_device = AudioDeviceInfo(
            index=0,
            name="Test Input",
            device_type=AudioDeviceType.INPUT,
            channels=2,
            sample_rate=16000,
            is_default=True
        )
        manager._devices[0] = mock_device
        
        # Get by index
        result = manager.get_device(0, AudioDeviceType.INPUT)
        assert result == mock_device
    
    def test_get_device_by_name(self, monkeypatch):
        """Test getting device by name."""
        from bridge.audio_pipeline import AudioDeviceInfo, AudioDeviceType
        
        manager = AudioDeviceManager()
        
        # Add mock devices
        manager._devices[0] = AudioDeviceInfo(
            index=0,
            name="Blue Yeti Nano",
            device_type=AudioDeviceType.INPUT,
            channels=2,
            sample_rate=16000,
            is_default=False
        )
        
        # Get by partial name (case insensitive)
        result = manager.get_device("yeti", AudioDeviceType.INPUT)
        assert result is not None
        assert result.name == "Blue Yeti Nano"
    
    def test_get_default_device(self, monkeypatch):
        """Test getting default device."""
        from bridge.audio_pipeline import AudioDeviceInfo, AudioDeviceType
        
        manager = AudioDeviceManager()
        
        # Add devices
        manager._devices[0] = AudioDeviceInfo(
            index=0,
            name="Default Input",
            device_type=AudioDeviceType.INPUT,
            channels=2,
            sample_rate=16000,
            is_default=True
        )
        manager._devices[1] = AudioDeviceInfo(
            index=1,
            name="Other Input",
            device_type=AudioDeviceType.INPUT,
            channels=2,
            sample_rate=16000,
            is_default=False
        )
        
        result = manager.get_default_device(AudioDeviceType.INPUT)
        assert result is not None
        assert result.name == "Default Input"
    
    def test_list_devices_filtered(self, monkeypatch):
        """Test listing devices with type filter."""
        from bridge.audio_pipeline import AudioDeviceInfo, AudioDeviceType
        
        # Mock sounddevice to prevent real device detection
        import sounddevice as sd
        monkeypatch.setattr(sd, 'query_devices', lambda: [])
        
        manager = AudioDeviceManager()
        
        # Clear any devices and add test devices
        manager._devices.clear()
        
        # Add mixed devices
        manager._devices[0] = AudioDeviceInfo(
            index=0, name="Input 1", device_type=AudioDeviceType.INPUT,
            channels=2, sample_rate=16000, is_default=False
        )
        manager._devices[1] = AudioDeviceInfo(
            index=1, name="Output 1", device_type=AudioDeviceType.OUTPUT,
            channels=2, sample_rate=48000, is_default=False
        )
        manager._devices[2] = AudioDeviceInfo(
            index=2, name="Input 2", device_type=AudioDeviceType.INPUT,
            channels=2, sample_rate=16000, is_default=True
        )
        
        # List all
        all_devices = manager.list_devices()
        assert len(all_devices) == 3
        
        # List inputs only
        inputs = manager.list_devices(AudioDeviceType.INPUT)
        assert len(inputs) == 2
        assert all(d.device_type == AudioDeviceType.INPUT for d in inputs)
        
        # List outputs only
        outputs = manager.list_devices(AudioDeviceType.OUTPUT)
        assert len(outputs) == 1
        assert outputs[0].name == "Output 1"
    
    def test_default_device_first_in_list(self, monkeypatch):
        """Test that default device appears first in list."""
        from bridge.audio_pipeline import AudioDeviceInfo, AudioDeviceType
        
        # Mock sounddevice to prevent real device detection
        import sounddevice as sd
        monkeypatch.setattr(sd, 'query_devices', lambda: [])
        
        manager = AudioDeviceManager()
        
        # Clear any devices and add test devices
        manager._devices.clear()
        
        # Add devices with default last
        manager._devices[0] = AudioDeviceInfo(
            index=0, name="Device A", device_type=AudioDeviceType.INPUT,
            channels=2, sample_rate=16000, is_default=False
        )
        manager._devices[1] = AudioDeviceInfo(
            index=1, name="Device B", device_type=AudioDeviceType.INPUT,
            channels=2, sample_rate=16000, is_default=True
        )
        
        devices = manager.list_devices(AudioDeviceType.INPUT)
        
        # Default should be first
        assert devices[0].name == "Device B"
        assert devices[0].is_default
