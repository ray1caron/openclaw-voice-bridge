"""
Voice Activity Detection (VAD) for audio pipeline.

Supports WebRTC VAD for lightweight, real-time speech detection.
Detects speech start/end events and filters non-speech audio.
"""
import enum
import threading
from dataclasses import dataclass
from typing import Callable, Optional, List

import structlog
import numpy as np

logger = structlog.get_logger()

# Try to import webrtcvad, fallback to mock if unavailable
try:
    import webrtcvad
    WEBRTC_AVAILABLE = True
except ImportError:
    WEBRTC_AVAILABLE = False
    logger.warning("webrtcvad not available, using mock VAD")


class VADMode(enum.Enum):
    """VAD aggressiveness modes."""
    NORMAL = 0      # Least aggressive, more false positives
    LOW = 1         # Low aggressiveness
    MEDIUM = 2      # Medium aggressiveness (default)
    HIGH = 3        # Most aggressive, fewer false positives


class VADState(enum.Enum):
    """Current VAD state."""
    SILENCE = "silence"
    SPEECH = "speech"
    UNKNOWN = "unknown"


@dataclass
class VADConfig:
    """VAD configuration."""
    mode: VADMode = VADMode.MEDIUM
    frame_duration_ms: int = 30  # 10, 20, or 30ms for WebRTC
    sample_rate: int = 16000
    min_speech_duration_ms: int = 250
    min_silence_duration_ms: int = 500
    padding_duration_ms: int = 300  # Padding around speech


@dataclass
class SpeechSegment:
    """Represents a detected speech segment."""
    start_time: float
    end_time: float
    audio_data: np.ndarray
    confidence: float = 1.0
    
    @property
    def duration_ms(self) -> float:
        """Get segment duration in milliseconds."""
        return (self.end_time - self.start_time) * 1000


class WebRTCVAD:
    """
    WebRTC-based Voice Activity Detection.
    
    Lightweight, real-time VAD suitable for continuous audio streaming.
    """
    
    def __init__(self, config: Optional[VADConfig] = None):
        """
        Initialize WebRTC VAD.
        
        Args:
            config: VAD configuration (uses defaults if None)
        """
        self.config = config or VADConfig()
        self._vad = None
        self._state = VADState.UNKNOWN
        self._state_lock = threading.Lock()
        
        if WEBRTC_AVAILABLE:
            self._init_webrtc()
        else:
            logger.warning("using_mock_vad")
    
    def _init_webrtc(self):
        """Initialize WebRTC VAD instance."""
        try:
            self._vad = webrtcvad.Vad(self.config.mode.value)
            logger.info(
                "webrtc_vad_initialized",
                mode=self.config.mode.name,
                aggressiveness=self.config.mode.value
            )
        except Exception as e:
            logger.error("webrtc_vad_init_failed", error=str(e))
            self._vad = None
    
    @property
    def is_available(self) -> bool:
        """Check if VAD is available and initialized."""
        return self._vad is not None or not WEBRTC_AVAILABLE
    
    @property
    def state(self) -> VADState:
        """Get current VAD state."""
        with self._state_lock:
            return self._state
    
    def process_frame(self, audio_frame: np.ndarray) -> bool:
        """
        Process a single audio frame for voice activity.
        
        Args:
            audio_frame: Audio samples as numpy array (int16)
            
        Returns:
            True if speech detected, False otherwise
        """
        if not WEBRTC_AVAILABLE or self._vad is None:
            # Mock VAD: always return True (assume speech)
            return True
        
        # Validate frame
        if len(audio_frame) == 0:
            return False
        
        # Calculate expected frame size
        expected_samples = int(
            self.config.sample_rate * self.config.frame_duration_ms / 1000
        )
        
        # If frame size matches, process directly
        if len(audio_frame) == expected_samples:
            return self._process_single_frame(audio_frame)
        
        # If frame is larger, split into proper chunks and check each
        if len(audio_frame) > expected_samples:
            return self._process_oversized_frame(audio_frame, expected_samples)
        
        # If frame is smaller, we need to buffer (for now, treat as silence)
        logger.warning(
            "frame_too_small",
            expected=expected_samples,
            actual=len(audio_frame)
        )
        return False
    
    def _process_single_frame(self, audio_frame: np.ndarray) -> bool:
        """Process a single properly-sized frame."""
        try:
            audio_bytes = audio_frame.astype(np.int16).tobytes()
            is_speech = self._vad.is_speech(audio_bytes, self.config.sample_rate)
            
            with self._state_lock:
                self._state = VADState.SPEECH if is_speech else VADState.SILENCE
            
            return is_speech
        except Exception as e:
            logger.error("vad_processing_failed", error=str(e))
            return False
    
    def _process_oversized_frame(self, audio_frame: np.ndarray, frame_size: int) -> bool:
        """
        Process an oversized frame by splitting into proper chunks.
        
        Returns True if any chunk contains speech.
        """
        num_frames = len(audio_frame) // frame_size
        speech_detected = False
        
        for i in range(num_frames):
            start = i * frame_size
            end = start + frame_size
            chunk = audio_frame[start:end]
            
            # Process each chunk
            if self._process_single_frame(chunk):
                speech_detected = True
                # If speech detected, we can stop early
                # (unless we want to track all state changes)
        
        # Handle remaining samples (not enough for a full frame)
        remaining = len(audio_frame) % frame_size
        if remaining > 0:
            # For remaining samples, we assume same state as last chunk
            pass
        
        return speech_detected
    
    def process_stream(
        self,
        audio_stream,
        callback: Optional[Callable[[bool, np.ndarray], None]] = None
    ):
        """
        Process a continuous audio stream.
        
        Args:
            audio_stream: Iterator yielding audio frames
            callback: Optional callback(is_speech, frame) for each frame
        """
        for frame in audio_stream:
            is_speech = self.process_frame(frame)
            if callback:
                callback(is_speech, frame)


class SpeechSegmenter:
    """
    Detects and extracts speech segments from continuous audio.
    
    Uses VAD to identify speech boundaries and returns complete
    speech segments with configurable padding.
    """
    
    def __init__(
        self,
        vad: Optional[WebRTCVAD] = None,
        config: Optional[VADConfig] = None
    ):
        """
        Initialize speech segmenter.
        
        Args:
            vad: VAD instance (creates default if None)
            config: VAD configuration
        """
        self.vad = vad or WebRTCVAD(config)
        self.config = config or self.vad.config
        
        # State tracking
        self._in_speech = False
        self._speech_start_time: Optional[float] = None
        self._silence_start_time: Optional[float] = None
        self._buffered_frames: List[np.ndarray] = []
        self._padding_frames: List[np.ndarray] = []
        
        # Timing
        self._frame_duration_sec = self.config.frame_duration_ms / 1000
        self._min_speech_sec = self.config.min_speech_duration_ms / 1000
        self._min_silence_sec = self.config.min_silence_duration_ms / 1000
        self._padding_frames_count = int(
            self.config.padding_duration_ms / self.config.frame_duration_ms
        )
    
    def reset(self):
        """Reset segmenter state."""
        self._in_speech = False
        self._speech_start_time = None
        self._silence_start_time = None
        self._buffered_frames = []
        self._padding_frames = []
    
    def process_frame(
        self,
        frame: np.ndarray,
        timestamp: Optional[float] = None
    ) -> Optional[SpeechSegment]:
        """
        Process a single frame and detect speech segments.
        
        Args:
            frame: Audio frame
            timestamp: Current timestamp (uses time.time() if None)
            
        Returns:
            SpeechSegment when speech ends, None otherwise
        """
        if timestamp is None:
            import time
            timestamp = time.time()
        
        is_speech = self.vad.process_frame(frame)
        
        # Store frame in padding buffer
        self._padding_frames.append(frame)
        if len(self._padding_frames) > self._padding_frames_count:
            self._padding_frames.pop(0)
        
        if is_speech:
            self._silence_start_time = None
            
            if not self._in_speech:
                # Speech start
                self._in_speech = True
                self._speech_start_time = timestamp
                # Include padding frames
                self._buffered_frames = self._padding_frames.copy()
                logger.debug("speech_started", timestamp=timestamp)
            
            self._buffered_frames.append(frame)
        else:
            if self._in_speech:
                if self._silence_start_time is None:
                    self._silence_start_time = timestamp
                
                silence_duration = timestamp - self._silence_start_time
                
                if silence_duration >= self._min_silence_sec:
                    # Speech end
                    speech_duration = self._silence_start_time - self._speech_start_time
                    
                    if speech_duration >= self._min_speech_sec:
                        # Valid speech segment
                        audio_data = np.concatenate(self._buffered_frames)
                        
                        segment = SpeechSegment(
                            start_time=self._speech_start_time,
                            end_time=self._silence_start_time,
                            audio_data=audio_data,
                            confidence=min(1.0, speech_duration / 1.0)  # Longer = more confident
                        )
                        
                        logger.info(
                            "speech_segment_detected",
                            duration_ms=segment.duration_ms,
                            confidence=segment.confidence
                        )
                        
                        self.reset()
                        return segment
                    else:
                        # Too short, discard
                        logger.debug("speech_too_short_discarded", duration=speech_duration)
                        self.reset()
                else:
                    # Still in speech, buffer the silence frames
                    self._buffered_frames.append(frame)
        
        return None
    
    def flush(self) -> Optional[SpeechSegment]:
        """
        Flush any remaining speech in buffer.
        
        Returns:
            SpeechSegment if speech was in progress, None otherwise
        """
        if self._in_speech and self._speech_start_time:
            import time
            end_time = time.time()
            speech_duration = end_time - self._speech_start_time
            
            if speech_duration >= self._min_speech_sec:
                audio_data = np.concatenate(self._buffered_frames)
                
                segment = SpeechSegment(
                    start_time=self._speech_start_time,
                    end_time=end_time,
                    audio_data=audio_data,
                    confidence=min(1.0, speech_duration / 1.0)
                )
                
                logger.info("speech_segment_flushed", duration_ms=segment.duration_ms)
                self.reset()
                return segment
        
        self.reset()
        return None


class MockVAD(WebRTCVAD):
    """Mock VAD for testing without webrtcvad dependency."""
    
    def __init__(self, config: Optional[VADConfig] = None):
        """Initialize mock VAD."""
        self.config = config or VADConfig()
        self._state = VADState.UNKNOWN
        self._frame_count = 0
    
    def _init_webrtc(self):
        """Skip WebRTC initialization."""
        pass
    
    @property
    def is_available(self) -> bool:
        """Mock is always available."""
        return True
    
    def process_frame(self, audio_frame: np.ndarray) -> bool:
        """
        Mock VAD: detect speech based on energy threshold.
        
        Simple energy-based detection for testing.
        """
        self._frame_count += 1
        
        # Calculate RMS energy
        if len(audio_frame) == 0:
            energy = 0
        else:
            energy = np.sqrt(np.mean(audio_frame.astype(np.float32) ** 2))
        
        # Threshold based on mode
        thresholds = {
            VADMode.NORMAL: 100,
            VADMode.LOW: 200,
            VADMode.MEDIUM: 300,
            VADMode.HIGH: 500
        }
        threshold = thresholds.get(self.config.mode, 300)
        
        is_speech = energy > threshold
        
        with threading.Lock():
            self._state = VADState.SPEECH if is_speech else VADState.SILENCE
        
        return is_speech
