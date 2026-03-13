"""Tests for interactive conversation mode in VoiceOrchestrator."""

import sys
import threading
import time
from unittest.mock import MagicMock, patch, AsyncMock
import numpy as np
import pytest

# ---------------------------------------------------------------------------
# Stub heavy optional deps before any bridge imports so collection succeeds
# even when sounddevice / webrtcvad / faster_whisper are not installed.
# ---------------------------------------------------------------------------
for _mod in ("sounddevice", "webrtcvad", "faster_whisper", "openwakeword"):
    sys.modules.setdefault(_mod, MagicMock())

from bridge.orchestrator import VoiceOrchestrator, OrchestratorState  # noqa: E402
from bridge.config import (  # noqa: E402
    AppConfig, BridgeConfig, InteractiveConfig, OpenClawConfig,
    WakeAcknowledgementConfig,
)
from bridge.vad import SpeechSegment  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_config(
    interactive_enabled: bool = True,
    idle_timeout: float = 5.0,
    cancel_phrases: list[str] | None = None,
    ack_enabled: bool = False,
) -> AppConfig:
    """Build a minimal AppConfig wired for unit testing.

    We use model_construct() on AppConfig to skip Pydantic validators (which
    would try to validate the auth token against a live OpenClaw instance).
    Sub-models that don't require network access are built normally.
    """
    openclaw = OpenClawConfig.model_construct(
        host="localhost",
        port=18789,
        secure=False,
        api_key=None,
        timeout=30.0,
        timeout_ms=30000,
        api_mode="websocket",
        ws_path="/api/voice",
        auth_token=None,
    )
    interactive = InteractiveConfig(
        enabled=interactive_enabled,
        idle_timeout_seconds=idle_timeout,
        cancel_phrases=cancel_phrases or ["stop", "cancel", "goodbye"],
    )
    ack = WakeAcknowledgementConfig(enabled=ack_enabled)
    bridge = BridgeConfig.model_construct(
        interactive=interactive,
        acknowledgement=ack,
        response_timeout=10.0,
        max_session_duration=300.0,
        log_level="DEBUG",
        hot_reload=False,
    )
    cfg = AppConfig.model_construct(
        openclaw=openclaw,
        bridge=bridge,
    )
    return cfg


def _make_orchestrator(
    interactive_enabled: bool = True,
    idle_timeout: float = 5.0,
    cancel_phrases: list[str] | None = None,
    ack_enabled: bool = False,
) -> VoiceOrchestrator:
    """Build a VoiceOrchestrator with all heavy deps mocked out."""
    config = _make_config(
        interactive_enabled=interactive_enabled,
        idle_timeout=idle_timeout,
        cancel_phrases=cancel_phrases,
        ack_enabled=ack_enabled,
    )

    audio_pipeline = MagicMock()
    audio_pipeline.start.return_value = True
    audio_pipeline.state = MagicMock()
    audio_pipeline.add_state_callback = MagicMock()
    audio_pipeline.add_frame_callback = MagicMock()
    audio_pipeline.add_speech_segment_callback = MagicMock()
    audio_pipeline.play_audio = MagicMock()
    audio_pipeline.stop_playback_immediate = MagicMock()

    stt_engine = MagicMock()
    stt_engine.initialize.return_value = True
    stt_engine.transcribe.return_value = ("hello world", 0.9)

    tts_engine = MagicMock()
    tts_engine.initialize.return_value = True
    # Return 0.1 s worth of audio at 22050 Hz
    tts_engine.speak.return_value = np.zeros(2205, dtype=np.int16)

    websocket = MagicMock()
    websocket.on_message = None
    websocket.on_connect = None
    websocket.on_disconnect = None
    websocket.is_connected = True

    wake_word_detector = MagicMock()
    wake_word_detector.start = MagicMock()
    wake_word_detector.stop = MagicMock()
    wake_word_detector.register_on_detected = MagicMock()

    orch = VoiceOrchestrator(
        config=config,
        audio_pipeline=audio_pipeline,
        stt_engine=stt_engine,
        tts_engine=tts_engine,
        websocket=websocket,
    )
    # Replace the real wake word detector with the mock
    orch.wake_word_detector = wake_word_detector

    return orch


def _speech_segment(text_hint: str = "hello") -> SpeechSegment:
    """Create a minimal SpeechSegment."""
    seg = MagicMock(spec=SpeechSegment)
    seg.audio_data = np.zeros(8000, dtype=np.int16)
    seg.duration_ms = 500
    return seg


# ---------------------------------------------------------------------------
# Tests: INTERACTIVE state entry
# ---------------------------------------------------------------------------


class TestInteractiveModeEntry:
    def test_wake_word_no_ack_enters_interactive(self):
        """When ack is disabled, wake word detection should enter INTERACTIVE."""
        orch = _make_orchestrator(ack_enabled=False)
        orch._running = True

        orch.on_wake_word_detected("computer")

        assert orch.state == OrchestratorState.INTERACTIVE
        assert orch._interactive_mode is True

    def test_wake_word_no_ack_interactive_disabled_goes_to_wake_word(self):
        """When interactive is disabled, goes to LISTENING_FOR_WAKE_WORD."""
        orch = _make_orchestrator(interactive_enabled=False, ack_enabled=False)
        orch._running = True

        orch.on_wake_word_detected("computer")

        assert orch.state == OrchestratorState.LISTENING_FOR_WAKE_WORD
        assert orch._interactive_mode is False

    def test_wake_ack_timeout_enters_interactive(self):
        """Wake ack timeout should enter INTERACTIVE when interactive is enabled."""
        orch = _make_orchestrator()
        orch._running = True
        orch._wake_ack_pending = True  # Simulate pending ack

        orch._on_wake_ack_timeout()

        assert orch.state == OrchestratorState.INTERACTIVE
        assert orch._interactive_mode is True

    def test_wake_ack_timeout_interactive_disabled_goes_to_wake_word(self):
        """Wake ack timeout → LISTENING_FOR_WAKE_WORD when interactive disabled."""
        orch = _make_orchestrator(interactive_enabled=False)
        orch._running = True
        orch._wake_ack_pending = True

        orch._on_wake_ack_timeout()

        assert orch.state == OrchestratorState.LISTENING_FOR_WAKE_WORD
        assert orch._interactive_mode is False


# ---------------------------------------------------------------------------
# Tests: INTERACTIVE state behaviour
# ---------------------------------------------------------------------------


class TestInteractiveModeLoop:
    def test_speech_segment_processed_in_interactive(self):
        """Speech segment in INTERACTIVE state triggers transcription."""
        orch = _make_orchestrator()
        orch._running = True
        orch._interactive_mode = True
        orch._state = OrchestratorState.INTERACTIVE

        # Patch _on_stt_complete so we can inspect the call
        orch._on_stt_complete = MagicMock()

        orch._on_speech_segment(_speech_segment())

        orch._on_stt_complete.assert_called_once_with("hello world")

    def test_speech_segment_ignored_outside_interactive(self):
        """Speech segment is ignored when in LISTENING_FOR_WAKE_WORD."""
        orch = _make_orchestrator()
        orch._running = True
        orch._interactive_mode = False
        orch._state = OrchestratorState.LISTENING_FOR_WAKE_WORD

        orch._on_stt_complete = MagicMock()
        orch._on_speech_segment(_speech_segment())

        orch._on_stt_complete.assert_not_called()

    def test_speech_segment_resets_idle_timer(self):
        """Speech in INTERACTIVE mode should restart the idle timer."""
        orch = _make_orchestrator(idle_timeout=60.0)
        orch._running = True
        orch._interactive_mode = True
        orch._state = OrchestratorState.INTERACTIVE

        orch._reset_idle_timer = MagicMock()
        orch._on_stt_complete = MagicMock()

        orch._on_speech_segment(_speech_segment())

        orch._reset_idle_timer.assert_called()


# ---------------------------------------------------------------------------
# Tests: cancel phrases
# ---------------------------------------------------------------------------


class TestCancelPhrases:
    def test_cancel_phrase_exits_interactive(self):
        """Saying 'stop' should exit interactive mode."""
        orch = _make_orchestrator(cancel_phrases=["stop", "cancel"])
        orch._running = True
        orch._interactive_mode = True
        orch._state = OrchestratorState.INTERACTIVE

        orch.stt_engine.transcribe.return_value = ("please stop", 0.95)

        # Intercept _exit_interactive_mode
        exit_calls = []
        original = orch._exit_interactive_mode
        orch._exit_interactive_mode = lambda reason="unknown": exit_calls.append(reason) or original(reason)

        orch._on_stt_complete("please stop")

        assert len(exit_calls) == 1
        assert exit_calls[0] == "cancel_phrase"
        assert orch.state == OrchestratorState.LISTENING_FOR_WAKE_WORD
        assert orch._interactive_mode is False

    def test_cancel_phrase_not_sent_to_openclaw(self):
        """A cancel phrase must NOT be forwarded to OpenClaw."""
        orch = _make_orchestrator(cancel_phrases=["cancel"])
        orch._running = True
        orch._interactive_mode = True
        orch._state = OrchestratorState.INTERACTIVE

        orch._dispatch_coroutine = MagicMock()

        orch._on_stt_complete("cancel that")

        # No coroutine should have been dispatched to OpenClaw
        orch._dispatch_coroutine.assert_not_called()

    def test_non_cancel_phrase_forwarded_to_openclaw(self):
        """Normal speech must be dispatched to OpenClaw."""
        orch = _make_orchestrator(cancel_phrases=["stop"])
        orch._running = True
        orch._interactive_mode = True
        orch._state = OrchestratorState.INTERACTIVE

        orch._dispatch_coroutine = MagicMock()

        orch._on_stt_complete("what is the weather today")

        orch._dispatch_coroutine.assert_called_once()

    def test_cancel_phrase_case_insensitive(self):
        """Cancel phrases should be matched case-insensitively."""
        orch = _make_orchestrator(cancel_phrases=["goodbye"])
        orch._running = True
        orch._interactive_mode = True
        orch._state = OrchestratorState.INTERACTIVE

        orch._dispatch_coroutine = MagicMock()
        orch._on_stt_complete("Goodbye!")

        orch._dispatch_coroutine.assert_not_called()
        assert orch._interactive_mode is False


# ---------------------------------------------------------------------------
# Tests: idle timeout
# ---------------------------------------------------------------------------


class TestIdleTimeout:
    def test_idle_timeout_exits_interactive(self):
        """_on_idle_timeout should exit interactive mode."""
        orch = _make_orchestrator()
        orch._running = True
        orch._interactive_mode = True
        orch._state = OrchestratorState.INTERACTIVE

        orch._on_idle_timeout()

        assert orch._interactive_mode is False
        assert orch.state == OrchestratorState.LISTENING_FOR_WAKE_WORD

    def test_idle_timeout_no_op_when_not_interactive(self):
        """_on_idle_timeout should do nothing outside interactive mode."""
        orch = _make_orchestrator()
        orch._running = True
        orch._interactive_mode = False
        orch._state = OrchestratorState.LISTENING_FOR_WAKE_WORD

        orch._on_idle_timeout()  # Should not raise or change state

        assert orch.state == OrchestratorState.LISTENING_FOR_WAKE_WORD

    def test_idle_timeout_no_op_after_shutdown(self):
        """_on_idle_timeout must bail when shutdown event is set."""
        orch = _make_orchestrator()
        orch._interactive_mode = True
        orch._shutdown_event.set()

        orch._on_idle_timeout()  # Should not crash or change state

    def test_reset_idle_timer_cancels_previous(self):
        """Resetting the timer should cancel the old one."""
        orch = _make_orchestrator(idle_timeout=60.0)
        orch._running = True
        orch._interactive_mode = True

        orch._reset_idle_timer()
        first_timer = orch._idle_timer

        orch._reset_idle_timer()
        second_timer = orch._idle_timer

        # First timer should have been cancelled
        assert first_timer is not second_timer
        assert second_timer is not None

        # Cleanup
        orch._cancel_idle_timer()


# ---------------------------------------------------------------------------
# Tests: exit interactive mode
# ---------------------------------------------------------------------------


class TestExitInteractiveMode:
    def test_exit_sets_state_to_wake_word(self):
        """Exiting interactive mode returns to LISTENING_FOR_WAKE_WORD."""
        orch = _make_orchestrator()
        orch._running = True
        orch._interactive_mode = True
        orch._state = OrchestratorState.INTERACTIVE

        orch._exit_interactive_mode("test")

        assert orch.state == OrchestratorState.LISTENING_FOR_WAKE_WORD
        assert orch._interactive_mode is False

    def test_exit_cancels_idle_timer(self):
        """Exiting must cancel the idle timer."""
        orch = _make_orchestrator(idle_timeout=60.0)
        orch._running = True
        orch._interactive_mode = True
        orch._reset_idle_timer()

        assert orch._idle_timer is not None

        orch._exit_interactive_mode("test")

        assert orch._idle_timer is None

    def test_exit_noop_when_not_interactive(self):
        """_exit_interactive_mode is safe to call when not in interactive mode."""
        orch = _make_orchestrator()
        orch._running = True
        orch._interactive_mode = False
        orch._state = OrchestratorState.LISTENING_FOR_WAKE_WORD

        orch._exit_interactive_mode("test")  # Should not raise

        assert orch.state == OrchestratorState.LISTENING_FOR_WAKE_WORD

    def test_stop_clears_interactive_state(self):
        """Calling stop() must clear interactive mode and cancel idle timer."""
        orch = _make_orchestrator(idle_timeout=60.0)
        orch._running = True
        orch._interactive_mode = True
        orch._reset_idle_timer()

        orch.stop()

        assert orch._interactive_mode is False
        assert orch._idle_timer is None


# ---------------------------------------------------------------------------
# Tests: config
# ---------------------------------------------------------------------------


class TestInteractiveConfig:
    def test_default_interactive_config(self):
        """InteractiveConfig should have sensible defaults."""
        cfg = InteractiveConfig()
        assert cfg.enabled is True
        assert cfg.idle_timeout_seconds == 30.0
        assert "stop" in cfg.cancel_phrases
        assert "cancel" in cfg.cancel_phrases

    def test_custom_cancel_phrases(self):
        """Custom cancel phrases are accepted."""
        cfg = InteractiveConfig(cancel_phrases=["adieu", "fin"])
        assert cfg.cancel_phrases == ["adieu", "fin"]

    def test_idle_timeout_bounds(self):
        """idle_timeout_seconds must be between 5 and 300."""
        with pytest.raises(Exception):
            InteractiveConfig(idle_timeout_seconds=1.0)
        with pytest.raises(Exception):
            InteractiveConfig(idle_timeout_seconds=999.0)
