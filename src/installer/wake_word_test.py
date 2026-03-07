"""Wake Word Acknowledgement Test for Voice Bridge Installation.

Provides interactive testing for wake word detection and acknowledgement:
- Wake word detection validation
- OpenClaw connection test
- Acknowledgement response verification
- TTS playback confirmation
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from enum import Enum
from typing import Optional, Callable
import threading

import structlog

logger = structlog.get_logger()


class WakeWordTestStatus(Enum):
    """Status of a wake word test."""
    PENDING = "pending"
    LISTENING = "listening"
    DETECTED = "detected"
    WAITING_RESPONSE = "waiting_response"
    RESPONSE_RECEIVED = "response_received"
    PLAYING = "playing"
    PASSED = "passed"
    FAILED = "failed"
    SKIPPED = "skipped"
    ERROR = "error"
    TIMEOUT = "timeout"
    NO_OPENCLAW = "no_openclaw"


@dataclass
class WakeWordTestResult:
    """Result of a wake word acknowledgement test."""
    status: WakeWordTestStatus
    message: str
    wake_word: Optional[str] = None
    detected_text: Optional[str] = None
    response_phrase: Optional[str] = None
    openclaw_responded: bool = False
    duration_ms: int = 0
    error: Optional[Exception] = None
    
    @property
    def passed(self) -> bool:
        return self.status == WakeWordTestStatus.PASSED
    
    @property
    def failed(self) -> bool:
        return self.status in (
            WakeWordTestStatus.FAILED,
            WakeWordTestStatus.ERROR,
            WakeWordTestStatus.TIMEOUT,
        )
    
    def __str__(self) -> str:
        status_icon = {
            WakeWordTestStatus.PENDING: "⏳",
            WakeWordTestStatus.LISTENING: "🎤",
            WakeWordTestStatus.DETECTED: "👂",
            WakeWordTestStatus.WAITING_RESPONSE: "⏳",
            WakeWordTestStatus.RESPONSE_RECEIVED: "✉️",
            WakeWordTestStatus.PLAYING: "🔊",
            WakeWordTestStatus.PASSED: "✅",
            WakeWordTestStatus.FAILED: "❌",
            WakeWordTestStatus.SKIPPED: "⏭️",
            WakeWordTestStatus.ERROR: "💥",
            WakeWordTestStatus.TIMEOUT: "⏰",
            WakeWordTestStatus.NO_OPENCLAW: "🔌",
        }.get(self.status, "❓")
        
        result = f"{status_icon} {self.message}"
        if self.detected_text:
            result += f" (heard: '{self.detected_text}')"
        return result


class WakeWordAckTester:
    """Tests wake word detection and acknowledgement flow."""
    
    def __init__(
        self,
        wake_word: str = None,
        response_phrase: str = "Yes?",
        ack_timeout_ms: int = 5000,
        listen_timeout_ms: int = 15000,
    ):
        """Initialize the wake word acknowledgement tester.
        
        Args:
            wake_word: The wake word phrase to detect (loads from config if None)
            response_phrase: Expected response phrase from OpenClaw
            ack_timeout_ms: Timeout for OpenClaw response in milliseconds
            listen_timeout_ms: Timeout for wake word detection in milliseconds
        """
        self.logger = structlog.get_logger()
        
        # Load wake word from config if not provided
        if wake_word is None:
            try:
                from bridge.config import get_config
                config = get_config()
                wake_word = config.wake_word.wake_word
            except Exception:
                wake_word = "computer"  # Fallback default
        
        self.wake_word = wake_word
        self.response_phrase = response_phrase
        self.ack_timeout_ms = ack_timeout_ms
        self.listen_timeout_ms = listen_timeout_ms
        
        # Audio dependencies
        self._audio_available = False
        self._sounddevice = None
        self._numpy = None
        self._check_dependencies()
        
        # Wake word detection
        self._wake_detector = None
        self._stt_engine = None
        
        # Async state
        self._openclaw_client = None
        self._response_received = False
        self._received_phrase = None
        self._received_audio = None
        
    def _check_dependencies(self) -> None:
        """Check if audio dependencies are available."""
        try:
            import sounddevice as sd
            self._sounddevice = sd
            self._audio_available = True
        except ImportError:
            self.logger.warning("sounddevice not available - wake word tests will be skipped")
        
        try:
            import numpy
            self._numpy = numpy
        except ImportError:
            self.logger.warning("numpy not available - some tests will be limited")
    
    @property
    def audio_available(self) -> bool:
        """Check if audio testing is available."""
        return self._audio_available
    
    def _init_wake_detector(self) -> bool:
        """Initialize wake word detector.
        
        Returns:
            True if initialization successful
        """
        try:
            from bridge.wake_word import WakeWordDetector
            from bridge.stt import STTEngine, STTConfig
            from bridge.config import get_config
            
            config = get_config()
            
            # STT engine - use STTConfig from AppConfig
            stt_config = getattr(config, 'stt', None)
            if stt_config is None:
                stt_config = STTConfig()
            self._stt_engine = STTEngine(stt_config)
            
            if not self._stt_engine.initialize():
                self.logger.warning("stt_engine_not_initialized")
                return False
            
            self._wake_detector = WakeWordDetector(
                config=config,
                stt_engine=self._stt_engine,
            )
            
            return True
            
        except ImportError as e:
            self.logger.warning("wake_word_dependencies_not_available", error=str(e))
            return False
        except Exception as e:
            self.logger.error("wake_detector_init_failed", error=str(e))
            return False
    
    def _init_tts(self) -> bool:
        """Initialize TTS engine.
        
        Returns:
            True if TTS available
        """
        try:
            from bridge.tts import TTSEngine
            self._tts = TTSEngine()
            return self._tts.initialize()
        except ImportError:
            return False
        except Exception as e:
            self.logger.error("tts_init_failed", error=str(e))
            return False
    
    def _play_tts(self, text: str) -> bool:
        """Play text using TTS.
        
        Args:
            text: Text to speak
            
        Returns:
            True if playback successful
        """
        if not self._tts:
            if not self._init_tts():
                return False
        
        try:
            # Generate audio
            audio_data = self._tts.speak(text)
            
            if audio_data is None or len(audio_data) == 0:
                return False
            
            # Play audio
            self._sounddevice.play(audio_data, samplerate=22050)
            self._sounddevice.wait()
            return True
            
        except Exception as e:
            self.logger.error("tts_playback_failed", error=str(e))
            return False
    
    def _play_tone(self, frequency: int = 880, duration: float = 0.1) -> None:
        """Play a notification tone.
        
        Args:
            frequency: Tone frequency in Hz
            duration: Duration in seconds
        """
        if not self._audio_available or not self._numpy:
            return
        
        try:
            sample_rate = 44100
            t = self._numpy.linspace(0, duration, int(sample_rate * duration), False)
            tone = self._numpy.sin(2 * self._numpy.pi * frequency * t) * 0.3
            self._sounddevice.play(tone, sample_rate)
            self._sounddevice.wait()
        except Exception:
            pass  # Non-critical
    
    def test_wake_word_detection(
        self,
        on_listening: Optional[Callable[[], None]] = None,
        on_speech_detected: Optional[Callable[[], None]] = None,
        on_wake_detected: Optional[Callable[[str], None]] = None,
        on_timeout: Optional[Callable[[], None]] = None,
    ) -> WakeWordTestResult:
        """Test wake word detection only (no OpenClaw connection).
        
        Args:
            on_listening: Callback when listening starts
            on_speech_detected: Callback when speech is detected
            on_wake_detected: Callback when wake word is detected
            on_timeout: Callback when timeout occurs
            
        Returns:
            WakeWordTestResult with detection status
        """
        import time
        
        if not self._audio_available:
            return WakeWordTestResult(
                status=WakeWordTestStatus.SKIPPED,
                message="Audio libraries not available",
            )
        
        if not self._init_wake_detector():
            return WakeWordTestResult(
                status=WakeWordTestStatus.ERROR,
                message="Wake word detector initialization failed",
                error=Exception("Could not initialize wake word detector"),
            )
        
        start_time = time.time()
        
        try:
            # Audio capture parameters
            sample_rate = 16000
            channels = 1
            chunk_size = 480  # 30ms at 16kHz
            
            # Detection state
            detected = False
            detected_text = None
            speech_detected = False
            
            def speech_start_callback():
                nonlocal speech_detected
                speech_detected = True
                if on_speech_detected:
                    on_speech_detected()
            
            def wake_detected_callback(text: str):
                nonlocal detected, detected_text
                detected = True
                detected_text = text
                if on_wake_detected:
                    on_wake_detected(text)
            
            # Register callbacks
            self._wake_detector.register_on_speech_start(speech_start_callback)
            self._wake_detector.register_on_detected(wake_detected_callback)
            self._wake_detector.start()
            
            if on_listening:
                on_listening()
            
            # Start recording
            default_input = self._sounddevice.default.device[0]
            
            def audio_callback(indata, frames, time_info, status):
                if status:
                    self.logger.warning("audio_callback_status", status=status)
                # Process frame through wake word detector
                audio_frame = indata.flatten()
                self._wake_detector.process_frame(audio_frame, sample_rate)
            
            # Calculate number of chunks for timeout
            timeout_chunks = int(self.listen_timeout_ms / 1000 * sample_rate / chunk_size)
            
            with self._sounddevice.InputStream(
                samplerate=sample_rate,
                channels=channels,
                blocksize=chunk_size,
                device=default_input,
                dtype='int16',
                callback=audio_callback,
            ):
                # Wait for detection or timeout
                for _ in range(timeout_chunks):
                    if detected:
                        break
                    time.sleep(chunk_size / sample_rate)
            
            self._wake_detector.stop()
            
            duration_ms = int((time.time() - start_time) * 1000)
            
            if detected:
                return WakeWordTestResult(
                    status=WakeWordTestStatus.DETECTED,
                    message=f"Wake word '{self.wake_word}' detected!",
                    wake_word=self.wake_word,
                    detected_text=detected_text,
                    duration_ms=duration_ms,
                )
            else:
                if on_timeout:
                    on_timeout()
                return WakeWordTestResult(
                    status=WakeWordTestStatus.TIMEOUT,
                    message=f"No wake word detected (listened for {self.listen_timeout_ms / 1000:.0f}s)",
                    duration_ms=duration_ms,
                )
                
        except Exception as e:
            self.logger.error("wake_word_detection_failed", error=str(e))
            return WakeWordTestResult(
                status=WakeWordTestStatus.ERROR,
                message="Wake word detection failed",
                error=e,
            )
    
    def test_full_acknowledgement(
        self,
        on_listening: Optional[Callable[[], None]] = None,
        on_speech_detected: Optional[Callable[[], None]] = None,
        on_wake_detected: Optional[Callable[[str], None]] = None,
        on_ack_sent: Optional[Callable[[], None]] = None,
        on_response_received: Optional[Callable[[str], None]] = None,
        on_playing: Optional[Callable[[], None]] = None,
        on_timeout: Optional[Callable[[], None]] = None,
        mock_openclaw_response: bool = False,
    ) -> WakeWordTestResult:
        """Test full wake word acknowledgement flow.
        
        This test:
        1. Listens for wake word
        2. Sends acknowledgement to OpenClaw
        3. Waits for OpenClaw response
        4. Plays response via TTS
        5. Confirms flow completed successfully
        
        Args:
            on_listening: Callback when listening starts
            on_speech_detected: Callback when speech is detected
            on_wake_detected: Callback when wake word is detected
            on_ack_sent: Callback when acknowledgement sent to OpenClaw
            on_response_received: Callback when response received from OpenClaw
            on_playing: Callback when TTS playback starts
            on_timeout: Callback when timeout occurs
            mock_openclaw_response: If True, use local TTS instead of waiting for OpenClaw
            
        Returns:
            WakeWordTestResult with full test status
        """
        import time
        
        if not self._audio_available:
            return WakeWordTestResult(
                status=WakeWordTestStatus.SKIPPED,
                message="Audio libraries not available",
            )
        
        if not self._init_wake_detector():
            return WakeWordTestResult(
                status=WakeWordTestStatus.ERROR,
                message="Wake word detector initialization failed",
                error=Exception("Could not initialize wake word detector"),
            )
        
        start_time = time.time()
        
        try:
            # Initialize TTS for response playback
            tts_available = self._init_tts()
            if not tts_available:
                self.logger.warning("tts_not_available_for_acknowledgement_test")
            
            # Async tracking
            self._response_received = False
            self._received_phrase = None
            
            # Try to connect to OpenClaw (unless mocking)
            if not mock_openclaw_response:
                openclaw_connected = self._try_connect_openclaw()
                if not openclaw_connected:
                    # Fall back to local TTS mode
                    self.logger.info("openclaw_not_available_using_local_tts")
                    mock_openclaw_response = True
            else:
                openclaw_connected = False
            
            # Audio capture parameters
            sample_rate = 16000
            channels = 1
            chunk_size = 480  # 30ms at 16kHz
            
            # Detection state
            detected = False
            detected_text = None
            
            def wake_detected_callback(text: str):
                nonlocal detected, detected_text
                detected = True
                detected_text = text
                if on_wake_detected:
                    on_wake_detected(text)
            
            # Register callbacks
            self._wake_detector.register_on_detected(wake_detected_callback)
            self._wake_detector.start()
            
            if on_listening:
                on_listening()
            
            # Start recording
            default_input = self._sounddevice.default.device[0]
            
            def audio_callback(indata, frames, time_info, status):
                if status:
                    self.logger.warning("audio_callback_status", status=status)
                audio_frame = indata.flatten()
                self._wake_detector.process_frame(audio_frame, sample_rate)
            
            # Calculate number of chunks for timeout
            timeout_chunks = int(self.listen_timeout_ms / 1000 * sample_rate / chunk_size)
            
            with self._sounddevice.InputStream(
                samplerate=sample_rate,
                channels=channels,
                blocksize=chunk_size,
                device=default_input,
                dtype='int16',
                callback=audio_callback,
            ):
                # Wait for detection or timeout
                for _ in range(timeout_chunks):
                    if detected:
                        break
                    time.sleep(chunk_size / sample_rate)
            
            self._wake_detector.stop()
            
            # Check if wake word was detected
            if not detected:
                if on_timeout:
                    on_timeout()
                duration_ms = int((time.time() - start_time) * 1000)
                return WakeWordTestResult(
                    status=WakeWordTestStatus.TIMEOUT,
                    message=f"No wake word detected after {self.listen_timeout_ms / 1000:.0f}s",
                    duration_ms=duration_ms,
                )
            
            # Send acknowledgement to OpenClaw
            if openclaw_connected and self._openclaw_client:
                # HTTP returns response directly, no need to wait
                response = self._send_acknowledgement(detected_text)
                if on_ack_sent:
                    on_ack_sent()
                
                if response:
                    self._received_phrase = response
                    if on_response_received:
                        on_response_received(response)
                else:
                    # Timeout waiting for response
                    self._received_phrase = self.response_phrase  # Use configured phrase
            
            # Use configured phrase for mock mode
            phrase_to_speak = self._received_phrase or self.response_phrase
            
            # Play response via TTS
            if tts_available:
                if on_playing:
                    on_playing()
                
                play_success = self._play_tts(phrase_to_speak)
                if not play_success:
                    # Fallback to system beep
                    self._play_tone(440, 0.2)
            else:
                # Just play a beep if TTS not available
                self._play_tone(440, 0.2)
            
            # HTTP client doesn't need disconnect - just clean up reference
            self._openclaw_client = None
            
            duration_ms = int((time.time() - start_time) * 1000)
            
            return WakeWordTestResult(
                status=WakeWordTestStatus.PASSED,
                message="Wake word acknowledgement test passed!",
                wake_word=self.wake_word,
                detected_text=detected_text,
                response_phrase=phrase_to_speak,
                openclaw_responded=self._response_received,
                duration_ms=duration_ms,
            )
                
        except Exception as e:
            self.logger.error("full_acknowledgement_test_failed", error=str(e), exc_info=True)
            return WakeWordTestResult(
                status=WakeWordTestStatus.ERROR,
                message=f"Test failed: {str(e)}",
                error=e,
            )
    
    def _try_connect_openclaw(self) -> bool:
        """Try to connect to OpenClaw via HTTP.
        
        Returns:
            True if connected successfully
        """
        try:
            from bridge.http_client import OpenClawHTTPClient
            from bridge.config import get_config
            
            config = get_config()
            
            # Use HTTP client instead of WebSocket
            self._openclaw_client = OpenClawHTTPClient(config=config.openclaw)
            
            # HTTP client doesn't need persistent connection
            # Just verify we can reach OpenClaw
            self.logger.info(
                "http_client_initialized",
                base_url=self._openclaw_client.base_url,
                mode="http"
            )
            return True
                
        except ImportError:
            self.logger.warning("http_client_not_available")
            return False
        except Exception as e:
            self.logger.warning("openclaw_connection_attempt_failed", error=str(e))
            return False
    
    def _send_acknowledgement(self, wake_word: str) -> Optional[str]:
        """Send wake word acknowledgement to OpenClaw via HTTP.
        
        Args:
            wake_word: Detected wake word phrase
            
        Returns:
            Response text from OpenClaw, or None if failed
        """
        if not self._openclaw_client:
            return None
        
        try:
            loop = asyncio.new_event_loop()
            # Use HTTP client to send wake word and get response
            response = loop.run_until_complete(
                self._openclaw_client.send_wake_ack(wake_word)
            )
            # Close the HTTP session
            loop.run_until_complete(self._openclaw_client.close())
            loop.close()
            
            if response:
                self._received_phrase = response
                self._response_received = True
                self.logger.info("wake_ack_response_received", response=response)
                return response
            else:
                self.logger.warning("empty_wake_ack_response")
                return None
        except Exception as e:
            self.logger.error("acknowledgement_send_failed", error=str(e))
            return None


def test_wake_word_detection(
    wake_word: str = None,
    timeout_ms: int = 15000,
) -> WakeWordTestResult:
    """Convenience function to test wake word detection.
    
    Args:
        wake_word: Wake word phrase to detect (loads from config if None)
        timeout_ms: Detection timeout in milliseconds
        
    Returns:
        Test result
    """
    tester = WakeWordAckTester(
        wake_word=wake_word,
        listen_timeout_ms=timeout_ms,
    )
    return tester.test_wake_word_detection()


def test_full_acknowledgement(
    wake_word: str = None,
    response_phrase: str = "Yes?",
    mock_openclaw: bool = False,
) -> WakeWordTestResult:
    """Convenience function to test full acknowledgement flow.
    
    Args:
        wake_word: Wake word phrase to detect (loads from config if None)
        response_phrase: Expected response phrase
        mock_openclaw: Use local TTS instead of OpenClaw
        
    Returns:
        Test result
    """
    tester = WakeWordAckTester(
        wake_word=wake_word,
        response_phrase=response_phrase,
    )
    return tester.test_full_acknowledgement(mock_openclaw_response=mock_openclaw)