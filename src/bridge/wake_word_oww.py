"""OpenWakeWord-based wake word detector."""
import time
import numpy as np
import structlog
from openwakeword import Model

from bridge.errorcapture import ErrorCapture
from bridge.known_issues import get_known_issues
from bridge.bug_tracker import BugSeverity

logger = structlog.get_logger()

# Available pre-trained models in openwakeword
AVAILABLE_MODELS = [
    "hey_jarvis",
    "alexa", 
    "hey_mycroft",
    "ok_nabu",
    "hey_rhasspy",
]

# OpenWakeWord requires 1280 samples (80ms at 16kHz) per frame
FRAME_SIZE = 1280


class OpenWakeWordDetector:
    """
    Fast wake word detector using OpenWakeWord.
    
    OpenWakeWord is a lightweight neural network that detects wake words
    in real-time (~5-10ms per frame). This is much faster than using
    STT for wake word detection.
    
    OpenWakeWord expects audio as float32 normalized to [-1.0, 1.0].
    This class handles conversion from various input formats (int16, float32, float64).
    """
    
    def __init__(self, model_name: str = "hey_mycroft", threshold: float = 0.15, refractory_seconds: float = 2.0):
        """
        Initialize OpenWakeWord detector.
        
        Args:
            model_name: Name of wake word model (hey_jarvis, alexa, hey_mycroft, ok_nabu)
            threshold: Detection threshold (0-1, default 0.5)
            refractory_seconds: Seconds to wait after detection before detecting again
        """
        self.model_name = model_name
        self.threshold = threshold
        self.refractory_seconds = refractory_seconds
        self._last_detection_time = 0.0
        self.model = None
        # Internal buffer stores float32 normalized to [-1.0, 1.0] as required by OpenWakeWord
        self._audio_buffer = np.array([], dtype=np.float32)
        self._model_key = None  # Cached model key with version suffix
        
        # Error capture for automatic bug tracking
        self.error_capture = ErrorCapture(component="wake_word", severity=BugSeverity.HIGH)
        
        # Track recent scores for known issue detection (max 100)
        self._recent_scores: list[float] = []
        
        try:
            # OpenWakeWord Model loads all pre-trained models by default
            self.model = Model()
            logger.info(
                "OpenWakeWordDetector_initialized",
                target_model=model_name,
                threshold=threshold,
                available_models=list(AVAILABLE_MODELS)
            )
        except Exception as e:
            logger.error("OpenWakeWordDetector_init_failed", error=str(e), exc_info=True)
            self.model = None
    
    def _normalize_audio(self, audio_frame: np.ndarray) -> np.ndarray:
        """
        Normalize audio to float32 in range [-1.0, 1.0] for OpenWakeWord.
        
        OpenWakeWord expects float32 audio normalized to [-1.0, 1.0].
        This method handles various input formats correctly.
        
        Args:
            audio_frame: Input audio (int16, float32, or float64)
            
        Returns:
            Normalized float32 audio in [-1.0, 1.0] range
            
        Raises:
            ValueError: If audio dtype is not supported or if float input has invalid range
        """
        dtype = audio_frame.dtype
        
        # Debug logging for audio stats
        logger.debug(
            "Audio frame stats",
            dtype=str(dtype),
            shape=audio_frame.shape,
            min=float(np.min(audio_frame)),
            max=float(np.max(audio_frame)),
            mean=float(np.mean(audio_frame)),
        )
        
        if dtype == np.int16:
            # int16: range [-32768, 32767] -> normalize to [-1.0, 1.0]
            # First remove DC offset (center around 0)
            dc_offset = float(np.mean(audio_frame))
            centered = audio_frame.astype(np.float32) - dc_offset
            # Then normalize to [-1.0, 1.0]
            normalized = centered / 32768.0
            
            # Debug log if significant DC offset was removed
            if abs(dc_offset) > 500:  # Significant if > 500 in int16 scale
                logger.debug(
                    "dc_offset_removed",
                    offset=int(dc_offset),
                    original_mean=float(np.mean(audio_frame)),
                    corrected_mean=float(np.mean(normalized)),
                )
            
            return normalized
        
        elif dtype == np.float32:
            # float32: should already be in [-1.0, 1.0]
            # Remove DC offset
            dc_offset = float(np.mean(audio_frame))
            centered = audio_frame - dc_offset
            # Check if values exceed 1.0 (overscaled)
            max_val = float(np.max(np.abs(centered)))
            if max_val > 1.0:
                # Audio is overscaled, normalize it
                logger.warning(
                    "Audio_overscaled",
                    max_value=max_val,
                    dc_offset=dc_offset,
                    action="normalizing"
                )
                return centered / max_val
            return centered
        
        elif dtype == np.float64:
            # float64: convert to float32
            # Remove DC offset
            dc_offset = float(np.mean(audio_frame))
            centered = audio_frame - dc_offset
            max_val = float(np.max(np.abs(centered)))
            if max_val > 1.0:
                # Audio is overscaled, normalize it
                logger.warning(
                    "Audio_overscaled",
                    max_value=max_val,
                    dc_offset=dc_offset,
                    action="normalizing"
                )
                return (centered / max_val).astype(np.float32)
            return centered.astype(np.float32)
        
        else:
            # Try to convert to float32 - this handles int32, uint8, etc.
            logger.warning(
                "Unexpected_audio_dtype",
                dtype=str(dtype),
                action="attempting_conversion"
            )
            # For integer types, normalize by their max value
            if np.issubdtype(dtype, np.integer):
                max_val = float(np.iinfo(dtype).max)
                return audio_frame.astype(np.float32) / max_val
            # For float types, just convert
            return audio_frame.astype(np.float32)
    
    def process_frame(self, audio_frame: np.ndarray, sample_rate: int = 16000) -> bool:
        """
        Process a single audio frame for wake word detection.
        
        This runs in ~5-10ms per frame - safe for real-time processing.
        
        OpenWakeWord expects float32 audio normalized to [-1.0, 1.0].
        This method handles various input formats and performs the necessary
        conversion. Supported input formats:
        - int16: Raw PCM audio from most capture sources
        - float32: Pre-normalized audio (values should be in [-1.0, 1.0])
        - float64: Higher precision audio (converted to float32)
        
        Args:
            audio_frame: Audio samples at the specified sample_rate.
                        Supported dtypes: int16, float32, float64
            sample_rate: Sample rate of input audio (default 16kHz)
            
        Returns:
            True if wake word was detected, False otherwise
        """
        if self.model is None:
            logger.warning("oww_model_not_loaded")
            return False
        
        # Refractory check - don't detect too frequently
        current_time = time.time()
        if current_time - self._last_detection_time < self.refractory_seconds:
            logger.debug("oww_refractory", elapsed=current_time - self._last_detection_time)
            return False
        
        # Initialize frame counter if needed
        if not hasattr(self, '_frame_count'):
            self._frame_count = 0
        self._frame_count += 1
        
        # DEBUG: Log buffer state on every frame
        logger.debug("oww_frame_start", frame=self._frame_count, buffer_len=len(self._audio_buffer))
        
        # Use error capture context to automatically capture any processing errors
        with self.error_capture.context(context=f"frame={self._frame_count}"):
            # Resample to 16kHz if needed
            if sample_rate != 16000:
                import scipy.signal
                num_samples = int(len(audio_frame) * 16000 / sample_rate)
                # Resample in float64 for accuracy, then normalize
                audio_frame = scipy.signal.resample(audio_frame.astype(np.float64), num_samples)
            
            # Normalize audio to float32 [-1.0, 1.0] for OpenWakeWord
            normalized_audio = self._normalize_audio(audio_frame)
            
            # Accumulate audio frames until we have FRAME_SIZE samples (1280 = 80ms at 16kHz)
            self._audio_buffer = np.concatenate((self._audio_buffer, normalized_audio))
            
            # DEBUG: Log buffer accumulation
            logger.debug("oww_buffer", buffer_len=len(self._audio_buffer), needed=FRAME_SIZE)
            
            # Only process when we have enough samples
            if len(self._audio_buffer) < FRAME_SIZE:
                return False
            
            logger.info("oww_processing_frame", frame=self._frame_count, buffer_len=len(self._audio_buffer))
            
            # Take exactly FRAME_SIZE samples and keep any remainder for next time
            frame_to_process = self._audio_buffer[:FRAME_SIZE].astype(np.float32)
            self._audio_buffer = self._audio_buffer[FRAME_SIZE:]
            
            # Discover model key on first prediction if not yet cached
            if self._model_key is None:
                # Run a test prediction to discover available keys
                test_prediction = self.model.predict(frame_to_process)
                available_keys = list(test_prediction.keys())
                
                # Find the key that matches our model name with version suffix
                # Model keys look like: "hey_jarvis_v0.1", "alexa_v0.1", etc.
                matched_keys = [k for k in available_keys if self.model_name in k]
                
                if matched_keys:
                    self._model_key = matched_keys[0]
                    logger.info(
                        "oww_model_key_discovered",
                        target_model=self.model_name,
                        discovered_key=self._model_key,
                        available_keys=available_keys
                    )
                else:
                    logger.warning(
                        "oww_model_key_not_found",
                        target_model=self.model_name,
                        available_keys=available_keys
                    )
                    return False
            
            # Run prediction with the buffered frame
            prediction = self.model.predict(frame_to_process)
            
            # Get score for our target model using the discovered key
            score = float(prediction.get(self._model_key, 0.0))
            
            # DEBUG: Log normalized audio stats for model input
            if self._frame_count <= 5 or self._frame_count % 20 == 0:
                logger.info(
                    "oww_audio_input",
                    frame=self._frame_count,
                    buffer_min=float(np.min(frame_to_process)),
                    buffer_max=float(np.max(frame_to_process)),
                    buffer_mean=float(np.mean(frame_to_process)),
                    buffer_std=float(np.std(frame_to_process)),
                )
            
            # DEBUG: Always log scores for troubleshooting (every 10 frames)
            if self._frame_count % 10 == 0:
                logger.info(
                    "oww_score",
                    frame=self._frame_count,
                    model_key=self._model_key,
                    score=round(score, 6),
                    threshold=self.threshold,
                    peak_score=round(float(max(prediction.values())), 6) if hasattr(prediction, 'values') else 0,
                )
            
            # Track maximum score seen (for debugging)
            if not hasattr(self, '_max_score'):
                self._max_score = 0.0
            if score > self._max_score:
                self._max_score = score
                logger.info(
                    "oww_score_peak",
                    frame=self._frame_count,
                    new_max=round(score, 6),
                    threshold=self.threshold,
                )
            
            # Run prediction with the buffered frame
            prediction = self.model.predict(frame_to_process)
            
            # Get score for our target model using the discovered key
            score = prediction.get(self._model_key, 0.0)
            
            # DEBUG: Always log first 20 scores for troubleshooting
            if self._frame_count <= 20:
                logger.info(
                    "oww_score_debug",
                    frame=self._frame_count,
                    model_key=self._model_key,
                    score=round(score, 6),
                    threshold=self.threshold,
                    all_scores=dict(prediction) if hasattr(prediction, 'items') else str(prediction),
                )
            
            # Track recent scores for known issue detection (max 100)
            self._recent_scores.append(score)
            if len(self._recent_scores) > 100:
                self._recent_scores.pop(0)
            
            # Check for zero-score known issue
            if len(self._recent_scores) == 100:
                avg_score = sum(self._recent_scores) / len(self._recent_scores)
                if avg_score == 0.0:
                    get_known_issues().detect_and_capture(
                        "wake_word_zero_scores",
                        context={
                            "avg_score": avg_score,
                            "frame_count": len(self._recent_scores),
                            "dtype": str(frame_to_process.dtype),
                        },
                        session_id=None,
                    )
            
            # Log score periodically (every 50 frames) or when score is notable
            if self._frame_count % 50 == 0 or score > 0.1:
                logger.info(
                    "oww_score",
                    model=self._model_key,
                    score=round(score, 4),
                    frame=self._frame_count,
                    threshold=self.threshold
                )
            
            if score >= self.threshold:
                self._last_detection_time = current_time
                logger.info(
                    "wake_word_detected",
                    model=self._model_key,
                    score=round(score, 3),
                    threshold=self.threshold
                )
                # Reset model buffer to prevent re-triggering on same audio
                self.model.reset()
                return True
            
            return False
    
    def is_available(self) -> bool:
        """Check if detector is available."""
        return self.model is not None
    
    def get_available_models(self) -> list:
        """Get list of available wake word models."""
        return list(AVAILABLE_MODELS)
    
    def reset(self):
        """Reset the prediction buffer."""
        if self.model:
            self.model.reset()
            self._last_detection_time = 0.0
            self._audio_buffer = np.array([], dtype=np.float32)
            self._recent_scores.clear()
            logger.debug("OpenWakeWord_reset")
