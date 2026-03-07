"""
Wake Word Detector for Voice Bridge.

Detects wake phrases using OpenWakeWord (fast) or STT-based detection (legacy).
"""
import threading
import time
from typing import Callable, Optional

import structlog
import numpy as np

from bridge.config import get_config, WakeWordConfig

logger = structlog.get_logger()


class WakeWordDetector:
    """
    Detects wake words in continuous audio stream.
    
    Uses OpenWakeWord for fast wake word detection (~5ms per frame),
    or legacy STT-based detection for backward compatibility.
    """
    
    def __init__(
        self,
        config=None,
        stt_engine=None,
        vad_config=None,
        openwakeword_model: str = "hey_mycroft"
    ):
        """
        Initialize wake word detector.
        
        Args:
            config: App configuration (loads from defaults if None)
            stt_engine: STT engine (only used for legacy STT backend)
            vad_config: VAD configuration (only used for legacy STT backend)
            openwakeword_model: Model name for OpenWakeWord backend
        """
        self.config = config or get_config()
        self._wake_word_config = self.config.wake_word
        
        # Wake word settings
        self._wake_word = self._wake_word_config.wake_word.lower().strip()
        self._backend = self._wake_word_config.backend
        
        # Initialize OpenWakeWord detector (fast, ~5ms)
        self._oww_detector = None
        if self._backend == "openwakeword":
            try:
                from bridge.wake_word_oww import OpenWakeWordDetector
                self._oww_detector = OpenWakeWordDetector(
                    model_name=self._wake_word_config.openwakeword_model,
                    threshold=self._wake_word_config.openwakeword_threshold,
                    refractory_seconds=self._wake_word_config.refractory_seconds
                )
                if self._oww_detector.is_available():
                    logger.info(
                        "openwakeword_initialized",
                        model=self._wake_word_config.openwakeword_model,
                        threshold=self._wake_word_config.openwakeword_threshold
                    )
                else:
                    logger.error("openwakeword_not_available_falling_back_to_stt")
                    self._backend = "stt"
            except Exception as e:
                logger.error("openwakeword_init_failed", error=str(e), exc_info=True)
                self._backend = "stt"  # Fallback to STT
        
        # Legacy STT backend setup
        self.stt_engine = None
        self._vad = None
        if self._backend == "stt":
            from bridge.stt import STTEngine, STTConfig
            from bridge.vad import WebRTCVAD, VADConfig
            if stt_engine:
                self.stt_engine = stt_engine
            else:
                stt_config = getattr(self.config, 'stt', None) or STTConfig()
                self.stt_engine = STTEngine(stt_config)
            self._vad = WebRTCVAD(vad_config or VADConfig())
        
        # Audio buffer for legacy STT backend
        self._buffered_frames: list = []
        self._buffer_lock = threading.Lock()
        
        # State
        self._running = False
        self._state_lock = threading.Lock()
        
        # Callbacks
        self._on_detected: Optional[Callable[[str], None]] = None
        self._on_wake_word_ack: Optional[Callable[[str], None]] = None
        
        # Timing (for legacy STT backend)
        self._last_detection_time = 0.0
        
        logger.info(
            "wake_word_detector_initialized",
            wake_word=self._wake_word,
            backend=self._backend
        )
    
    @property
    def is_running(self) -> bool:
        """Check if detector is running."""
        with self._state_lock:
            return self._running
    
    @property
    def wake_word(self) -> str:
        """Get current wake word."""
        return self._wake_word
    
    @wake_word.setter
    def wake_word(self, word: str):
        """Update wake word (case-insensitive)."""
        self._wake_word = word.lower().strip()
        logger.info("wake_word_updated", wake_word=self._wake_word)
    
    def start(self):
        """Start wake word detection."""
        with self._state_lock:
            if self._running:
                logger.warning("wake_word_detector_already_running")
                return
            self._running = True
            logger.info("wake_word_detector_started", backend=self._backend)
    
    def stop(self):
        """Stop wake word detection."""
        with self._state_lock:
            if not self._running:
                return
            self._running = False
            if self._buffered_frames:
                self._buffered_frames.clear()
            logger.info("wake_word_detector_stopped")
    
    def register_on_detected(self, callback: Callable[[str], None]):
        """Register callback for wake word detection."""
        self._on_detected = callback
        logger.info("on_detected_callback_registered")
    
    def register_on_wake_word_ack(self, callback: Callable[[str], None]):
        """Register callback for wake word acknowledgement."""
        self._on_wake_word_ack = callback
        logger.info("on_wake_word_ack_callback_registered")
    
    def process_frame(self, audio_frame: np.ndarray, sample_rate: int = 16000) -> bool:
        """
        Process a single audio frame for wake word detection.
        
        Args:
            audio_frame: Audio samples at any sample rate
            sample_rate: Sample rate of input audio (default 16kHz)
            
        Returns:
            True if wake word was detected
        """
        if not self._running:
            return False
        
        # Use OpenWakeWord for fast detection (~5ms)
        if self._backend == "openwakeword" and self._oww_detector:
            return self._process_oww(audio_frame, sample_rate)
        
        # Legacy: Use STT for wake word detection (slow, not recommended)
        return self._process_stt(audio_frame, sample_rate)
    
    def _process_oww(self, audio_frame: np.ndarray, sample_rate: int) -> bool:
        """Process frame using OpenWakeWord (fast, ~5ms)."""
        try:
            # Convert sample rate if needed (OpenWakeWord expects 16kHz)
            if sample_rate != 16000:
                import scipy.signal
                num_samples = int(len(audio_frame) * 16000 / sample_rate)
                audio_frame = scipy.signal.resample(audio_frame.astype(np.float64), num_samples).astype(np.int16)
            
            # OpenWakeWord process_frame returns True if wake word detected
            detected = self._oww_detector.process_frame(audio_frame)
            
            if detected:
                logger.info("wake_word_detected_oww", model=self._wake_word_config.openwakeword_model)
                
                # Call callbacks
                if self._on_wake_word_ack:
                    try:
                        self._on_wake_word_ack(self._wake_word)
                    except Exception as e:
                        logger.error("wake_word_ack_callback_error", error=str(e))
                
                if self._on_detected:
                    try:
                        self._on_detected(self._wake_word)
                    except Exception as e:
                        logger.error("on_detected_callback_error", error=str(e))
            
            return detected
            
        except Exception as e:
            logger.error("oww_process_error", error=str(e))
            return False
    
    def _process_stt(self, audio_frame: np.ndarray, sample_rate: int) -> bool:
        """Process frame using STT-based detection (reliable, any wake word)."""
        # Resample to 16kHz if needed
        if sample_rate != 16000:
            import scipy.signal
            num_samples = int(len(audio_frame) * 16000 / sample_rate)
            audio_frame = scipy.signal.resample(audio_frame.astype(np.float64), num_samples).astype(np.int16)
        
        # Check VAD for speech
        is_speech = self._vad.process_frame(audio_frame)
        
        # Buffer frame
        with self._buffer_lock:
            self._buffered_frames.append(audio_frame)
            
            # Get buffered audio duration (at 16kHz, each frame is ~30ms for 480 samples)
            buffered_samples = sum(len(f) for f in self._buffered_frames)
            buffered_duration = buffered_samples / 16000.0
        
        # If speech ongoing and buffer is growing, check periodically
        if is_speech and buffered_duration > 0.8:  # Lowered from 1.5s for faster detection
            # We have ~1.5 seconds of speech, try early transcription
            # This catches wake word even if user keeps speaking
            audio_data = np.concatenate(self._buffered_frames)
            detected = self._check_wake_word_stt(audio_data)
            
            if detected:
                with self._buffer_lock:
                    self._buffered_frames.clear()
                
                if self._on_wake_word_ack:
                    try:
                        self._on_wake_word_ack(detected)
                    except Exception as e:
                        logger.error("wake_word_ack_callback_error", error=str(e))
                
                if self._on_detected:
                    try:
                        self._on_detected(detected)
                    except Exception as e:
                        logger.error("on_detected_callback_error", error=str(e))
                
                return True
        
        if not is_speech:
            # Silence detected - check if we have enough speech buffered
            # Need at least 0.5 seconds for Whisper to transcribe meaningfully
            with self._buffer_lock:
                buffered_samples = sum(len(f) for f in self._buffered_frames)
                buffered_duration = buffered_samples / 16000.0
                
                if buffered_duration >= 0.5:
                    audio_data = np.concatenate(self._buffered_frames)
                    self._buffered_frames.clear()
                else:
                    # Not enough audio, just clear buffer
                    self._buffered_frames.clear()
                    return False
            
            logger.debug(
                "wake_word_transcribing_buffer",
                duration=buffered_duration
            )
            
            # Transcribe and check for wake word
            detected = self._check_wake_word_stt(audio_data)
            
            if detected and self._on_detected:
                try:
                    self._on_detected(detected)
                except Exception as e:
                    logger.error("on_detected_callback_error", error=str(e))
            
            return detected is not None
        
        return False
    
    def _check_wake_word_stt(self, audio_data: np.ndarray) -> Optional[str]:
        """Transcribe audio and check for wake word."""
        try:
            result = self.stt_engine.transcribe(audio_data)
            
            if isinstance(result, tuple):
                text, confidence = result
            else:
                text = result
                confidence = 1.0
            
            if not text:
                logger.debug("stt_wake_word_empty_transcript")
                return None
            
            text_lower = text.lower().strip()
            logger.debug("stt_wake_word_transcript", text=text_lower, confidence=round(confidence, 2))
            
            # Check if wake word appears in transcription
            # Support both "hey mycroft" and just "mycroft"
            wake_words = [self._wake_word]
            if self._wake_word.startswith("hey "):
                # Also match just the name (e.g., "mycroft" for "hey mycroft")
                wake_words.append(self._wake_word[4:])  # Remove "hey "
            
            for wake_word in wake_words:
                if wake_word in text_lower:
                    current_time = time.time()
                    if current_time - self._last_detection_time > self._wake_word_config.refractory_seconds:
                        self._last_detection_time = current_time
                        logger.info("wake_word_detected_stt", text=text, confidence=confidence, wake_word=wake_word)
                        return text
            
            return None
            
        except Exception as e:
            logger.error("stt_check_error", error=str(e))
            return None
    
    def reset(self):
        """Reset detector state."""
        with self._state_lock:
            if self._buffered_frames:
                self._buffered_frames.clear()
            self._last_detection_time = 0.0
            logger.info("wake_word_detector_reset")