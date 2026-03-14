"""Bridge Integration Test for Voice Bridge Installation.

Tests the actual VoiceOrchestrator stack — AudioPipeline, VAD, wake-word
detector, HTTP client, TTS — rather than individual components in isolation.
This gives the installer a ground-truth answer about whether the complete
bridge will work on this machine.

Two levels of testing are available:

1. ``BridgeTester.test_startup()``
   Starts the bridge and immediately stops it.  Verifies that every
   component (audio devices, STT model, TTS engine, wake-word model)
   initialises without crashing.  No user interaction needed.

2. ``BridgeTester.run(...)``
   Starts the bridge and waits for the user to say the wake word.  After
   detection the bridge handles the full OpenClaw ACK → TTS response flow
   using the same code path as production.  Requires user interaction.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, List, Optional

import structlog

logger = structlog.get_logger()


class BridgeTestPhase(Enum):
    """Phase reached when the test result was captured."""
    NOT_STARTED = "not_started"        # Config / import failure before anything ran
    STARTUP_FAILED = "startup_failed"  # orchestrator.start() returned False
    LISTENING = "listening"            # Bridge started; waiting for wake word
    WAKE_WORD = "wake_word"            # Wake word detected by the real pipeline
    INTERACTIVE = "interactive"        # Entered interactive mode (ACK done / timed out)
    SPEAKING = "speaking"              # TTS is actively playing
    COMPLETE = "complete"              # Full flow finished


@dataclass
class BridgeTestResult:
    """Result of a bridge integration test."""
    phase_reached: BridgeTestPhase
    success: bool
    message: str
    details: Optional[str] = None
    startup_ok: bool = False
    wake_word_detected: bool = False
    wake_word_text: Optional[str] = None
    openclaw_responded: bool = False
    tts_played: bool = False
    error: Optional[Exception] = None
    duration_ms: int = 0

    @property
    def passed(self) -> bool:
        return self.success

    @property
    def failed(self) -> bool:
        return not self.success

    def summary_lines(self) -> List[str]:
        """Return human-readable lines suitable for printing."""
        lines = [
            f"  Bridge startup   : {'✅ OK' if self.startup_ok else '❌ FAILED'}",
        ]
        if self.startup_ok:
            ww = f"✅ '{self.wake_word_text}'" if self.wake_word_detected else "❌ not detected"
            lines.append(f"  Wake word        : {ww}")
        if self.wake_word_detected:
            oc = "✅ responded" if self.openclaw_responded else "⏰ timed out (advisory)"
            lines.append(f"  OpenClaw ACK     : {oc}")
            tts = "✅ played" if self.tts_played else "⏸  skipped (no ACK response)"
            lines.append(f"  TTS playback     : {tts}")
        if self.duration_ms:
            lines.append(f"  Duration         : {self.duration_ms}ms")
        return lines


class BridgeTester:
    """Run the real VoiceOrchestrator to verify the bridge end-to-end.

    The tester creates an ``Orchestrator`` with the live configuration,
    starts it (which opens the audio devices and loads all models), runs
    the test, then stops it cleanly — leaving the system exactly as found.

    Example — startup-only (no user interaction)::

        result = BridgeTester().test_startup()

    Example — full wake-word flow (interactive)::

        tester = BridgeTester(wake_word_timeout_s=20)
        result = tester.run(
            on_listening=lambda: print("Say the wake word now!"),
            on_wake_detected=lambda t: print(f"Detected: {t!r}"),
        )
    """

    def __init__(
        self,
        wake_word_timeout_s: float = 20.0,
        response_timeout_s: float = 10.0,
    ) -> None:
        self.wake_word_timeout_s = wake_word_timeout_s
        self.response_timeout_s = response_timeout_s
        self._orchestrator = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def test_startup(self) -> BridgeTestResult:
        """Start the bridge, wait briefly, then stop it.

        Verifies that every component initialises without error:
        audio I/O devices, STT model, TTS engine, wake-word detector.
        No user interaction is required.
        """
        start = time.time()

        config, err = self._load_config()
        if err:
            return BridgeTestResult(
                phase_reached=BridgeTestPhase.NOT_STARTED,
                success=False,
                message=f"Config load failed: {err}",
                error=err,
                duration_ms=int((time.time() - start) * 1000),
            )

        orchestrator, err = self._create_orchestrator(config)
        if err:
            return BridgeTestResult(
                phase_reached=BridgeTestPhase.NOT_STARTED,
                success=False,
                message=f"VoiceOrchestrator unavailable: {err}",
                error=err,
                duration_ms=int((time.time() - start) * 1000),
            )

        try:
            started = orchestrator.start()
        except Exception as exc:
            return BridgeTestResult(
                phase_reached=BridgeTestPhase.STARTUP_FAILED,
                success=False,
                message=f"orchestrator.start() raised: {exc}",
                error=exc,
                duration_ms=int((time.time() - start) * 1000),
            )

        if not started:
            return BridgeTestResult(
                phase_reached=BridgeTestPhase.STARTUP_FAILED,
                success=False,
                message="Orchestrator failed to start (audio device or pipeline error)",
                duration_ms=int((time.time() - start) * 1000),
            )

        # Brief warm-up so the audio pipeline settles before we stop.
        time.sleep(1.5)
        self._safe_stop(orchestrator)

        return BridgeTestResult(
            phase_reached=BridgeTestPhase.LISTENING,
            success=True,
            message="Bridge started and stopped cleanly — all components initialised OK",
            startup_ok=True,
            duration_ms=int((time.time() - start) * 1000),
        )

    def run(
        self,
        on_started: Optional[Callable[[], None]] = None,
        on_listening: Optional[Callable[[], None]] = None,
        on_wake_detected: Optional[Callable[[str], None]] = None,
        on_ack_complete: Optional[Callable[[bool], None]] = None,
        on_speaking: Optional[Callable[[], None]] = None,
        on_complete: Optional[Callable[[], None]] = None,
        on_timeout: Optional[Callable[[], None]] = None,
    ) -> BridgeTestResult:
        """Start the bridge and wait for a full wake-word → ACK → TTS cycle.

        The caller is responsible for telling the user to speak the wake word
        (typically via the ``on_listening`` callback).

        Callback sequence on success:
            on_started → on_listening → on_wake_detected → on_ack_complete
            → on_speaking → on_complete

        Args:
            on_started:      Bridge started; components ready.
            on_listening:    Ready to hear the wake word (prompt the user now).
            on_wake_detected: Wake word heard; text passed as argument.
            on_ack_complete: ACK cycle done.  Argument is True if OpenClaw
                             responded with a phrase, False if it timed out.
            on_speaking:     TTS playback has begun.
            on_complete:     TTS playback finished; bridge stopping.
            on_timeout:      Wake word not heard within the timeout.
        """
        start = time.time()

        config, err = self._load_config()
        if err:
            return BridgeTestResult(
                phase_reached=BridgeTestPhase.NOT_STARTED,
                success=False,
                message=f"Config load failed: {err}",
                error=err,
                duration_ms=int((time.time() - start) * 1000),
            )

        orchestrator, err = self._create_orchestrator(config)
        if err:
            return BridgeTestResult(
                phase_reached=BridgeTestPhase.NOT_STARTED,
                success=False,
                message=f"VoiceOrchestrator unavailable: {err}",
                error=err,
                duration_ms=int((time.time() - start) * 1000),
            )

        # Synchronisation events
        wake_event = threading.Event()
        speaking_event = threading.Event()
        speaking_done_event = threading.Event()

        # Mutable state shared across callbacks (using lists for closure capture)
        captured_wake_text: list = [None]
        past_wake: list = [False]
        saw_speaking: list = [False]

        # ── Register callbacks ────────────────────────────────────────────

        def _on_wake_word(text: str) -> None:
            captured_wake_text[0] = text
            past_wake[0] = True
            wake_event.set()
            if on_wake_detected:
                on_wake_detected(text)

        def _on_state_change(old_state, new_state) -> None:
            try:
                from bridge.orchestrator import OrchestratorState
            except ImportError:
                return

            # OpenClaw ACK cycle completed (whether by response or timeout).
            # WAKE_WORD_ACK → SPEAKING means OpenClaw provided a phrase.
            # WAKE_WORD_ACK → INTERACTIVE means it timed out.
            if old_state == OrchestratorState.WAKE_WORD_ACK:
                responded = (new_state == OrchestratorState.SPEAKING)
                if on_ack_complete:
                    on_ack_complete(responded)

            # TTS playback started after wake word
            if past_wake[0] and new_state == OrchestratorState.SPEAKING:
                saw_speaking[0] = True
                speaking_event.set()
                if on_speaking:
                    on_speaking()

            # TTS playback finished
            if (past_wake[0]
                    and old_state == OrchestratorState.SPEAKING
                    and new_state == OrchestratorState.INTERACTIVE):
                speaking_done_event.set()
                if on_complete:
                    on_complete()

        orchestrator.on_wake_word = _on_wake_word
        orchestrator.on_state_change = _on_state_change

        # ── Start the bridge ──────────────────────────────────────────────

        try:
            started = orchestrator.start()
        except Exception as exc:
            return BridgeTestResult(
                phase_reached=BridgeTestPhase.STARTUP_FAILED,
                success=False,
                message=f"orchestrator.start() raised: {exc}",
                error=exc,
                duration_ms=int((time.time() - start) * 1000),
            )

        if not started:
            return BridgeTestResult(
                phase_reached=BridgeTestPhase.STARTUP_FAILED,
                success=False,
                message="Orchestrator failed to start (audio device or pipeline error)",
                duration_ms=int((time.time() - start) * 1000),
            )

        self._orchestrator = orchestrator

        if on_started:
            on_started()
        if on_listening:
            on_listening()

        # ── Wait for wake word ────────────────────────────────────────────

        detected = wake_event.wait(timeout=self.wake_word_timeout_s)

        if not detected:
            self._safe_stop(orchestrator)
            if on_timeout:
                on_timeout()
            return BridgeTestResult(
                phase_reached=BridgeTestPhase.LISTENING,
                success=False,
                message=(
                    f"Wake word not detected within {self.wake_word_timeout_s:.0f}s. "
                    "Bridge started correctly but the wake word was not triggered — "
                    "check microphone positioning or ambient noise."
                ),
                startup_ok=True,
                wake_word_detected=False,
                duration_ms=int((time.time() - start) * 1000),
            )

        # ── Wait for ACK and optional TTS ────────────────────────────────
        # Give the ACK its configured timeout plus extra for HTTP round-trip.
        try:
            ack_budget_s = config.bridge.acknowledgement.timeout_ms / 1000.0
        except Exception:
            ack_budget_s = 5.0
        speaking_event.wait(timeout=ack_budget_s + self.response_timeout_s)

        openclaw_responded = saw_speaking[0]

        if openclaw_responded:
            # Wait for TTS to finish before stopping the bridge
            speaking_done_event.wait(timeout=15.0)
            tts_played = speaking_done_event.is_set()
        else:
            tts_played = False

        self._safe_stop(orchestrator)

        phase = (
            BridgeTestPhase.COMPLETE
            if (openclaw_responded and tts_played)
            else BridgeTestPhase.INTERACTIVE
        )

        return BridgeTestResult(
            phase_reached=phase,
            success=True,
            message=(
                "Bridge test passed — wake word, OpenClaw ACK, and TTS all working"
                if openclaw_responded
                else "Wake word detected — OpenClaw ACK timed out (advisory: bridge is functional)"
            ),
            startup_ok=True,
            wake_word_detected=True,
            wake_word_text=captured_wake_text[0],
            openclaw_responded=openclaw_responded,
            tts_played=tts_played,
            duration_ms=int((time.time() - start) * 1000),
        )

    def stop(self) -> None:
        """Stop the orchestrator if it is still running."""
        if self._orchestrator:
            self._safe_stop(self._orchestrator)
            self._orchestrator = None

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _load_config():
        """Return (config, None) or (None, exception)."""
        try:
            from bridge.config import get_config
            return get_config(), None
        except Exception as exc:
            return None, exc

    @staticmethod
    def _create_orchestrator(config):
        """Return (orchestrator, None) or (None, exception)."""
        try:
            from bridge.orchestrator import VoiceOrchestrator
            return VoiceOrchestrator(config=config), None
        except ImportError as exc:
            return None, exc
        except Exception as exc:
            return None, exc

    @staticmethod
    def _safe_stop(orchestrator) -> None:
        try:
            orchestrator.stop()
        except Exception as exc:
            logger.warning("bridge_test_stop_error", error=str(exc))
