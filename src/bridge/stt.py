"""
Speech-to-Text Engine using Faster-Whisper.

Provides asynchronous speech-to-text transcription
with model selection and device configuration.
"""
import asyncio
import threading
from dataclasses import dataclass
from typing import Optional, Callable, List

import structlog
import numpy as np

from bridge.errorcapture import ErrorCapture, capture_errors
from bridge.bug_tracker import BugSeverity

logger = structlog.get_logger()

# Try to import faster-whisper
try:
    from faster_whisper import WhisperModel
    FASTER_WHISPER_AVAILABLE = True
except ImportError:
    FASTER_WHISPER_AVAILABLE = False
    logger.warning("faster-whisper not available, using mock STT")


class STTState:
    """STT engine states."""
    IDLE = "idle"
    PROCESSING = "processing"
    ERROR = "error"


@dataclass
class STTConfig:
    """STT configuration."""
    model: str = "base"
    device: str = "auto"
    compute_type: str = "int8"
    language: str | None = None
    beam_size: int = 5
    vad_filter: bool = True
    vad_threshold: float = 0.5


class STTEngine:
    """
    Speech-to-Text engine using Faster-Whisper.

    Features:
    - Multiple Whisper model sizes supported
    - CPU/GPU acceleration
    - Async transcription interface
    - Audio buffer processing
    """

    # Supported Whisper model sizes
    SUPPORTED_MODELS = {
        "tiny": "tiny",
        "base": "base",
        "small": "small",
        "medium": "medium",
        "large": "large",
        "large-v2": "large-v2",
        "large-v3": "large-v3"
    }

    def __init__(
        self,
        config: Optional[STTConfig] = None
    ):
        """
        Initialize STT engine.

        Args:
            config: STT configuration
        """
        self.config = config or STTConfig()

        # Error capture
        self.error_capture = ErrorCapture(component="stt", severity=BugSeverity.MEDIUM)

        # Model instance
        self._model: Optional[WhisperModel] = None
        self._model_lock = threading.Lock()

        # State
        self._state = STTState.IDLE
        self._state_lock = threading.Lock()

        # Async loop for background transcription
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._loop_lock = threading.Lock()

        # Transcription queue
        self._queue: asyncio.Queue = asyncio.Queue()
        self._processing_task: Optional[asyncio.Task] = None

        #Callbacks
        self._on_transcription: Optional[Callable[[str], None]] = None

        logger.info(
            "stt_engine_initialized",
            model=self.config.model,
            device=self.config.device,
            language=self.config.language
        )

    @property
    def is_available(self) -> bool:
        """Check if Faster-Whisper is available."""
        return FASTER_WHISPER_AVAILABLE

    @property
    def state(self) -> STTState:
        """Get current STT state."""
        with self._state_lock:
            return self._state

    def set_on_transcription(self, callback: Callable[[str, float], None]):
        """Set transcription callback.
        
        Args:
            callback: Function called with (text, confidence) when transcription completes
        """
        self._on_transcription = callback

    def _set_state(self, new_state: STTState):
        """Set STT state."""
        with self._state_lock:
            self._state = new_state

    @capture_errors("stt", severity=BugSeverity.HIGH, reraise=False)
    def initialize(self) -> bool:
        """
        Load Whisper model.

        Returns:
            True if model loaded successfully
        """
        if not FASTER_WHISPER_AVAILABLE:
            logger.warning("stt_not_available_fallback")
            return False

        if self._model is not None:
            logger.info("stt_model_already_loaded")
            return True

        try:
            # Determine device
            device = self.config.device
            if device == "auto":
                device = "cuda" if self._cuda_available() else "cpu"

            # Determine compute type
            compute_type = self.config.compute_type
            if device == "cpu" and compute_type == "float16":
                compute_type = "int8"  # CPU doesn't support float16

            model_size = self.SUPPORTED_MODELS.get(self.config.model)
            if not model_size:
                logger.error(
                    "invalid_model_size",
                    model=self.config.model,
                    valid_models=list(self.SUPPORTED_MODELS.keys())
                )
                return False

            logger.info(
                "loading_stt_model",
                model=model_size,
                device=device,
                compute_type=compute_type
            )

            self._model = WhisperModel(
                model_size_or_path=model_size,
                device=device,
                compute_type=compute_type,
                local_files_only=False
            )

            logger.info(
                "stt_model_loaded",
                model=model_size,
                device=device
            )

            return True

        except Exception as e:
            logger.error("stt_model_load_failed", error=str(e))
            return False

    def _cuda_available(self) -> bool:
        """Check if CUDA is available."""
        try:
            import torch
            return torch.cuda.is_available()
        except ImportError:
            return False

    @capture_errors("stt", severity=BugSeverity.MEDIUM)
    def transcribe(self, audio_data: np.ndarray, sample_rate: int = None, timeout: float = 30.0) -> tuple:
        """
        Transcribe audio data synchronously.

        Args:
            audio_data: Audio samples as numpy array (int16 or float)
            sample_rate: Sample rate of the audio (default: None, assumes 16kHz)
            timeout: Maximum time to wait for transcription

        Returns:
            Tuple of (transcribed text, confidence score 0.0-1.0)
        """
        if not FASTER_WHISPER_AVAILABLE or self._model is None:
            # Fallback: return mock transcription
            logger.warning("stt_fallback_transcription")
            return self._mock_transcription(audio_data)

        try:
            # Convert audio to float32, normalize, and resample to 16kHz
            audio_float = self._prepare_audio(audio_data, sample_rate)

            # Run transcription
            segments, info = self._model.transcribe(
                audio_float,
                language=self.config.language,
                beam_size=self.config.beam_size,
                vad_filter=self.config.vad_filter
            )

            # Collect all segments
            segments_list = list(segments)
            text = " ".join(segment.text for segment in segments_list).strip()

            # Get confidence from language probability
            confidence = info.language_probability if hasattr(info, 'language_probability') else 0.0

            logger.info(
                "stt_transcription_complete",
                language=info.language,
                language_probability=confidence,
                duration=len(audio_float) / 16000,  # After resampling to 16kHz
                text=text[:100] + "..." if len(text) > 100 else text
            )

            return text, confidence

        except Exception as e:
            logger.error("stt_transcription_error", error=str(e))
            return "", 0.0

    async def transcribe_async(self, audio_data: np.ndarray) -> tuple:
        """
        Transcribe audio data asynchronously.

        Args:
            audio_data: Audio samples as numpy array (int16)

        Returns:
            Tuple of (transcribed text, confidence)
        """
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self.transcribe, audio_data)

    def _prepare_audio(self, audio_data: np.ndarray, sample_rate: int = None) -> np.ndarray:
        """
        Prepare audio data for transcription.

        Args:
            audio_data: Input audio (int16 or float)
            sample_rate: Original sample rate (default: 16000, assuming already ready)

        Returns:
            Normalized audio at 16kHz (float32, range [-1, 1])
        """
        import numpy as np
        
        # Convert to float32
        audio_float = audio_data.astype(np.float32)

        # Flatten if multi-channel
        if len(audio_float.shape) > 1:
            audio_float = audio_float.flatten()

        # Whisper expects 16kHz audio
        # If sample rate is provided and not 16kHz, resample
        target_rate = 16000
        if sample_rate and sample_rate != target_rate:
            try:
                import scipy.signal
                # Calculate number of samples at target rate
                num_samples = int(len(audio_float) * target_rate / sample_rate)
                audio_float = scipy.signal.resample(audio_float, num_samples)
            except ImportError:
                # If scipy not available, use numpy (less accurate)
                # Simple decimation - only works for integer ratios
                ratio = sample_rate // target_rate
                if ratio > 1:
                    audio_float = audio_float[::ratio]

        # Normalize to [-1, 1] range (Whisper expects this)
        max_val = np.max(np.abs(audio_float))
        if max_val > 0:
            audio_float /= max_val

        return audio_float

    def _mock_transcription(self, audio_data: np.ndarray) -> tuple:
        """
        Mock transcription for testing without faster-whisper.

        Args:
            audio_data: Audio samples

        Returns:
            Tuple of (mock transcribed text, confidence)
        """
        if len(audio_data) > 0:
            energy = np.sqrt(np.mean(audio_data.astype(np.float32) ** 2))
            if energy > 100:
                return "[mock transcription - speech detected]", 0.8
            else:
                return "[mock transcription - silence]", 0.5
        return "", 0.0

    def set_on_transcription(self, callback: Callable[[str], None]):
        """
        Register callback for transcription results.

        Args:
            callback: Function taking transcribed text as parameter
        """
        self._on_transcription = callback
        logger.info("on_transcription_callback_registered")

    @classmethod
    def get_supported_models(cls) -> List[str]:
        """
        Get list of supported model sizes.

        Returns:
            List of model size strings
        """
        return list(cls.SUPPORTED_MODELS.keys())
