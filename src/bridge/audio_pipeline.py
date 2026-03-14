"""
Audio Pipeline for Voice Bridge.

Manages audio I/O, voice activity detection, buffering, and barge-in.
Connects microphone input to STT and TTS output to speakers.
"""
import enum
import queue
import threading
import time
from dataclasses import dataclass
from typing import Callable, Optional, List, Dict, Any

import structlog
import numpy as np

from bridge.config import get_config, AudioConfig
from bridge.vad import WebRTCVAD, VADConfig, VADMode, SpeechSegmenter, SpeechSegment
from bridge.audio_buffer import AudioBuffer
from bridge.errorcapture import ErrorCapture, capture_errors
from bridge.bug_tracker import BugSeverity

logger = structlog.get_logger()

# Optional imports for audio I/O
try:
    import sounddevice as sd
    SOUNDDEVICE_AVAILABLE = True
except ImportError:
    SOUNDDEVICE_AVAILABLE = False
    logger.warning("sounddevice not available, audio I/O disabled")


class PipelineState(enum.Enum):
    """Audio pipeline states."""
    IDLE = "idle"                    # Waiting for wake word
    LISTENING = "listening"          # Capturing speech
    PROCESSING = "processing"         # STT/OpenClaw processing
    SPEAKING = "speaking"            # TTS playback
    ERROR = "error"                  # Error state


class AudioDeviceType(enum.Enum):
    """Type of audio device."""
    INPUT = "input"
    OUTPUT = "output"


@dataclass
class AudioDeviceInfo:
    """Information about an audio device."""
    index: int
    name: str
    device_type: AudioDeviceType
    channels: int
    sample_rate: int
    is_default: bool = False


@dataclass
class PipelineStats:
    """Audio pipeline statistics."""
    state_changes: int = 0
    speech_segments_detected: int = 0
    audio_frames_processed: int = 0
    tts_utterances_played: int = 0
    barge_in_count: int = 0
    error_count: int = 0
    start_time: float = 0.0
    queue_overflow_count: int = 0
    
    @property
    def uptime_seconds(self) -> float:
        """Get pipeline uptime in seconds."""
        if self.start_time == 0:
            return 0
        return time.time() - self.start_time


class AudioDeviceManager:
    """
    Manages audio device discovery and selection.
    
    Handles device enumeration, selection by name/index,
    and provides device information.
    """
    
    def __init__(self):
        """Initialize device manager."""
        self._devices: Dict[int, AudioDeviceInfo] = {}
        self._refresh_devices()
    
    def _refresh_devices(self):
        """Refresh device list from sounddevice."""
        self._devices.clear()
        
        if not SOUNDDEVICE_AVAILABLE:
            logger.warning("sounddevice not available, no devices found")
            return
        
        try:
            # Get all devices
            devices = sd.query_devices()
            default_input = sd.query_devices(kind='input')
            default_output = sd.query_devices(kind='output')
            
            for idx, device in enumerate(devices):
                # Determine device type
                max_input = device.get('max_input_channels', 0)
                max_output = device.get('max_output_channels', 0)
                
                # Devices can be both input and output (like pipewire/default)
                # Create separate entries for each type
                if max_input > 0:
                    is_default = (device.get('name') == default_input.get('name')) if default_input else False
                    info = AudioDeviceInfo(
                        index=idx,
                        name=device['name'],
                        device_type=AudioDeviceType.INPUT,
                        channels=max_input,
                        sample_rate=int(device.get('default_samplerate', 16000)),
                        is_default=is_default
                    )
                    self._devices[f"{idx}_input"] = info
                
                if max_output > 0:
                    is_default = (device.get('name') == default_output.get('name')) if default_output else False
                    info = AudioDeviceInfo(
                        index=idx,
                        name=device['name'],
                        device_type=AudioDeviceType.OUTPUT,
                        channels=max_output,
                        sample_rate=int(device.get('default_samplerate', 16000)),
                        is_default=is_default
                    )
                    self._devices[f"{idx}_output"] = info
            
            logger.info(
                "devices_refreshed",
                input_count=sum(1 for d in self._devices.values() if d.device_type == AudioDeviceType.INPUT),
                output_count=sum(1 for d in self._devices.values() if d.device_type == AudioDeviceType.OUTPUT)
            )
            
        except Exception as e:
            logger.error("device_refresh_failed", error=str(e))
    
    def list_devices(self, device_type: Optional[AudioDeviceType] = None) -> List[AudioDeviceInfo]:
        """
        List available audio devices.
        
        Args:
            device_type: Filter by type (input/output) or None for all
            
        Returns:
            List of device information
        """
        devices = list(self._devices.values())
        if device_type:
            devices = [d for d in devices if d.device_type == device_type]
        return sorted(devices, key=lambda d: (not d.is_default, d.name))
    
    def get_device(self, identifier: str or int, device_type: AudioDeviceType) -> Optional[AudioDeviceInfo]:
        """
        Get device by index or name.
        
        Args:
            identifier: Device index (int) or name (str)
            device_type: Expected device type
            
        Returns:
            Device info or None if not found
        """
        type_suffix = "_input" if device_type == AudioDeviceType.INPUT else "_output"
        
        if isinstance(identifier, int):
            # Look up by index with type suffix
            key = f"{identifier}{type_suffix}"
            device = self._devices.get(key)
            if device:
                return device
        else:
            # Search by name (case-insensitive, partial match)
            identifier_lower = identifier.lower()
            for key, device in self._devices.items():
                if (device.device_type == device_type and 
                    identifier_lower in device.name.lower()):
                    return device
        return None
    
    def get_default_device(self, device_type: AudioDeviceType) -> Optional[AudioDeviceInfo]:
        """
        Get default device for type.
        
        Args:
            device_type: Input or output
            
        Returns:
            Default device info or None
        """
        for device in self._devices.values():
            if device.device_type == device_type and device.is_default:
                return device
        return None


class AudioPipeline:
    """
    Main audio pipeline managing I/O, VAD, and barge-in.
    
    Coordinates audio capture, voice activity detection, buffering,
    and playback with support for interruption (barge-in).
    
    Uses a queue-based producer-consumer pattern:
    - Audio callback (producer): Copies frames to queue, returns in &lt;1ms
    - Worker thread (consumer): Pulls from queue and processes frames
    """
    
    def __init__(
        self,
        audio_config: Optional[AudioConfig] = None,
        vad_config: Optional[VADConfig] = None
    ):
        """
        Initialize audio pipeline.
        
        Args:
            audio_config: Audio configuration (loads from config if None)
            vad_config: VAD configuration (uses defaults if None)
        """
        # Load configuration
        if audio_config is None:
            config = get_config()
            audio_config = config.audio
        
        self.audio_config = audio_config
        self.vad_config = vad_config or VADConfig()
        
        # Initialize components
        self.device_manager = AudioDeviceManager()
        self.vad = WebRTCVAD(self.vad_config)
        self.segmenter = SpeechSegmenter(self.vad, self.vad_config)
        
        # Audio buffers - use sample counts
        vad_frame_samples = int(
            self.audio_config.sample_rate * self.vad_config.frame_duration_ms / 1000
        )
        self.input_buffer = AudioBuffer(
            max_samples=vad_frame_samples * 100,  # 100 frames of VAD audio
            sample_rate=self.audio_config.sample_rate
        )
        # FIFO queue for audio output frames.  Each entry is a 1024-sample
        # np.int16 array at the device native rate.  512 slots ≈ 10 s at
        # 48 kHz — large enough for any TTS response.  Using a proper FIFO
        # here (instead of the ring-buffer AudioBuffer) ensures each frame
        # is played exactly once; the old non-destructive read() caused the
        # callback to loop the same 1024-sample chunk forever.
        self._output_queue: queue.Queue = queue.Queue(maxsize=512)
        
        # State
        self._state = PipelineState.IDLE
        self._state_lock = threading.RLock()
        self._state_callbacks: List[Callable[[PipelineState, PipelineState], None]] = []
        self._frame_callbacks: List[Callable[[np.ndarray, int], None]] = []  # Changed to bytes, sample_rate for compatibility
        self._speech_segment_callbacks: List[Callable[[SpeechSegment], None]] = []  # Speech segment callbacks
        
        # Audio I/O
        self._input_stream = None
        self._output_stream = None
        self._input_device = None
        self._output_device = None
        
        # Wake word frame buffering
        self._wake_word_frame_size = getattr(self.audio_config, 'wake_word_frame_size', 1280)
        self._wake_word_buffer: List[np.ndarray] = []
        self._wake_word_buffer_lock = threading.Lock()
        
        # Queue-based audio processing
        self._audio_queue = None
        self._worker_thread = None
        self._stop_event = None
        
        # Barge-in
        self._barge_in_enabled = True
        self._is_speaking = False
        self._barge_in_lock = threading.Lock()
        
        # Statistics
        self._stats = PipelineStats(start_time=time.time())
        
        # Error capture
        self.error_capture = ErrorCapture(component="audio_pipeline", severity=BugSeverity.HIGH)
        
        logger.info(
            "audio_pipeline_initialized",
            sample_rate=self.audio_config.sample_rate,
            vad_mode=self.vad_config.mode.name,
            barge_in=self._barge_in_enabled,
            wake_word_frame_size=self._wake_word_frame_size
        )
    
    @property
    def state(self) -> PipelineState:
        """Get current pipeline state."""
        with self._state_lock:
            return self._state
    
    def _set_state(self, new_state: PipelineState):
        """Set pipeline state and notify callbacks."""
        with self._state_lock:
            old_state = self._state
            if old_state != new_state:
                self._state = new_state
                self._stats.state_changes += 1
                logger.info(
                    "pipeline_state_changed",
                    old=old_state.value,
                    new=new_state.value
                )
                
                # Notify callbacks
                for callback in self._state_callbacks[:]:  # Copy to avoid modification during iteration
                    try:
                        callback(old_state, new_state)
                    except Exception as e:
                        logger.error("state_callback_error", error=str(e))
    
    def add_state_callback(self, callback: Callable[[PipelineState, PipelineState], None]):
        """Add callback for state changes."""
        self._state_callbacks.append(callback)
    
    def remove_state_callback(self, callback: Callable[[PipelineState, PipelineState], None]):
        """Remove state change callback."""
        if callback in self._state_callbacks:
            self._state_callbacks.remove(callback)
    
    def add_frame_callback(self, callback: Callable[[np.ndarray, int], None]):
        """Add callback for audio frames (for wake word detection). Passes (audio_frame, sample_rate)"""
        self._frame_callbacks.append(callback)
    
    def remove_frame_callback(self, callback: Callable[[np.ndarray, int], None]):
        """Remove frame callback."""
        if callback in self._frame_callbacks:
            self._frame_callbacks.remove(callback)
    
    def add_speech_segment_callback(self, callback: Callable[[SpeechSegment], None]):
        """Add callback for speech segments (for STT)."""
        self._speech_segment_callbacks.append(callback)
    
    def remove_speech_segment_callback(self, callback: Callable[[SpeechSegment], None]):
        """Remove speech segment callback."""
        if callback in self._speech_segment_callbacks:
            self._speech_segment_callbacks.remove(callback)
    
    @property
    def stats(self) -> PipelineStats:
        """Get pipeline statistics."""
        return self._stats
    
    def initialize_devices(
        self,
        input_device: Optional[str or int] = None,
        output_device: Optional[str or int] = None
    ) -> bool:
        """
        Initialize audio input/output devices.
        
        Args:
            input_device: Input device name or index (uses config default if None)
            output_device: Output device name or index (uses config default if None)
            
        Returns:
            True if both devices initialized successfully
        """
        # Resolve devices
        if input_device is None:
            input_device = getattr(self.audio_config, 'input_device', None)
        if output_device is None:
            output_device = getattr(self.audio_config, 'output_device', None)
        
        # Get input device
        if input_device is not None:
            self._input_device = self.device_manager.get_device(
                input_device, AudioDeviceType.INPUT
            )
        if self._input_device is None:
            self._input_device = self.device_manager.get_default_device(
                AudioDeviceType.INPUT
            )
        
        # Get output device
        if output_device is not None:
            self._output_device = self.device_manager.get_device(
                output_device, AudioDeviceType.OUTPUT
            )
        if self._output_device is None:
            self._output_device = self.device_manager.get_default_device(
                AudioDeviceType.OUTPUT
            )
        
        if self._input_device is None:
            logger.error("no_input_device_found")
            return False
        if self._output_device is None:
            logger.error("no_output_device_found")
            return False
        
        logger.info(
            "devices_initialized",
            input_device=self._input_device.name,
            input_index=self._input_device.index,
            output_device=self._output_device.name,
            output_index=self._output_device.index
        )
        
        return True
    
    def start_capture(self) -> bool:
        """
        Start audio capture from input device.
        
        Returns:
            True if capture started successfully
        """
        if not SOUNDDEVICE_AVAILABLE:
            logger.error("sounddevice_not_available")
            return False
        
        if self._input_device is None:
            logger.error("no_input_device_initialized")
            return False
        
        try:
            # Initialize queue and stop event for worker thread
            # Queue size: at 16kHz/1024 = ~15 fps, 500 frames = ~33 seconds buffer
            self._audio_queue = queue.Queue(maxsize=500)
            self._stop_event = threading.Event()
            
            frame_size = int(
                self.audio_config.sample_rate * self.vad_config.frame_duration_ms / 1000
            )
            
            self._input_stream = sd.InputStream(
                device=self._input_device.index,
                channels=1,
                samplerate=self.audio_config.sample_rate,
                blocksize=frame_size,
                dtype=np.int16,
                callback=self._audio_input_callback
            )
            
            self._input_stream.start()
            
            # Start worker thread for processing
            self._worker_thread = threading.Thread(
                target=self._worker_loop,
                daemon=True,
                name="AudioPipelineWorker"
            )
            self._worker_thread.start()
            
            self._set_state(PipelineState.LISTENING)
            
            logger.info(
                "audio_capture_started",
                frame_size=frame_size,
                wake_word_frame_size=self._wake_word_frame_size,
                queue_maxsize=100
            )
            return True
            
        except Exception as e:
            logger.error("audio_capture_start_failed", error=str(e))
            self._set_state(PipelineState.ERROR)
            # Cleanup
            self._audio_queue = None
            self._stop_event = None
            self._worker_thread = None
            return False
    
    def stop_capture(self):
        """Stop audio capture."""
        # Signal worker thread to stop
        if self._stop_event:
            self._stop_event.set()
        
        # Wait for worker thread to finish (short timeout)
        if self._worker_thread and self._worker_thread.is_alive():
            self._worker_thread.join(timeout=1.0)
            if self._worker_thread.is_alive():
                logger.warning("worker_thread_did_not_stop_cleanly")
        
        self._worker_thread = None
        self._stop_event = None
        
        if self._input_stream:
            try:
                self._input_stream.stop()
                self._input_stream.close()
                logger.info("audio_capture_stopped")
            except Exception as e:
                logger.error("audio_capture_stop_error", error=str(e))
            self._input_stream = None
        
        # Drain queue
        if self._audio_queue:
            drained = 0
            try:
                while True:
                    self._audio_queue.get_nowait()
                    drained += 1
            except queue.Empty:
                pass
            logger.debug("drained_queue", items=drained)
            self._audio_queue = None
        
        if self.state == PipelineState.LISTENING:
            self._set_state(PipelineState.IDLE)
        
        # Clear wake word buffer
        with self._wake_word_buffer_lock:
            self._wake_word_buffer.clear()
    
    def _worker_loop(self):
        """
        Worker thread loop - processes audio frames from queue.
        
        This runs in a separate thread and can safely perform blocking operations.
        """
        logger.info("worker_thread_started", queue_size=self._audio_queue.maxsize if self._audio_queue else 0)
        frames_processed = 0
        
        with self.error_capture.context(context="capture_loop"):
            while not self._stop_event.is_set():
                try:
                    audio_data = self._audio_queue.get(timeout=0.05)
                except queue.Empty:
                    continue
                
                try:
                    self._process_audio_frame(audio_data)
                    frames_processed += 1
                    
                    # Log every 100 frames to confirm audio is flowing
                    if frames_processed % 100 == 0:
                        logger.debug("worker_frames_processed", count=frames_processed)
                except Exception as e:
                    if not self._stop_event.is_set():
                        logger.error("worker_frame_error", error=str(e), exc_info=True)
                        # Don't re-raise - keep processing
            
            # Drain remaining queue on exit
            while not self._audio_queue.empty():
                try:
                    audio_data = self._audio_queue.get_nowait()
                    self._process_audio_frame(audio_data)
                except queue.Empty:
                    break
                except Exception:
                    break
        
        logger.info("worker_thread_stopped")
    
    def _process_audio_frame(self, audio_data: bytes):
        """
        Process single audio frame in worker thread.
        
        All original callback logic moved here.
        Buffers frames until wake_word_frame_size is reached, then calls callbacks.
        """
        with self.error_capture.context(context="frame_processing"):
            # Convert bytes back to np array
            indata = np.frombuffer(audio_data, dtype=np.int16).reshape(-1, 1)
            
            # Convert to mono if needed (though stream is mono)
            if indata.shape[1] > 1:
                audio_frame = indata.mean(axis=1).astype(np.int16)
            else:
                audio_frame = indata[:, 0].astype(np.int16)
            
            # Store in input buffer
            self.input_buffer.write(audio_frame, block=False)
            self._stats.audio_frames_processed += 1
            
            # Buffer frames for wake word detection
            # Wake word typically needs 1280 samples (80ms at 16kHz)
            # We accumulate VAD-sized frames until we reach wake_word_frame_size
            native_rate = self.audio_config.sample_rate
            vad_rate = 16000
            
            # Resample to 16kHz for consistent wake word processing
            if native_rate != vad_rate:
                try:
                    from scipy import signal
                    num_samples = int(len(audio_frame) * vad_rate / native_rate)
                    wake_word_frame = signal.resample(audio_frame.astype(np.float64), num_samples).astype(np.int16)
                except ImportError:
                    # Fallback: decimate if higher rate, or simple approach
                    if native_rate > vad_rate and native_rate % vad_rate == 0:
                        ratio = native_rate // vad_rate
                        wake_word_frame = audio_frame[::ratio]
                    else:
                        wake_word_frame = audio_frame
            else:
                wake_word_frame = audio_frame
            
            # Lock when accessing wake word buffer
            with self._wake_word_buffer_lock:
                self._wake_word_buffer.append(wake_word_frame)
                total_samples = sum(frame.shape[0] for frame in self._wake_word_buffer)
                
                # Check if we have enough samples for a wake word frame
                while total_samples >= self._wake_word_frame_size:
                    # Collect frames until we have enough
                    frames_to_combine = []
                    samples_collected = 0
                    
                    for f in self._wake_word_buffer:
                        frames_to_combine.append(f)
                        samples_collected += f.shape[0]
                        if samples_collected >= self._wake_word_frame_size:
                            break
                    
                    # Concatenate to form exactly wake_word_frame_size samples
                    combined = np.concatenate(frames_to_combine) if len(frames_to_combine) > 1 else frames_to_combine[0]
                    wake_word_frame_complete = combined[:self._wake_word_frame_size]
                    
                    # Keep any remaining samples for next time
                    remainder = combined[self._wake_word_frame_size:]
                    if remainder.shape[0] > 0:
                        self._wake_word_buffer = [remainder] + self._wake_word_buffer[len(frames_to_combine):]
                    else:
                        self._wake_word_buffer = self._wake_word_buffer[len(frames_to_combine):]
                    
                    # Call frame callbacks with complete wake word frame
                    for callback in self._frame_callbacks:
                        try:
                            callback(wake_word_frame_complete, vad_rate)
                        except Exception as e:
                            logger.error("frame_callback_error", error=str(e))
                    
                    total_samples = sum(frame.shape[0] for frame in self._wake_word_buffer)
            
            # VAD processing (only if listening)
            if self.state == PipelineState.LISTENING:
                try:
                    vad_frame = audio_frame
                    if native_rate == 48000:
                        vad_frame = audio_frame[::3]  # Fast decimation
                    elif native_rate != vad_rate:
                        # Fallback resample
                        try:
                            from scipy import signal
                            num_samples = int(len(audio_frame) * vad_rate / native_rate)
                            vad_frame = signal.resample(audio_frame.astype(np.float64), num_samples).astype(np.int16)
                        except ImportError:
                            vad_frame = audio_frame
                    else:
                        vad_frame = audio_frame
                    
                    segment = self.segmenter.process_frame(vad_frame)
                    if segment:
                        self._stats.speech_segments_detected += 1
                        self._on_speech_segment(segment)
                except Exception as e:
                    logger.error("vad_processing_error", error=str(e))
    
    def _audio_input_callback(self, indata, frames, time_info, status):
        """
        Fast audio callback - producer only.
        
        Returns immediately after queueing bytes.
        """
        if status:
            logger.warning("audio_input_status", status=str(status))
        
        # Fast copy to bytes
        audio_bytes = indata.tobytes()
        
        if self._audio_queue:
            try:
                self._audio_queue.put_nowait(audio_bytes)
            except queue.Full:
                # Queue is full — evict the oldest frame so the pipeline
                # always receives the freshest audio.  This is preferable
                # to silently dropping the incoming frame, which would bias
                # the VAD toward stale data.
                try:
                    self._audio_queue.get_nowait()
                except queue.Empty:
                    pass
                try:
                    self._audio_queue.put_nowait(audio_bytes)
                except queue.Full:
                    pass  # Extremely rare race; accept the loss
                self._stats.queue_overflow_count += 1
                if self._stats.queue_overflow_count % 50 == 1:
                    # Log only on first occurrence and every 50th to avoid flooding
                    logger.warning(
                        "audio_queue_overflow_evicting_oldest",
                        total_overflows=self._stats.queue_overflow_count,
                    )
    
    def _on_speech_segment(self, segment: SpeechSegment):
        """
        Handle detected speech segment.
        """
        logger.info(
            "speech_segment_ready",
            duration_ms=segment.duration_ms,
            confidence=segment.confidence
        )
        
        for callback in self._speech_segment_callbacks[:]:
            try:
                callback(segment)
            except Exception as e:
                logger.error("speech_segment_callback_error", error=str(e))
    
    def start_playback(self) -> bool:
        """
        Start audio playback to output device.
        """
        if not SOUNDDEVICE_AVAILABLE:
            logger.error("sounddevice_not_available")
            return False
        
        if self._output_device is None:
            logger.error("no_output_device_initialized")
            return False
        
        try:
            # Use device's native sample rate
            device_info = sd.query_devices(self._output_device.index)
            native_rate = int(device_info.get('default_samplerate', self.audio_config.sample_rate))
            
            self._output_rate = native_rate
            self._output_stream = sd.OutputStream(
                device=self._output_device.index,
                channels=1,
                samplerate=native_rate,
                blocksize=1024,
                dtype=np.int16,
                callback=self._audio_output_callback
            )
            
            self._output_stream.start()
            logger.info("audio_playback_started", device=self._output_device.name, rate=native_rate)
            return True
            
        except Exception as e:
            logger.error("audio_playback_start_failed", error=str(e))
            return False
    
    def stop_playback(self):
        """Stop audio playback."""
        if self._output_stream:
            try:
                self._output_stream.stop()
                self._output_stream.close()
                logger.info("audio_playback_stopped")
            except Exception as e:
                logger.error("audio_playback_stop_error", error=str(e))
            finally:
                self._output_stream = None
    
    def _audio_output_callback(self, outdata, frames, time_info, status):
        """
        Output stream callback.
        """
        if status:
            logger.warning("audio_output_status", status=str(status))

        try:
            frame = self._output_queue.get_nowait()
            if len(frame) >= frames:
                outdata[:, 0] = frame[:frames]
            else:
                outdata[:len(frame), 0] = frame
                outdata[len(frame):, 0] = 0
        except queue.Empty:
            outdata.fill(0)
    
    def play_audio(self, audio_data: np.ndarray, sample_rate: int = None) -> bool:
        """
        Queue audio data for playback (resamples if needed).
        """
        if self.state == PipelineState.ERROR:
            logger.error("cannot_play_audio_in_error_state")
            return False
        
        target_rate = getattr(self, '_output_rate', 48000)
        if sample_rate and sample_rate != target_rate:
            try:
                from scipy import signal
                num_samples = int(len(audio_data) * target_rate / sample_rate)
                audio_data = signal.resample(audio_data.astype(np.float64), num_samples).astype(np.int16)
            except ImportError:
                if sample_rate % target_rate == 0:
                    ratio = sample_rate // target_rate
                    audio_data = audio_data[::ratio]
        
        # Split into frames
        frame_size = 1024
        frames = [audio_data[i:i+frame_size] for i in range(0, len(audio_data), frame_size)]
        
        queued = 0
        for frame in frames:
            try:
                self._output_queue.put_nowait(frame)
                queued += 1
            except queue.Full:
                break  # Queue full — drop remaining frames rather than blocking
        
        if queued > 0:
            self._set_state(PipelineState.SPEAKING)
            self._is_speaking = True
            self._stats.tts_utterances_played += 1
            logger.info("audio_queued_for_playback", frames=queued)
            return True
        return False
    
    def _drain_output_queue(self):
        """Discard all pending frames in the output queue."""
        while True:
            try:
                self._output_queue.get_nowait()
            except queue.Empty:
                break

    def stop_playback_immediate(self):
        """Stop playback immediately for barge-in."""
        with self._barge_in_lock:
            if self._is_speaking:
                logger.info("barge_in_triggered")
                self._drain_output_queue()
                self._is_speaking = False
                self._stats.barge_in_count += 1
                self._set_state(PipelineState.LISTENING)
    
    def enable_barge_in(self, enabled: bool = True):
        """Enable/disable barge-in."""
        self._barge_in_enabled = enabled
        logger.info("barge_in_enabled", enabled=enabled)
    
    @capture_errors("audio_pipeline", severity=BugSeverity.CRITICAL)
    def start(self) -> bool:
        """
        Start full pipeline.
        """
        logger.info("starting_audio_pipeline")
        
        if not self.initialize_devices():
            self._set_state(PipelineState.ERROR)
            return False
        
        if not self.start_playback():
            logger.warning("playback_start_failed_but_continuing")
        
        if not self.start_capture():
            self._set_state(PipelineState.ERROR)
            return False
        
        logger.info("audio_pipeline_started_successfully")
        return True
    
    def stop(self):
        """Stop full pipeline."""
        logger.info("stopping_audio_pipeline")
        self.stop_capture()
        self.stop_playback()
        self._set_state(PipelineState.IDLE)
        logger.info("audio_pipeline_stopped")
    
    def __enter__(self):
        self.start()
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        self.stop()
        return False
