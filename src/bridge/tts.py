"""Text-to-Speech Engine using Piper TTS.

Provides text-to-speech synthesis with voice selection
and audio output as numpy arrays.
"""
import io
import wave
from pathlib import Path
from typing import Optional, Callable, List

import structlog
import numpy as np

from bridge.errorcapture import ErrorCapture
from bridge.bug_tracker import BugSeverity

logger = structlog.get_logger()

# Try to import piper
try:
    from piper import PiperVoice
    from piper.config import PiperConfig, SynthesisConfig
    PIPER_AVAILABLE = True
except ImportError:
    PIPER_AVAILABLE = False
    logger.warning("piper-tts not available, using mock TTS")

# Default voice model path
DEFAULT_VOICE_DIR = Path.home() / ".voice-bridge" / "voices"
DEFAULT_VOICE = "en_US-lessac-medium"


class TTSState:
    """TTS engine states."""
    IDLE = "idle"
    GENERATING = "generating"
    ERROR = "error"


class TTSEngine:
    """Text-to-Speech engine using Piper TTS.
    
    Provides text-to-speech synthesis with configurable voices.
    """
    
    def __init__(
        self,
        voice: str = DEFAULT_VOICE,
        speed: float = 1.0,
        volume: float = 1.0,
        voice_dir: Optional[Path] = None,
    ):
        """Initialize TTS engine.
        
        Args:
            voice: Voice model name (e.g., "en_US-lessac-medium")
            speed: Speech speed multiplier (0.5-2.0)
            volume: Volume multiplier (0.0-2.0)
            voice_dir: Directory containing voice models
        """
        self.voice_name = voice
        self.speed = speed
        self.volume = volume
        self.voice_dir = voice_dir or DEFAULT_VOICE_DIR
        self._voice: Optional[PiperVoice] = None
        self._state = TTSState.IDLE
        self._available_voices: List[str] = []
        self._on_audio_generated: Optional[Callable[[np.ndarray], None]] = None

        # Error capture for automatic bug tracking
        self.error_capture = ErrorCapture(component="tts", severity=BugSeverity.MEDIUM)

        # Ensure voice directory exists
        self.voice_dir.mkdir(parents=True, exist_ok=True)
    
    @property
    def state(self) -> str:
        """Get current TTS state."""
        return self._state

    def set_on_audio_generated(self, callback: Callable[[np.ndarray], None]):
        """Set audio generated callback.
        
        Args:
            callback: Function called with audio data (np.ndarray int16) when TTS completes
        """
        self._on_audio_generated = callback

    @property
    def is_available(self) -> bool:
        """Check if TTS is available."""
        return PIPER_AVAILABLE
    
    @property
    def available_voices(self) -> List[str]:
        """Get list of available voice names."""
        return self._available_voices.copy()
    
    def initialize(self) -> bool:
        """
        Load Piper TTS model.
        
        Returns:
            True if model loaded successfully
        """
        if not PIPER_AVAILABLE:
            logger.warning("tts_not_available_using_mock")
            return False
        
        if self._voice is not None:
            logger.info("tts_voice_already_loaded", voice=self.voice_name)
            return True
        
        try:
            self._state = TTSState.GENERATING
            
            # Find voice model file
            model_path = self._find_voice_model(self.voice_name)
            
            if model_path is None:
                # Voice not found, will need to download
                logger.warning(
                    "tts_voice_not_found",
                    voice=self.voice_name,
                    hint="Run: piper download " + self.voice_name
                )
                # Fall back to mock
                self._voice = None
                self._state = TTSState.ERROR
                return False
            
            # Load voice
            config_path = self._find_voice_config(model_path)
            self._voice = PiperVoice.load(
                model_path,
                config_path=config_path,
            )
            
            # Scan for available voices
            self._scan_voices()

            self._state = TTSState.IDLE

            logger.info(
                "tts_voice_loaded",
                voice=self.voice_name,
                model=str(model_path)
            )

            return True
            
        except Exception as e:
            self._state = TTSState.ERROR
            logger.error("tts_load_failed", voice=self.voice_name, error=str(e))
            return False
    
    def _find_voice_model(self, voice_name: str) -> Optional[Path]:
        """Find voice model file.
        
        Args:
            voice_name: Voice name (e.g., "en_US-lessac-medium")
            
        Returns:
            Path to model file or None if not found
        """
        # Check in voice_dir
        model_path = self.voice_dir / f"{voice_name}.onnx"
        if model_path.exists():
            return model_path
        
        # Check common locations
        for check_dir in [
            self.voice_dir,
            Path.home() / ".local" / "share" / "piper",
        ]:
            model_path = check_dir / f"{voice_name}.onnx"
            if model_path.exists():
                return model_path
        
        return None
    
    def _find_voice_config(self, model_path: Path) -> Optional[Path]:
        """Find voice config file.
        
        Args:
            model_path: Path to the .onnx model file
            
        Returns:
            Path to config file or None if not found
        """
        # Try .onnx.json first (huggingface format)
        config_path = Path(str(model_path).replace(".onnx", ".onnx.json"))
        if config_path.exists():
            return config_path
        
        # Try .json (piper format)
        config_path = Path(str(model_path).replace(".onnx", ".json"))
        if config_path.exists():
            return config_path
        
        return None
    
    def _synthesize(self, text: str) -> np.ndarray:
        """Run Piper inference and return int16 audio. Internal helper."""
        from piper.config import SynthesisConfig
        syn_config = SynthesisConfig(
            noise_scale=0.667,
            length_scale=1.0 / self.speed,
            noise_w_scale=0.8,
        )
        wav_buffer = io.BytesIO()
        with wave.open(wav_buffer, 'wb') as wav_file:
            self._voice.synthesize_wav(text, wav_file, syn_config=syn_config)
        wav_buffer.seek(0)
        with wave.open(wav_buffer, 'rb') as wav_file:
            n_frames = wav_file.getnframes()
            audio_data = wav_file.readframes(n_frames)
        audio = np.frombuffer(audio_data, dtype=np.int16)
        if self.volume != 1.0:
            audio = (audio * self.volume).clip(-32768, 32767).astype(np.int16)
        return audio

    def _scan_voices(self):
        """Scan for available voice models."""
        self._available_voices.clear()
        
        for path in self.voice_dir.glob("*.onnx"):
            voice_name = path.stem
            self._available_voices.append(voice_name)
        
        logger.debug(
            "tts_voices_scanned",
            count=len(self._available_voices),
            voices=self._available_voices
        )
    
    def speak(self, text: str) -> np.ndarray:
        """
        Generate speech audio from text.

        Args:
            text: Text to synthesize

        Returns:
            Audio samples as numpy array (int16)
        """
        if not PIPER_AVAILABLE or self._voice is None:
            logger.warning("tts_not_available_returning_silence", text=text[:50])
            return self._mock_audio(text)

        with self.error_capture.context(context="speak"):
            try:
                self._state = TTSState.GENERATING
                audio = self._synthesize(text)
                self._state = TTSState.IDLE
                logger.debug(
                    "tts_synthesized",
                    text_length=len(text),
                    audio_length=len(audio),
                )
                return audio

            except Exception as e:
                self._state = TTSState.ERROR
                logger.error("tts_synthesis_failed", text=text[:50], error=str(e))
                return self._mock_audio(text)
    
    def speak_streaming(
        self,
        text: str,
        on_chunk: Callable[[bytes], None]
    ) -> bool:
        """
        Stream speech audio chunks.
        
        Args:
            text: Text to synthesize
            on_chunk: Callback for each audio chunk
            
        Returns:
            True if synthesis succeeded
        """
        if not PIPER_AVAILABLE or self._voice is None:
            # Generate mock audio and send as chunks
            mock = self._mock_audio(text)
            chunk_size = 4096
            for i in range(0, len(mock), chunk_size):
                chunk = mock[i:i+chunk_size].tobytes()
                on_chunk(chunk)
            return True
        
        try:
            self._state = TTSState.GENERATING
            
            syn_config = SynthesisConfig(
                noise_scale=0.667,
                length_scale=1.0 / self.speed,
                noise_w_scale=0.8,
            )
            
            # Stream audio chunks
            for audio_chunk in self._voice.synthesize(text, syn_config=syn_config):
                # Convert float audio to int16
                if audio_chunk.audio.shape[0] > 0:
                    audio_int16 = (audio_chunk.audio * 32767).astype(np.int16)
                    on_chunk(audio_int16.tobytes())
            
            self._state = TTSState.IDLE
            return True
            
        except Exception as e:
            self._state = TTSState.ERROR
            logger.error("tts_streaming_failed", error=str(e))
            return False
    
    def _mock_audio(self, text: str) -> np.ndarray:
        """Generate mock audio (silence) for testing.
        
        Args:
            text: Text that would be synthesized
            
        Returns:
            Silence as numpy array (int16)
        """
        # Estimate duration at ~150ms per word with a 300ms floor
        sample_rate = 22050
        word_count = max(1, len(text.split()))
        duration_estimate = max(0.3, word_count * 0.15)
        n_samples = int(sample_rate * duration_estimate)
        
        # Return silence
        return np.zeros(n_samples, dtype=np.int16)
    
    def set_voice(self, voice_name: str) -> bool:
        """
        Change the voice.
        
        Args:
            voice_name: New voice name
            
        Returns:
            True if voice changed successfully
        """
        if voice_name == self.voice_name:
            return True
        
        old_voice = self.voice_name
        self.voice_name = voice_name
        
        # Reinitialize with new voice
        self._voice = None
        if self.initialize():
            logger.info("tts_voice_changed", old=old_voice, new=voice_name)
            return True
        else:
            self.voice_name = old_voice
            self.initialize()  # Revert
            return False
    
    def stop(self):
        """Stop any ongoing synthesis."""
        self._state = TTSState.IDLE


# Convenience function
def create_tts(
    voice: str = DEFAULT_VOICE,
    speed: float = 1.0,
    volume: float = 1.0,
) -> TTSEngine:
    """Create and initialize TTS engine.
    
    Args:
        voice: Voice model name
        speed: Speech speed (0.5-2.0)
        volume: Volume multiplier (0.0-2.0)
        
    Returns:
        Initialized TTS engine
    """
    engine = TTSEngine(voice=voice, speed=speed, volume=volume)
    engine.initialize()
    return engine