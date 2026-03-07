"""
Integration tests for audio pipeline components.

Tests interaction between audio buffer, VAD, and pipeline.
"""
import threading
import time

import numpy as np
import pytest

from bridge.audio_buffer import AudioBuffer
from bridge.vad import (
    WebRTCVAD,
    VADConfig,
    VADMode,
    SpeechSegmenter,
    SpeechSegment,
    MockVAD,
)
from bridge.audio_pipeline import (
    AudioPipeline,
    AudioDeviceManager,
    AudioDeviceType,
    PipelineState,
)


class TestAudioBufferVADIntegration:
    """Test integration between AudioBuffer and VAD."""
    
    def test_buffer_to_vad_flow(self):
        """Test audio flow from buffer to VAD."""
        # Create buffer and VAD
        buffer = AudioBuffer(max_frames=10, frame_size=480)
        vad = MockVAD(VADConfig(mode=VADMode.NORMAL))
        
        # Simulate audio capture
        speech_frames = []
        for _ in range(5):
            frame = np.full(480, 2000, dtype=np.int16)
            buffer.write(frame)
            speech_frames.append(frame)
        
        # Process through VAD
        results = []
        while not buffer.is_empty:
            frame = buffer.read()
            if frame is not None:
                is_speech = vad.process_frame(frame)
                results.append(is_speech)
        
        # All frames should be detected as speech
        assert all(results)
    
    def test_concurrent_buffer_vad(self):
        """Test concurrent buffer writes and VAD processing."""
        buffer = AudioBuffer(max_frames=50, frame_size=480)
        vad = MockVAD(VADConfig(mode=VADMode.NORMAL))
        
        speech_detected = []
        
        def writer():
            for i in range(30):
                frame = np.full(480, 2000 if i % 2 == 0 else 0, dtype=np.int16)
                buffer.write(frame, block=True, timeout=1.0)
                time.sleep(0.001)
        
        def reader():
            for _ in range(30):
                frame = buffer.read(block=True, timeout=1.0)
                if frame is not None:
                    is_speech = vad.process_frame(frame)
                    speech_detected.append(is_speech)
                time.sleep(0.001)
        
        writer_thread = threading.Thread(target=writer)
        reader_thread = threading.Thread(target=reader)
        
        writer_thread.start()
        reader_thread.start()
        writer_thread.join()
        reader_thread.join()
        
        # Should have processed frames
        assert len(speech_detected) > 0


class TestVADSegmenterIntegration:
    """Test integration between VAD and SpeechSegmenter."""
    
    def test_segmenter_with_vad(self):
        """Test speech segmentation with VAD."""
        config = VADConfig(
            mode=VADMode.NORMAL,
            min_speech_duration_ms=100,
            min_silence_duration_ms=100,
            frame_duration_ms=30
        )
        
        vad = MockVAD(config)
        segmenter = SpeechSegmenter(vad, config)
        
        # Simulate speech
        speech_frame = np.full(480, 2000, dtype=np.int16)
        silence_frame = np.zeros(480, dtype=np.int16)
        
        segments = []
        
        # Send some silence
        for _ in range(3):
            segmenter.process_frame(silence_frame)
        
        # Send speech (at least 100ms = ~4 frames @ 30ms)
        for _ in range(5):
            result = segmenter.process_frame(speech_frame)
            if result:
                segments.append(result)
        
        # Send silence to end (at least 100ms)
        for _ in range(5):
            result = segmenter.process_frame(silence_frame)
            if result:
                segments.append(result)
        
        # Should have detected at least one segment
        assert len(segments) >= 1
        assert all(isinstance(s, SpeechSegment) for s in segments)
    
    def test_segmenter_barge_in_simulation(self):
        """Test segmenter behavior during barge-in scenario."""
        config = VADConfig(
            mode=VADMode.NORMAL,
            min_speech_duration_ms=50,
            min_silence_duration_ms=50,
            frame_duration_ms=30
        )
        
        vad = MockVAD(config)
        segmenter = SpeechSegmenter(vad, config)
        
        speech_frame = np.full(480, 2000, dtype=np.int16)
        silence_frame = np.zeros(480, dtype=np.int16)
        
        # First speech segment
        for _ in range(3):
            segmenter.process_frame(speech_frame)
        
        # Brief silence (barge-in gap)
        for _ in range(2):
            segmenter.process_frame(silence_frame)
        
        # Second speech (barge-in)
        for _ in range(3):
            segmenter.process_frame(speech_frame)
        
        # End with silence
        segments = []
        for _ in range(5):
            result = segmenter.process_frame(silence_frame)
            if result:
                segments.append(result)
        
        # Should detect the combined or separate segments
        assert len(segments) >= 1


class TestAudioPipelineIntegration:
    """Test full audio pipeline integration."""
    
    def test_pipeline_init(self):
        """Test pipeline initialization."""
        pipeline = AudioPipeline()
        
        assert pipeline.state == PipelineState.IDLE
        assert pipeline._barge_in_enabled
        assert pipeline._input_stream is None
        assert pipeline._output_stream is None
    
    def test_state_transitions(self):
        """Test pipeline state transitions."""
        pipeline = AudioPipeline()
        
        states_recorded = []
        
        def on_state_change(old, new):
            states_recorded.append((old, new))
        
        pipeline.add_state_callback(on_state_change)
        
        # Transition to LISTENING
        pipeline._set_state(PipelineState.LISTENING)
        assert pipeline.state == PipelineState.LISTENING
        
        # Transition to SPEAKING
        pipeline._set_state(PipelineState.SPEAKING)
        assert pipeline.state == PipelineState.SPEAKING
        
        # Should have recorded transitions
        assert len(states_recorded) == 2
        assert states_recorded[0] == (PipelineState.IDLE, PipelineState.LISTENING)
        assert states_recorded[1] == (PipelineState.LISTENING, PipelineState.SPEAKING)
    
    def test_barge_in_toggle(self):
        """Test barge-in enable/disable."""
        pipeline = AudioPipeline()
        
        assert pipeline._barge_in_enabled
        
        pipeline.enable_barge_in(False)
        assert not pipeline._barge_in_enabled
        
        pipeline.enable_barge_in(True)
        assert pipeline._barge_in_enabled
    
    def test_stats_access(self):
        """Test statistics access."""
        pipeline = AudioPipeline()
        
        stats = pipeline.stats
        assert isinstance(stats, PipelineStats)
        assert stats.start_time > 0
    
    def test_initialize_devices_without_sounddevice(self, monkeypatch):
        """Test device initialization when sounddevice unavailable."""
        pipeline = AudioPipeline()
        
        # Mock device manager to return None
        def mock_get_device(identifier, device_type):
            return None
        
        def mock_get_default(device_type):
            return None
        
        monkeypatch.setattr(pipeline.device_manager, 'get_device', mock_get_device)
        monkeypatch.setattr(pipeline.device_manager, 'get_default_device', mock_get_default)
        
        result = pipeline.initialize_devices()
        assert result is False
    
    def test_initialize_devices_success(self, monkeypatch):
        """Test successful device initialization."""
        from bridge.audio_pipeline import AudioDeviceInfo, AudioDeviceType
        
        pipeline = AudioPipeline()
        
        # Create mock devices
        input_device = AudioDeviceInfo(
            index=0,
            name="Test Input",
            device_type=AudioDeviceType.INPUT,
            channels=2,
            sample_rate=16000,
            is_default=True
        )
        
        output_device = AudioDeviceInfo(
            index=1,
            name="Test Output",
            device_type=AudioDeviceType.OUTPUT,
            channels=2,
            sample_rate=48000,
            is_default=True
        )
        
        def mock_get_device(identifier, device_type):
            if device_type == AudioDeviceType.INPUT:
                return input_device
            return output_device
        
        def mock_get_default(device_type):
            if device_type == AudioDeviceType.INPUT:
                return input_device
            return output_device
        
        monkeypatch.setattr(pipeline.device_manager, 'get_device', mock_get_device)
        monkeypatch.setattr(pipeline.device_manager, 'get_default_device', mock_get_default)
        
        result = pipeline.initialize_devices()
        assert result is True
        assert pipeline._input_device == input_device
        assert pipeline._output_device == output_device
    
    def test_start_capture_without_sounddevice(self, monkeypatch):
        """Test capture start when sounddevice unavailable."""
        pipeline = AudioPipeline()
        
        # Mock sounddevice unavailable
        monkeypatch.setattr('bridge.audio_pipeline.SOUNDDEVICE_AVAILABLE', False)
        
        result = pipeline.start_capture()
        assert result is False
    
    def test_start_capture_without_device(self):
        """Test capture start without initialized device."""
        pipeline = AudioPipeline()
        
        result = pipeline.start_capture()
        assert result is False
    
    def test_stop_capture_without_start(self):
        """Test stopping capture when not started."""
        pipeline = AudioPipeline()
        
        # Should not raise
        pipeline.stop_capture()
        
        assert pipeline._input_stream is None
    
    def test_start_playback_without_sounddevice(self, monkeypatch):
        """Test playback start when sounddevice unavailable."""
        pipeline = AudioPipeline()
        
        monkeypatch.setattr('bridge.audio_pipeline.SOUNDDEVICE_AVAILABLE', False)
        
        result = pipeline.start_playback()
        assert result is False
    
    def test_start_playback_without_device(self):
        """Test playback start without initialized device."""
        pipeline = AudioPipeline()
        
        result = pipeline.start_playback()
        assert result is False
    
    def test_stop_playback_without_start(self):
        """Test stopping playback when not started."""
        pipeline = AudioPipeline()
        
        # Should not raise
        pipeline.stop_playback()
        
        assert pipeline._output_stream is None
    
    def test_play_audio_in_error_state(self):
        """Test playing audio when in error state."""
        pipeline = AudioPipeline()
        pipeline._set_state(PipelineState.ERROR)
        
        audio = np.ones(16000, dtype=np.int16)
        result = pipeline.play_audio(audio)
        
        assert result is False
    
    def test_play_audio_success(self):
        """Test successful audio playback queueing."""
        pipeline = AudioPipeline()
        
        # Start in IDLE state
        assert pipeline.state == PipelineState.IDLE
        
        # Queue audio
        audio = np.ones(16000, dtype=np.int16)
        result = pipeline.play_audio(audio)
        
        # Should succeed and transition to SPEAKING
        assert result is True
        assert pipeline.state == PipelineState.SPEAKING
        assert pipeline._is_speaking
        assert pipeline.stats.tts_utterances_played == 1
    
    def test_stop_playback_immediate(self):
        """Test immediate playback stop (barge-in)."""
        pipeline = AudioPipeline()
        
        # Set up speaking state
        pipeline._set_state(PipelineState.SPEAKING)
        pipeline._is_speaking = True
        
        # Add some audio to output buffer
        audio = np.ones(16000, dtype=np.int16)
        pipeline.play_audio(audio)
        
        # Trigger barge-in
        pipeline.stop_playback_immediate()
        
        # Should have cleared and reset
        assert not pipeline._is_speaking
        assert pipeline.state == PipelineState.LISTENING
        assert pipeline.stats.barge_in_count == 1
    
    def test_stop_playback_immediate_not_speaking(self):
        """Test barge-in when not speaking."""
        pipeline = AudioPipeline()
        
        # Not speaking
        pipeline._is_speaking = False
        
        # Should not crash
        pipeline.stop_playback_immediate()
        
        assert not pipeline._is_speaking
        assert pipeline.stats.barge_in_count == 0
    
    def test_start_stop_full_pipeline(self, monkeypatch):
        """Test starting and stopping the full pipeline."""
        from bridge.audio_pipeline import AudioDeviceInfo, AudioDeviceType
        
        pipeline = AudioPipeline()
        
        # Mock devices
        input_device = AudioDeviceInfo(
            index=0, name="Input", device_type=AudioDeviceType.INPUT,
            channels=2, sample_rate=16000, is_default=True
        )
        output_device = AudioDeviceInfo(
            index=1, name="Output", device_type=AudioDeviceType.OUTPUT,
            channels=2, sample_rate=48000, is_default=True
        )
        
        def mock_get_device(identifier, device_type):
            return input_device if device_type == AudioDeviceType.INPUT else output_device
        
        def mock_get_default(device_type):
            return input_device if device_type == AudioDeviceType.INPUT else output_device
        
        monkeypatch.setattr(pipeline.device_manager, 'get_device', mock_get_device)
        monkeypatch.setattr(pipeline.device_manager, 'get_default_device', mock_get_default)
        
        # Mock sounddevice
        mock_stream = MagicMock()
        monkeypatch.setattr('bridge.audio_pipeline.SOUNDDEVICE_AVAILABLE', True)
        monkeypatch.setattr('sounddevice.InputStream', lambda **kwargs: mock_stream)
        monkeypatch.setattr('sounddevice.OutputStream', lambda **kwargs: mock_stream)
        
        # Start pipeline
        result = pipeline.start()
        assert result is True
        assert pipeline.state == PipelineState.LISTENING
        
        # Stop pipeline
        pipeline.stop()
        assert pipeline.state == PipelineState.IDLE
    
    def test_context_manager(self, monkeypatch):
        """Test using pipeline as context manager."""
        from bridge.audio_pipeline import AudioDeviceInfo, AudioDeviceType
        
        # Mock devices and sounddevice
        input_device = AudioDeviceInfo(
            index=0, name="Input", device_type=AudioDeviceType.INPUT,
            channels=2, sample_rate=16000, is_default=True
        )
        output_device = AudioDeviceInfo(
            index=1, name="Output", device_type=AudioDeviceType.OUTPUT,
            channels=2, sample_rate=48000, is_default=True
        )
        
        def mock_get_device(identifier, device_type):
            return input_device if device_type == AudioDeviceType.INPUT else output_device
        
        def mock_get_default(device_type):
            return input_device if device_type == AudioDeviceType.INPUT else output_device
        
        mock_stream = MagicMock()
        
        with patch('bridge.audio_pipeline.SOUNDDEVICE_AVAILABLE', True):
            with patch.object(AudioDeviceManager, 'get_device', mock_get_device):
                with patch.object(AudioDeviceManager, 'get_default_device', mock_get_default):
                    with patch('sounddevice.InputStream', return_value=mock_stream):
                        with patch('sounddevice.OutputStream', return_value=mock_stream):
                            # Use pipeline as context manager
                            with AudioPipeline() as pipeline:
                                assert pipeline.state == PipelineState.LISTENING
                            
                            # After exit, should be IDLE
                            assert pipeline.state == PipelineState.IDLE


class TestAudioPipelineStateTransitions:
    """Test pipeline state transitions."""
    
    def test_idle_to_listening(self):
        """Test transition from IDLE to LISTENING."""
        pipeline = AudioPipeline()
        
        assert pipeline.state == PipelineState.IDLE
        
        # Simulate starting capture
        pipeline._set_state(PipelineState.LISTENING)
        
        assert pipeline.state == PipelineState.LISTENING
        assert pipeline.stats.state_changes == 1
    
    def test_listening_to_speaking(self):
        """Test transition from LISTENING to SPEAKING."""
        pipeline = AudioPipeline()
        pipeline._set_state(PipelineState.LISTENING)
        
        # Play audio to transition to SPEAKING
        audio = np.ones(16000, dtype=np.int16)
        pipeline.play_audio(audio)
        
        assert pipeline.state == PipelineState.SPEAKING
    
    def test_speaking_to_listening_barge_in(self):
        """Test transition from SPEAKING to LISTENING via barge-in."""
        pipeline = AudioPipeline()
        pipeline._set_state(PipelineState.SPEAKING)
        pipeline._is_speaking = True
        
        # Trigger barge-in
        pipeline.stop_playback_immediate()
        
        assert pipeline.state == PipelineState.LISTENING
        assert not pipeline._is_speaking
    
    def test_error_state(self):
        """Test transition to ERROR state."""
        pipeline = AudioPipeline()
        
        pipeline._set_state(PipelineState.ERROR)
        
        assert pipeline.state == PipelineState.ERROR


class TestAudioPipelineConcurrency:
    """Test concurrent operations on audio pipeline."""
    
    def test_concurrent_state_access(self):
        """Test concurrent state reads and writes."""
        pipeline = AudioPipeline()
        
        results = []
        
        def reader():
            for _ in range(50):
                state = pipeline.state
                results.append(('read', state))
                time.sleep(0.001)
        
        def writer():
            states = [PipelineState.LISTENING, PipelineState.SPEAKING, PipelineState.IDLE]
            for i in range(50):
                pipeline._set_state(states[i % len(states)])
                results.append(('write', states[i % len(states)]))
                time.sleep(0.001)
        
        reader_thread = threading.Thread(target=reader)
        writer_thread = threading.Thread(target=writer)
        
        reader_thread.start()
        writer_thread.start()
        reader_thread.join()
        writer_thread.join()
        
        # Should have completed without errors
        assert len(results) == 100
    
    def test_concurrent_audio_queueing(self):
        """Test concurrent audio queueing."""
        pipeline = AudioPipeline()
        
        success_count = [0]
        
        def queue_audio():
            for _ in range(10):
                audio = np.ones(16000, dtype=np.int16)
                if pipeline.play_audio(audio):
                    success_count[0] += 1
                time.sleep(0.001)
        
        threads = [threading.Thread(target=queue_audio) for _ in range(3)]
        
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        
        # Should have queued some audio
        assert success_count[0] > 0


class TestAudioPipelineEdgeCases:
    """Test edge cases and error handling."""
    
    def test_play_audio_empty(self):
        """Test playing empty audio."""
        pipeline = AudioPipeline()
        
        empty_audio = np.array([], dtype=np.int16)
        result = pipeline.play_audio(empty_audio)
        
        # Should handle gracefully
        assert isinstance(result, bool)
    
    def test_play_audio_wrong_dtype(self):
        """Test playing audio with wrong dtype."""
        pipeline = AudioPipeline()
        
        # Float instead of int16
        float_audio = np.ones(16000, dtype=np.float32)
        
        # Should handle or convert
        result = pipeline.play_audio(float_audio)
        assert isinstance(result, bool)
    
    def test_barge_in_when_not_speaking(self):
        """Test barge-in when not in speaking state."""
        pipeline = AudioPipeline()
        
        # Should not crash
        pipeline.stop_playback_immediate()
        
        assert pipeline.state == PipelineState.IDLE
        assert pipeline.stats.barge_in_count == 0
    
    def test_multiple_state_callbacks(self):
        """Test multiple state callbacks."""
        pipeline = AudioPipeline()
        
        calls = []
        
        def callback1(old, new):
            calls.append('callback1')
        
        def callback2(old, new):
            calls.append('callback2')
        
        pipeline.add_state_callback(callback1)
        pipeline.add_state_callback(callback2)
        
        pipeline._set_state(PipelineState.LISTENING)
        
        assert 'callback1' in calls
        assert 'callback2' in calls
    
    def test_callback_exception_handling(self):
        """Test that callback exceptions don't break pipeline."""
        pipeline = AudioPipeline()
        
        def bad_callback(old, new):
            raise ValueError("Test error")
        
        def good_callback(old, new):
            pass
        
        pipeline.add_state_callback(bad_callback)
        pipeline.add_state_callback(good_callback)
        
        # Should not raise
        pipeline._set_state(PipelineState.LISTENING)
        
        assert pipeline.state == PipelineState.LISTENING
