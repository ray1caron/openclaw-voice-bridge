"""
Audio Buffer with Lazy Resampling.

Stores audio at original rate and resamples on-demand with caching.
"""
import numpy as np
from typing import Optional, Dict
import structlog

logger = structlog.get_logger()


class AudioBuffer:
    """
    Ring buffer for audio with lazy resampling.
    
    Stores at original rate, converts on-demand.
    Caches conversions to avoid redundant processing.
    """
    
    # Standard rates for common services
    RATE_VAD = 16000      # WebRTC VAD standard
    RATE_STT = 16000      # Whisper trained at 16kHz
    RATE_TTS = 22050      # Piper native rate
    
    def __init__(self, max_samples: int, sample_rate: int = 16000):
        """
        Initialize buffer.
        
        Args:
            max_samples: Maximum samples to store
            sample_rate: Sample rate of incoming audio
        """
        self.max_samples = max_samples
        self._sample_rate = sample_rate
        self._buffer: np.ndarray = np.zeros(max_samples, dtype=np.int16)
        self._write_pos: int = 0
        self._count: int = 0
        self._converted: Dict[int, np.ndarray] = {}  # Cache: rate → audio
    
    def write(self, audio: np.ndarray, block: bool = True) -> bool:
        """
        Write audio to buffer at original rate.
        
        Args:
            audio: Audio samples (int16)
            block: Not used (compatibility)
            
        Returns:
            True if successful
        """
        # Invalidate cache on new write
        self._converted.clear()
        
        # Ring buffer write
        n = len(audio)
        if n > self.max_samples:
            audio = audio[-self.max_samples:]
            n = self.max_samples
        
        # Calculate write positions
        first_part = min(n, self.max_samples - self._write_pos)
        self._buffer[self._write_pos:self._write_pos + first_part] = audio[:first_part]
        
        if n > first_part:
            # Wrap around
            self._buffer[0:n - first_part] = audio[first_part:]
        
        self._write_pos = (self._write_pos + n) % self.max_samples
        self._count = min(self._count + n, self.max_samples)
        
        return True
    
    def read(self, n: Optional[int] = None) -> np.ndarray:
        """
        Read audio from buffer.
        
        Args:
            n: Number of samples (None = all)
            
        Returns:
            Audio samples at original rate
        """
        if n is None:
            n = self._count
        
        n = min(n, self._count)
        
        if n == 0:
            return np.array([], dtype=np.int16)
        
        # Read from ring buffer
        start = (self._write_pos - n) % self.max_samples
        if start + n <= self.max_samples:
            return self._buffer[start:start + n].copy()
        else:
            # Wrap around
            first_part = self.max_samples - start
            result = np.empty(n, dtype=np.int16)
            result[:first_part] = self._buffer[start:]
            result[first_part:] = self._buffer[:n - first_part]
            return result
    
    def get_at_rate(self, target_rate: int) -> np.ndarray:
        """
        Get audio at requested sample rate.
        
        Lazy resampling with caching - only converts when needed.
        
        Args:
            target_rate: Desired sample rate
            
        Returns:
            Audio at requested rate
        """
        if target_rate == self._sample_rate:
            return self.read()
        
        # Check cache
        if target_rate in self._converted:
            return self._converted[target_rate]
        
        # Convert and cache
        audio = self.read()
        if len(audio) == 0:
            return audio
        
        converted = self._resample(audio, self._sample_rate, target_rate)
        self._converted[target_rate] = converted
        return converted
    
    def _resample(self, audio: np.ndarray, from_rate: int, to_rate: int) -> np.ndarray:
        """Resample audio to target rate using scipy."""
        if from_rate == to_rate:
            return audio
        
        try:
            import scipy.signal
            num_samples = int(len(audio) * to_rate / from_rate)
            return scipy.signal.resample(audio.astype(np.float64), num_samples).astype(np.int16)
        except ImportError:
            # Fallback: simple decimation for integer ratios
            ratio = from_rate // to_rate
            if ratio > 1 and from_rate % to_rate == 0:
                return audio[::ratio]
            logger.warning("resample_fallback", 
                          from_rate=from_rate, 
                          to_rate=to_rate,
                          note="scipy not available")
            return audio
    
    def clear(self):
        """Clear buffer and cache."""
        self._write_pos = 0
        self._count = 0
        self._converted.clear()
    
    @property
    def sample_rate(self) -> int:
        """Get sample rate."""
        return self._sample_rate
    
    @sample_rate.setter
    def sample_rate(self, rate: int):
        """Set sample rate (invalidates cache)."""
        self._sample_rate = rate
        self._converted.clear()
    
    def __len__(self) -> int:
        """Get sample count."""
        return self._count
    
    def is_empty(self) -> bool:
        """Check if buffer is empty."""
        return self._count == 0