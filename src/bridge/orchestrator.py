"""
Voice Orchestrator for Voice Bridge.

Main voice processing loop that coordinates:
- Audio pipeline
- Wake word detection
- Speech-to-text
- HTTP/WebSocket communication
- Text-to-speech
- Barge-in handling
- Wake word acknowledgement
"""
import asyncio
import enum
import threading
import time
from dataclasses import dataclass
from typing import Callable, Optional

import structlog
import numpy as np

from bridge.config import get_config
from bridge.audio_pipeline import AudioPipeline, PipelineState
from bridge.vad import SpeechSegment
from bridge.wake_word import WakeWordDetector
from bridge.stt import STTEngine, STTConfig
from bridge.tts import TTSEngine
from bridge.websocket_client import OpenClawWebSocketClient
from bridge.http_client import OpenClawHTTPClient, OpenClawHTTPTimeoutError, OpenClawHTTPError
from bridge.errorcapture import ErrorCapture
from bridge.bug_tracker import BugSeverity

logger = structlog.get_logger()


class OrchestratorState(enum.Enum):
    """Orchestrator state machine states."""
    IDLE = "idle"                    # Waiting for wake word
    LISTENING_FOR_WAKE_WORD = "listening_for_wake_word"  # Active VAD
    WAKE_WORD_ACK = "wake_word_ack"  # Waiting for ack response from OpenClaw
    LISTENING = "listening"          # Capturing speech after wake word
    PROCESSING = "processing"        # Sending to OpenClaw
    SPEAKING = "speaking"            # Playing TTS response
    ERROR = "error"                  # Error state


@dataclass
class OrchestratorStats:
    """Orchestrator statistics."""
    state_changes: int = 0
    wake_word_detections: int = 0
    completed_transcriptions: int = 0
    completed_responses: int = 0
    barge_in_count: int = 0
    error_count: int = 0
    start_time: float = 0.0
    
    @property
    def uptime_seconds(self) -> float:
        """Get orchestrator uptime in seconds."""
        if self.start_time == 0:
            return 0
        return time.time() - self.start_time


class VoiceOrchestrator:
    """
    Main voice processing orchestrator.
    
    Manages the complete voice interaction loop:
    1. Listen for wake word
    2. Capture speech after wake word
    3. Transcribe to text
    4. Send to OpenClaw
    5. Receive and play response
    
    Supports barge-in for interruption handling.
    """
    
    def __init__(
        self,
        config=None,
        audio_pipeline: Optional[AudioPipeline] = None,
        stt_engine: Optional[STTEngine] = None,
        tts_engine: Optional[TTSEngine] = None,
        websocket: Optional[OpenClawWebSocketClient] = None
    ):
        """
        Initialize voice orchestrator.
        
        Args:
            config: Configuration (loads from defaults if None)
            audio_pipeline: Audio pipeline instance (creates new if None)
            stt_engine: STT engine (creates new if None)
            tts_engine: TTS engine (creates new if None)
            websocket: WebSocket client (creates new if None)
        """
        self.config = config or get_config()
        
        # Error capture for bug tracking
        self.error_capture = ErrorCapture(
            component="orchestrator",
            severity=BugSeverity.HIGH,
        )
        
        # Initialize components (or use provided)
        self.audio_pipeline = audio_pipeline or AudioPipeline()
        
        # STT engine - use config's STT settings (important for vad_filter)
        if stt_engine:
            self.stt_engine = stt_engine
        else:
            stt_config = getattr(self.config, 'stt', None) or STTConfig()
            self.stt_engine = STTEngine(stt_config)
        
        self.tts_engine = tts_engine or TTSEngine()
        self.websocket = websocket or OpenClawWebSocketClient()
        
        # HTTP client for API mode
        self.http_client: Optional[OpenClawHTTPClient] = None
        if self.config.openclaw.api_mode == "http":
            self.http_client = OpenClawHTTPClient(config=self.config.openclaw)
            logger.info("http_client_initialized", mode="http")
        
        # Wake word detector
        self.wake_word_detector = WakeWordDetector(
            config=self.config,
            stt_engine=self.stt_engine
        )
        
        # State
        self._state = OrchestratorState.IDLE
        self._state_lock = threading.RLock()
        self._state_callbacks: list[Callable[[OrchestratorState, OrchestratorState], None]] = []
        
        # Statistics
        self._stats = OrchestratorStats(start_time=time.time())
        
        # Runtime flags
        self._running = False
        self._running_lock = threading.Lock()
        
        # Speech state
        self._is_speaking = False
        self._is_speaking_lock = threading.Lock()
        
        # Barge-in
        self._barge_in_enabled = True
        
        # Wake word acknowledgement
        self._wake_ack_pending = False
        self._wake_ack_timer: Optional[threading.Timer] = None
        self._wake_ack_lock = threading.Lock()
        # Set when stop() is called so in-flight timer callbacks can bail early
        self._shutdown_event = threading.Event()

        # Event loop reference for safe async dispatch from audio threads.
        # Populated in start() once the loop is guaranteed to be running.
        self._event_loop: Optional[asyncio.AbstractEventLoop] = None

        # Register callbacks
        self._setup_callbacks()
        
        logger.info(
            "voice_orchestrator_initialized",
            state_machine=[s.value for s in OrchestratorState]
        )
    
    def _setup_callbacks(self):
        """Setup component callbacks."""
        # Audio pipeline state changes
        self.audio_pipeline.add_state_callback(self._on_pipeline_state_change)
        
        # Audio frame callback for wake word detection
        self.audio_pipeline.add_frame_callback(self._on_audio_frame)
        
        # Speech segment callback for STT (post-wake word)
        self.audio_pipeline.add_speech_segment_callback(self._on_speech_segment)
        
        # Wake word detector callbacks
        self.wake_word_detector.register_on_detected(self.on_wake_word_detected)
        
        # WebSocket callbacks
        if self.websocket:
            self.websocket.on_message = self._on_websocket_message
            self.websocket.on_connect = self._on_websocket_connect
            self.websocket.on_disconnect = self._on_websocket_disconnect
    
    @property
    def state(self) -> OrchestratorState:
        """Get current orchestrator state."""
        with self._state_lock:
            return self._state
    
    def _set_state(self, new_state: OrchestratorState):
        """Set orchestrator state and notify callbacks."""
        with self._state_lock:
            old_state = self._state
            if old_state != new_state:
                self._state = new_state
                self._stats.state_changes += 1
                logger.info(
                    "orchestrator_state_changed",
                    old=old_state.value,
                    new=new_state.value
                )
                
                # Notify callbacks
                for callback in self._state_callbacks:
                    try:
                        callback(old_state, new_state)
                    except Exception as e:
                        logger.error("state_callback_error", error=str(e))
    
    def add_state_callback(self, callback: Callable[[OrchestratorState, OrchestratorState], None]):
        """Add state change callback."""
        self._state_callbacks.append(callback)
    
    def remove_state_callback(self, callback: Callable[[OrchestratorState, OrchestratorState], None]):
        """Remove state change callback."""
        if callback in self._state_callbacks:
            self._state_callbacks.remove(callback)
    
    @property
    def stats(self) -> OrchestratorStats:
        """Get orchestrator statistics."""
        return self._stats

    def get_stats(self) -> OrchestratorStats:
        """Get orchestrator statistics (alias for stats property)."""
        return self._stats
    
    def start(self) -> bool:
        """Start the voice orchestrator."""
        with self._running_lock:
            if self._running:
                logger.warning("orchestrator_already_running")
                return False

            logger.info("starting_orchestrator")

            # Clear shutdown flag so timer callbacks work after a restart
            self._shutdown_event.clear()

            # Capture the running event loop so audio-thread callbacks can
            # safely schedule coroutines via run_coroutine_threadsafe.
            try:
                self._event_loop = asyncio.get_running_loop()
            except RuntimeError:
                self._event_loop = None

            # Initialize components
            if not self.stt_engine.initialize():
                logger.warning("stt_initialization_failed")

            if not self.tts_engine.initialize():
                logger.warning("tts_initialization_failed")

            # Start audio pipeline
            if not self.audio_pipeline.start():
                logger.error("audio_pipeline_start_failed")
                self._set_state(OrchestratorState.ERROR)
                return False

            # Start wake word detector
            self.wake_word_detector.start()

            # Update state
            self._running = True
            self._set_state(OrchestratorState.LISTENING_FOR_WAKE_WORD)

            logger.info("orchestrator_started")
            return True

    def stop(self):
        """Stop the voice orchestrator."""
        logger.info("stopping_orchestrator")

        with self._running_lock:
            if not self._running:
                return

            self._running = False

            # Signal shutdown to any in-flight timer callbacks before
            # cancelling the timer, so a callback already executing in
            # another thread sees the flag and exits cleanly.
            self._shutdown_event.set()

            # Cancel wake ack timer if running
            with self._wake_ack_lock:
                self._wake_ack_pending = False
                if self._wake_ack_timer:
                    self._wake_ack_timer.cancel()
                    self._wake_ack_timer = None

            # Stop components
            self.wake_word_detector.stop()
            self.audio_pipeline.stop()

            # WebSocket cleanup - handled by __del__ or context manager
            # Don't try to await from sync context; coroutine warning is harmless
            self.websocket = None

            self._set_state(OrchestratorState.IDLE)
            logger.info("orchestrator_stopped")

    def is_running(self) -> bool:
        """Check if orchestrator is running."""
        with self._running_lock:
            return self._running

    def _dispatch_coroutine(self, coro) -> None:
        """
        Schedule a coroutine on the captured event loop from any thread.

        Audio pipeline callbacks run in background threads, so we cannot
        use asyncio.run() (which creates a second loop) or asyncio.create_task()
        (which requires an async context). run_coroutine_threadsafe() is the
        correct bridge between threads and a running event loop.
        """
        if self._event_loop and self._event_loop.is_running():
            asyncio.run_coroutine_threadsafe(coro, self._event_loop)
        else:
            # Fallback for unit-test scenarios where no loop is running yet.
            try:
                asyncio.run(coro)
            except Exception as e:
                logger.error("dispatch_coroutine_error", error=str(e))
                self._set_state(OrchestratorState.LISTENING)
    
    # =========================================================================
    # Audio frame callback
    # =========================================================================
    
    def _on_audio_frame(self, audio_frame: np.ndarray, sample_rate: int = 16000):
        """
        Handle incoming audio frame from pipeline.
        
        Passes audio to wake word detector when listening for wake word.
        Uses lazy resampling - detector converts to its required rate.
        
        Args:
            audio_frame: Audio samples at native sample rate
            sample_rate: Sample rate of the audio (default 16kHz)
        """
        if not self._running:
            return
        
        # Only process for wake word detection when in IDLE/LISTENING_FOR_WAKE_WORD state
        if self._state in (OrchestratorState.IDLE, OrchestratorState.LISTENING_FOR_WAKE_WORD):
            self.wake_word_detector.process_frame(audio_frame, sample_rate=sample_rate)
    
    # =========================================================================
    # Wake word callbacks
    # =========================================================================
    
    def on_wake_word_detected(self, text: str):
        """
        Handle wake word detection.
        
        Called when wake word is detected in audio stream.
        If acknowledgement is enabled, sends ack to OpenClaw and waits for response.
        Otherwise, transitions directly to LISTENING state for speech capture.
        
        Args:
            text: Text that matched wake word
        """
        logger.info("wake_word_detected", text=text)
        
        if not self._running:
            return
        
        self._stats.wake_word_detections += 1
        
        # Check if wake word acknowledgement is enabled
        ack_config = self.config.bridge.acknowledgement
        if ack_config.enabled:
            self._handle_wake_word_ack(text, ack_config)
        else:
            # Skip acknowledgement, go directly to listening
            self._set_state(OrchestratorState.LISTENING)
        
        # Notify any registered listeners
        if hasattr(self, 'on_wake_word'):
            try:
                self.on_wake_word(text)
            except Exception as e:
                logger.error("on_wake_word_callback_error", error=str(e))
    
    def _handle_wake_word_ack(self, wake_word_text: str, ack_config):
        """
        Handle wake word acknowledgement flow.
        
        Sends acknowledgement to OpenClaw and waits for response.
        Falls back to local TTS if OpenClaw doesn't respond in time.
        
        Args:
            wake_word_text: The detected wake word text
            ack_config: WakeAcknowledgementConfig instance
        """
        logger.info(
            "handling_wake_word_ack",
            response_phrase=ack_config.response_phrase,
            timeout_ms=ack_config.timeout_ms
        )
        
        self._set_state(OrchestratorState.WAKE_WORD_ACK)
        
        with self._wake_ack_lock:
            self._wake_ack_pending = True
        
        # Start timeout timer for fallback
        self._wake_ack_timer = threading.Timer(
            ack_config.timeout_ms / 1000.0,
            self._on_wake_ack_timeout,
            args=[ack_config]
        )
        self._wake_ack_timer.start()
        
        # Check API mode and send acknowledgement
        if self.config.openclaw.api_mode == "http" and self.http_client:
            # Use HTTP API
            async def send_http_ack():
                try:
                    response = await self.http_client.send_wake_ack(wake_word_text)
                    if response:
                        logger.info("wake_ack_response_received", response=response)
                        self._on_wake_ack_response(response)
                    else:
                        logger.warning("empty_wake_ack_response")
                        self._on_wake_ack_timeout(ack_config)
                except OpenClawHTTPTimeoutError:
                    logger.warning("wake_ack_http_timeout")
                    self._on_wake_ack_timeout(ack_config)
                except OpenClawHTTPError as e:
                    logger.error("wake_ack_http_error", error=str(e))
                    self._on_wake_ack_timeout(ack_config)
            
            self._dispatch_coroutine(send_http_ack())
        else:
            # Use WebSocket (legacy mode)
            async def send_ack():
                if self.websocket and self.websocket.is_connected:
                    success = await self.websocket.send_wake_word_ack(wake_word_text)
                    if not success:
                        logger.warning("failed_to_send_wake_word_ack")
                        self._on_wake_ack_timeout(ack_config)
                else:
                    logger.warning("websocket_not_connected_for_wake_ack")
                    self._on_wake_ack_timeout(ack_config)

            self._dispatch_coroutine(send_ack())
    
    def _on_wake_ack_timeout(self, ack_config):
        """
        Handle timeout waiting for wake word ack response from OpenClaw.

        Falls back to local TTS if enabled, otherwise proceeds to listening state.
        Guards against being called after stop() — the timer may fire in a
        thread that is already racing with shutdown.

        Args:
            ack_config: WakeAcknowledgementConfig instance
        """
        if self._shutdown_event.is_set():
            return

        with self._wake_ack_lock:
            if not self._wake_ack_pending:
                # Already handled
                return
            self._wake_ack_pending = False

        logger.info("wake_ack_timeout_using_fallback")

        # Cancel timer if still running
        if self._wake_ack_timer:
            self._wake_ack_timer.cancel()
            self._wake_ack_timer = None

        if ack_config.fallback_to_local_tts:
            self._speak_acknowledgement(ack_config.response_phrase)
        else:
            self._set_state(OrchestratorState.LISTENING)

    def _speak_acknowledgement(self, phrase: str):
        """
        Speak the acknowledgement phrase via TTS.

        Dispatches TTS synthesis to a thread pool so the event loop is never
        blocked by Piper inference (50-200ms).

        Args:
            phrase: The phrase to speak
        """
        logger.info("speaking_acknowledgement", phrase=phrase)

        async def _async_speak():
            try:
                # Run blocking TTS synthesis off the event loop
                audio_data = await asyncio.to_thread(self.tts_engine.speak, phrase)
                if len(audio_data) > 0:
                    self.audio_pipeline.play_audio(audio_data, sample_rate=22050)
                    self._set_state(OrchestratorState.SPEAKING)
                    # Transition to listening after audio starts (pipeline
                    # state callback will also handle this on playback end)
                    await asyncio.sleep(0.5)
                    if self._state == OrchestratorState.SPEAKING:
                        self._set_state(OrchestratorState.LISTENING)
                else:
                    logger.warning("empty_acknowledgement_audio")
                    self._set_state(OrchestratorState.LISTENING)
            except Exception as e:
                logger.error("speak_acknowledgement_error", error=str(e))
                self._set_state(OrchestratorState.LISTENING)

        self._dispatch_coroutine(_async_speak())
    
    def _on_wake_ack_response(self, response_text: str):
        """
        Handle acknowledgement response from OpenClaw.
        
        Called when OpenClaw responds to wake_word_ack with a voice_response.
        
        Args:
            response_text: The acknowledgement phrase from OpenClaw
        """
        with self._wake_ack_lock:
            if not self._wake_ack_pending:
                # Already handled (timeout occurred)
                return
            self._wake_ack_pending = False
        
        # Cancel timeout timer
        if self._wake_ack_timer:
            self._wake_ack_timer.cancel()
            self._wake_ack_timer = None
        
        logger.info("wake_ack_response_received", response_text=response_text)
        
        # Speak the response
        self._speak_acknowledgement(response_text)
    
    # =========================================================================
    # Speech detection callbacks
    # =========================================================================
    
    def _on_speech_segment(self, segment: SpeechSegment):
        """
        Handle completed speech segment.
        
        Called when VAD detects end of speech.
        Transcribes the segment and sends to OpenClaw.
        
        Args:
            segment: Completed speech segment
        """
        if not self._running:
            return
        
        # Only process if in LISTENING state (after wake word)
        if self._state != OrchestratorState.LISTENING:
            return
        
        logger.info(
            "speech_segment_complete",
            duration_ms=segment.duration_ms
        )
        
        # Get audio data
        audio_data = segment.audio_data
        
        # Transition to PROCESSING
        self._set_state(OrchestratorState.PROCESSING)
        
        # Transcribe audio
        try:
            text, confidence = self.stt_engine.transcribe(audio_data)
            logger.info("transcription_complete", text=text, confidence=confidence)
            
            # Handle transcription
            self._on_stt_complete(text)
            
        except Exception as e:
            logger.error("transcription_error", error=str(e))
            self._set_state(OrchestratorState.LISTENING)
    
    def _on_stt_complete(self, text: str):
        """
        Handle STT transcription completion.
        
        Sends transcribed text to OpenClaw.
        
        Args:
            text: Transcribed text
        """
        if not self._running:
            return
        
        self._stats.completed_transcriptions += 1
        
        if not text.strip():
            logger.warning("empty_transcription")
            # Go back to listening
            if self.state == OrchestratorState.PROCESSING:
                self._set_state(OrchestratorState.LISTENING)
            return
        
        logger.info("transcription_complete", text=text)
        
        # Transition to PROCESSING state
        self._set_state(OrchestratorState.PROCESSING)
        
        # Send to OpenClaw
        async def send_to_openclaw():
            # Check API mode
            if self.config.openclaw.api_mode == "http" and self.http_client:
                try:
                    response = await self.http_client.send_message(text)
                    if response:
                        logger.info("http_message_sent_success", response=response)
                        # Process the response from OpenClaw
                        response_text = response.get("text", "") if isinstance(response, dict) else str(response)
                        self._on_response_received(response_text)
                    else:
                        logger.error("empty_http_response")
                        self._set_state(OrchestratorState.LISTENING)
                except OpenClawHTTPTimeoutError:
                    logger.error("http_message_timeout")
                    self._set_state(OrchestratorState.LISTENING)
                except OpenClawHTTPError as e:
                    logger.error("http_message_error", error=str(e))
                    self._set_state(OrchestratorState.LISTENING)
            else:
                # Use WebSocket (legacy mode)
                success = await self.websocket.send_voice_input(text)
                if not success:
                    logger.error("failed_to_send_to_openclaw")
                    self._set_state(OrchestratorState.LISTENING)
        
        self._dispatch_coroutine(send_to_openclaw())
    
    # =========================================================================
    # WebSocket callbacks
    # =========================================================================
    
    def _on_websocket_message(self, message: dict):
        """
        Handle incoming WebSocket messages.
        
        Processes OpenClaw responses and triggers TTS.
        
        Args:
            message: Incoming message dict
        """
        if not self._running:
            return
        
        msg_type = message.get("type", "")
        
        if msg_type == "voice_response":
            # Check if this is a wake word ack response
            with self._wake_ack_lock:
                if self._wake_ack_pending and self._state == OrchestratorState.WAKE_WORD_ACK:
                    # This is the wake word acknowledgement response
                    response_text = message.get("text", "")
                    if response_text:
                        self._on_wake_ack_response(response_text)
                        return
            
            # Process voice response
            response_text = message.get("text", "")
            if response_text:
                self._on_response_received(response_text)
        
        elif msg_type == "control":
            # Handle control messages
            action = message.get("action", "")
            if action == "interrupt":
                self._handle_barge_in()
    
    def _on_response_received(self, text: str):
        """
        Handle response from OpenClaw.

        Dispatches TTS synthesis to a thread pool so the event loop is never
        blocked by Piper inference (50-200ms).

        Args:
            text: Response text to speak
        """
        logger.info("response_received", text=text)
        self._stats.completed_responses += 1

        async def _async_respond():
            try:
                audio_data = await asyncio.to_thread(self.tts_engine.speak, text)
                if len(audio_data) > 0:
                    self.audio_pipeline.play_audio(audio_data, sample_rate=22050)
                    self._set_state(OrchestratorState.SPEAKING)
                else:
                    self._set_state(OrchestratorState.LISTENING)
            except Exception as e:
                logger.error("response_tts_error", error=str(e))
                self._set_state(OrchestratorState.LISTENING)

        self._dispatch_coroutine(_async_respond())
    
    def _on_websocket_connect(self):
        """Handle WebSocket connection."""
        logger.info("websocket_connected")
        # Resume listening when connected
        if self.state == OrchestratorState.IDLE:
            self._set_state(OrchestratorState.LISTENING_FOR_WAKE_WORD)
    
    def _on_websocket_disconnect(self):
        """Handle WebSocket disconnection."""
        logger.info("websocket_disconnected")
    
    # =========================================================================
    # TTS callbacks
    # =========================================================================
    
    def _on_tts_generated(self, audio_data: np.ndarray):
        """
        Handle TTS audio generation.
        
        Queue audio for playback.
        
        Args:
            audio_data: Generated audio samples
        """
        if len(audio_data) == 0:
            logger.warning("empty_tts_audio")
            return
        
        # Audio pipeline starts playback automatically
        # State transitions happen in play_audio
    
    def _on_pipeline_state_change(self, old_state: PipelineState, new_state: PipelineState):
        """
        Handle audio pipeline state changes.
        
        Synchronizes orchestrator state with pipeline state.
        
        Args:
            old_state: Previous pipeline state
            new_state: New pipeline state
        """
        # Pipeline state -> Orchestrator state mapping
        pipeline_to_orchestrator = {
            PipelineState.IDLE: OrchestratorState.IDLE,
            PipelineState.LISTENING: OrchestratorState.LISTENING,
            PipelineState.PROCESSING: OrchestratorState.PROCESSING,
            PipelineState.SPEAKING: OrchestratorState.SPEAKING,
            PipelineState.ERROR: OrchestratorState.ERROR,
        }
        
        if new_state in pipeline_to_orchestrator:
            orchestrator_state = pipeline_to_orchestrator[new_state]
            
            # Don't override if we're in a more specific state
            with self._state_lock:
                if self._state == OrchestratorState.IDLE:
                    self._set_state(orchestrator_state)
    
    # =========================================================================
    # Barge-in handling
    # =========================================================================
    
    def _handle_barge_in(self):
        """
        Handle interruption (user speaks while AI is speaking).
        
        Stops current TTS playback and returns to listening state.
        """
        if not self._barge_in_enabled:
            return
        
        with self._is_speaking_lock:
            if not self._is_speaking:
                return
        
        logger.info("barge_in_detected")
        
        # Stop playback
        self.audio_pipeline.stop_playback_immediate()
        
        # Update stats
        self._stats.barge_in_count += 1
        
        # Return to listening state
        self._set_state(OrchestratorState.LISTENING)
        
        # Reset speaking flag
        self._is_speaking = False
    
    def enable_barge_in(self, enabled: bool = True):
        """Enable or disable barge-in capability."""
        self._barge_in_enabled = enabled
        self.audio_pipeline.enable_barge_in(enabled)
        logger.info("barge_in_toggled", enabled=enabled)
    
    def barge_in_enabled(self) -> bool:
        """Check if barge-in is enabled."""
        return self._barge_in_enabled
    
    def start_speaking(self):
        """Mark that AI is currently speaking."""
        with self._is_speaking_lock:
            self._is_speaking = True
            self._set_state(OrchestratorState.SPEAKING)
    
    def stop_speaking(self):
        """Mark that AI has finished speaking."""
        with self._is_speaking_lock:
            self._is_speaking = False
            if self._running:
                self._set_state(OrchestratorState.LISTENING)
    
    def set_on_wake_word(self, callback: Callable[[str], None]):
        """Register callback for wake word detection."""
        self.on_wake_word = callback
        logger.info("on_wake_word_callback_registered")
    
    def set_on_speech_end(self, callback: Callable[[str], None]):
        """Register callback for completed speech transcription."""
        original_handler = self._on_stt_complete
        
        def handler(text: str):
            original_handler(text)
            try:
                callback(text)
            except Exception as e:
                logger.error("on_speech_end_callback_error", error=str(e))
        
        self.stt_engine.set_on_transcription(handler)
        logger.info("on_speech_end_callback_registered")
    
    def set_on_response(self, callback: Callable[[str], None]):
        """Register callback for AI response text."""
        original_handler = self._on_response_received
        
        def handler(text: str):
            try:
                callback(text)
            except Exception as e:
                logger.error("on_response_callback_error", error=str(e))
            original_handler(text)
        
        self._on_response_received = handler
        logger.info("on_response_callback_registered")
    
    def on_error(self, error: Exception):
        """
        Handle orchestrator errors.
        
        Captures errors to the bug tracking system with context.
        
        Args:
            error: The exception that occurred
        """
        logger.error("orchestrator_error", error=str(error), error_type=type(error).__name__)
        
        # Capture to bug tracker via ErrorCapture
        # Use run() with a function that raises the error to leverage ErrorCapture's tracking
        def raise_error():
            raise error
        
        self.error_capture.run(raise_error, context=f"State: {self._state.value}")
    
    def __enter__(self):
        """Context manager entry."""
        self.start()
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit."""
        self.stop()
        return False
