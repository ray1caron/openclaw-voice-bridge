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

Diagnostic information captured
--------------------------------
* Full exception tracebacks (not just the message)
* All bridge log lines emitted during the test (via stdlib logging handler)
* Component-level status: which of audio/STT/TTS/wake-word failed
* Audio pipeline stats on wake-word timeout (frames processed, VAD segments)
* New bug-tracker entries written by the bridge during the test
"""

from __future__ import annotations

import logging
import threading
import time
import traceback as _tb
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Dict, List, Optional

import structlog

logger = structlog.get_logger()


# ---------------------------------------------------------------------------
# Log capture helper
# ---------------------------------------------------------------------------

class _LogCapture(logging.Handler):
    """Stdlib logging handler that collects records into a list."""

    def __init__(self) -> None:
        super().__init__(level=logging.DEBUG)
        self.setFormatter(logging.Formatter(
            "%(asctime)s [%(levelname)-5s] %(name)s: %(message)s",
            datefmt="%H:%M:%S",
        ))
        self.lines: List[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self.lines.append(self.format(record))
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------

class BridgeTestPhase(Enum):
    """Phase reached when the test result was captured."""
    NOT_STARTED = "not_started"        # Config / import failure before anything ran
    STARTUP_FAILED = "startup_failed"  # orchestrator.start() returned False / raised
    LISTENING = "listening"            # Bridge started; waiting for wake word
    WAKE_WORD = "wake_word"            # Wake word detected by the real pipeline
    INTERACTIVE = "interactive"        # Entered interactive mode (ACK done / timed out)
    SPEAKING = "speaking"              # TTS is actively playing
    COMPLETE = "complete"              # Full flow finished


@dataclass
class BridgeTestResult:
    """Result of a bridge integration test, including full diagnostic data."""
    phase_reached: BridgeTestPhase
    success: bool
    message: str

    # High-level pass/fail per component
    startup_ok: bool = False
    wake_word_detected: bool = False
    wake_word_text: Optional[str] = None
    openclaw_responded: bool = False
    tts_played: bool = False

    # Component initialisation detail (populated on startup failure)
    component_status: Dict[str, str] = field(default_factory=dict)

    # Exception that caused the failure (with traceback)
    error: Optional[Exception] = None
    traceback_str: Optional[str] = None

    # All bridge log lines captured during the test
    log_lines: List[str] = field(default_factory=list)

    # Audio pipeline statistics at test end (useful for wake-word timeouts)
    audio_stats: Dict[str, object] = field(default_factory=dict)

    # Bug-tracker entries written by the bridge during this test
    new_bug_ids: List[int] = field(default_factory=list)

    duration_ms: int = 0

    @property
    def passed(self) -> bool:
        return self.success

    @property
    def failed(self) -> bool:
        return not self.success

    def summary_lines(self) -> List[str]:
        """Human-readable summary lines suitable for printing."""
        lines = [
            f"  Bridge startup   : {'✅ OK' if self.startup_ok else '❌ FAILED'}",
        ]
        if self.component_status:
            for name, status in self.component_status.items():
                icon = "✅" if "ok" in status.lower() or "loaded" in status.lower() else "❌"
                lines.append(f"    {icon} {name}: {status}")
        if self.startup_ok:
            ww = f"✅ '{self.wake_word_text}'" if self.wake_word_detected else "❌ not detected"
            lines.append(f"  Wake word        : {ww}")
        if self.wake_word_detected:
            oc = "✅ responded" if self.openclaw_responded else "⏰ timed out (advisory)"
            lines.append(f"  OpenClaw ACK     : {oc}")
            tts = "✅ played" if self.tts_played else "⏸  skipped (no ACK response)"
            lines.append(f"  TTS playback     : {tts}")
        if self.audio_stats:
            lines.append(f"  Audio stats:")
            for k, v in self.audio_stats.items():
                lines.append(f"    {k}: {v}")
        if self.duration_ms:
            lines.append(f"  Duration         : {self.duration_ms}ms")
        return lines

    def debug_lines(self) -> List[str]:
        """Full debug output including traceback and log lines."""
        lines = []
        if self.traceback_str:
            lines.append("  --- Traceback ---")
            for ln in self.traceback_str.splitlines():
                lines.append(f"  {ln}")
        if self.log_lines:
            lines.append(f"  --- Bridge log ({len(self.log_lines)} lines) ---")
            for ln in self.log_lines[-60:]:   # last 60 lines are most relevant
                lines.append(f"  {ln}")
            if len(self.log_lines) > 60:
                lines.insert(-len(self.log_lines[-60:]), f"  ... ({len(self.log_lines) - 60} earlier lines omitted)")
        if self.new_bug_ids:
            lines.append(f"  --- New bug-tracker entries: {self.new_bug_ids} ---")
            lines.append("  Run: python -m installer --show-bugs")
        return lines


# ---------------------------------------------------------------------------
# BridgeTester
# ---------------------------------------------------------------------------

class BridgeTester:
    """Run the real VoiceOrchestrator to verify the bridge end-to-end.

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
        enable_logging: bool = True,
    ) -> None:
        self.wake_word_timeout_s = wake_word_timeout_s
        self.response_timeout_s = response_timeout_s
        self.enable_logging = enable_logging
        self._orchestrator = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def test_startup(self) -> BridgeTestResult:
        """Start the bridge, wait briefly, then stop it.

        Verifies that every component initialises without error:
        audio I/O devices, STT model, TTS engine, wake-word detector.
        No user interaction required.
        """
        start = time.time()
        log_capture = self._attach_log_capture()
        bug_ids_before = self._current_bug_ids()

        config, err, tb = self._load_config()
        if err:
            return self._make_error_result(
                BridgeTestPhase.NOT_STARTED,
                f"Config load failed: {err}",
                err, tb, log_capture, bug_ids_before, start,
            )

        orchestrator, err, tb = self._create_orchestrator(config)
        if err:
            return self._make_error_result(
                BridgeTestPhase.NOT_STARTED,
                f"VoiceOrchestrator unavailable: {err}",
                err, tb, log_capture, bug_ids_before, start,
            )

        try:
            started = orchestrator.start()
        except Exception as exc:
            return self._make_error_result(
                BridgeTestPhase.STARTUP_FAILED,
                f"orchestrator.start() raised: {exc}",
                exc, _tb.format_exc(), log_capture, bug_ids_before, start,
            )

        component_status = self._probe_component_status(orchestrator)

        if not started:
            result = self._make_error_result(
                BridgeTestPhase.STARTUP_FAILED,
                "Orchestrator failed to start — check component status below",
                None, None, log_capture, bug_ids_before, start,
            )
            result.component_status = component_status
            self._safe_stop(orchestrator)
            return result

        # Brief warm-up — lets audio pipeline settle then stop cleanly
        time.sleep(1.5)
        audio_stats = self._collect_audio_stats(orchestrator)
        self._safe_stop(orchestrator)
        self._detach_log_capture(log_capture)

        return BridgeTestResult(
            phase_reached=BridgeTestPhase.LISTENING,
            success=True,
            message="Bridge started and stopped cleanly — all components initialised OK",
            startup_ok=True,
            component_status=component_status,
            log_lines=log_capture.lines[:],
            audio_stats=audio_stats,
            new_bug_ids=self._new_bug_ids(bug_ids_before),
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

        The caller prompts the user to speak the wake word (typically via
        the ``on_listening`` callback).

        Callback sequence on success:
            on_started → on_listening → on_wake_detected → on_ack_complete
            → on_speaking → on_complete
        """
        start = time.time()
        log_capture = self._attach_log_capture()
        bug_ids_before = self._current_bug_ids()

        config, err, tb = self._load_config()
        if err:
            return self._make_error_result(
                BridgeTestPhase.NOT_STARTED,
                f"Config load failed: {err}",
                err, tb, log_capture, bug_ids_before, start,
            )

        orchestrator, err, tb = self._create_orchestrator(config)
        if err:
            return self._make_error_result(
                BridgeTestPhase.NOT_STARTED,
                f"VoiceOrchestrator unavailable: {err}",
                err, tb, log_capture, bug_ids_before, start,
            )

        # Synchronisation events
        wake_event = threading.Event()
        speaking_event = threading.Event()
        speaking_done_event = threading.Event()

        # Mutable shared state (lists for closure capture)
        captured_wake_text: list = [None]
        past_wake: list = [False]
        saw_speaking: list = [False]

        # ── Callbacks ────────────────────────────────────────────────────

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
            # OpenClaw ACK cycle done (response or timeout)
            if old_state == OrchestratorState.WAKE_WORD_ACK:
                responded = (new_state == OrchestratorState.SPEAKING)
                if on_ack_complete:
                    on_ack_complete(responded)
            # TTS started after wake word
            if past_wake[0] and new_state == OrchestratorState.SPEAKING:
                saw_speaking[0] = True
                speaking_event.set()
                if on_speaking:
                    on_speaking()
            # TTS finished
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
            return self._make_error_result(
                BridgeTestPhase.STARTUP_FAILED,
                f"orchestrator.start() raised: {exc}",
                exc, _tb.format_exc(), log_capture, bug_ids_before, start,
            )

        component_status = self._probe_component_status(orchestrator)

        if not started:
            result = self._make_error_result(
                BridgeTestPhase.STARTUP_FAILED,
                "Orchestrator failed to start — check component status below",
                None, None, log_capture, bug_ids_before, start,
            )
            result.component_status = component_status
            return result

        self._orchestrator = orchestrator

        if on_started:
            on_started()
        if on_listening:
            on_listening()

        # ── Wait for wake word ────────────────────────────────────────────

        detected = wake_event.wait(timeout=self.wake_word_timeout_s)

        if not detected:
            audio_stats = self._collect_audio_stats(orchestrator)
            self._safe_stop(orchestrator)
            self._detach_log_capture(log_capture)
            if on_timeout:
                on_timeout()
            return BridgeTestResult(
                phase_reached=BridgeTestPhase.LISTENING,
                success=False,
                message=(
                    f"Wake word not detected within {self.wake_word_timeout_s:.0f}s. "
                    "Bridge started correctly but the wake word was not triggered — "
                    "check microphone positioning or background noise."
                ),
                startup_ok=True,
                component_status=component_status,
                wake_word_detected=False,
                log_lines=log_capture.lines[:],
                audio_stats=audio_stats,
                new_bug_ids=self._new_bug_ids(bug_ids_before),
                duration_ms=int((time.time() - start) * 1000),
            )

        # ── Wait for ACK and optional TTS ────────────────────────────────

        try:
            ack_budget_s = config.bridge.acknowledgement.timeout_ms / 1000.0
        except Exception:
            ack_budget_s = 5.0

        speaking_event.wait(timeout=ack_budget_s + self.response_timeout_s)
        openclaw_responded = saw_speaking[0]

        if openclaw_responded:
            speaking_done_event.wait(timeout=15.0)
            tts_played = speaking_done_event.is_set()
        else:
            tts_played = False

        audio_stats = self._collect_audio_stats(orchestrator)
        self._safe_stop(orchestrator)
        self._detach_log_capture(log_capture)

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
            component_status=component_status,
            log_lines=log_capture.lines[:],
            audio_stats=audio_stats,
            new_bug_ids=self._new_bug_ids(bug_ids_before),
            duration_ms=int((time.time() - start) * 1000),
        )

    def stop(self) -> None:
        """Stop the orchestrator if still running."""
        if self._orchestrator:
            self._safe_stop(self._orchestrator)
            self._orchestrator = None

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _attach_log_capture(self) -> _LogCapture:
        """Install a log handler to capture all bridge log output."""
        handler = _LogCapture()
        if self.enable_logging:
            logging.getLogger().addHandler(handler)
        return handler

    @staticmethod
    def _detach_log_capture(handler: _LogCapture) -> None:
        logging.getLogger().removeHandler(handler)

    @staticmethod
    def _load_config():
        """Return (config, None, None) or (None, exception, traceback_str)."""
        try:
            from bridge.config import get_config
            return get_config(), None, None
        except Exception as exc:
            return None, exc, _tb.format_exc()

    @staticmethod
    def _create_orchestrator(config):
        """Return (orchestrator, None, None) or (None, exception, traceback_str)."""
        try:
            from bridge.orchestrator import VoiceOrchestrator
            return VoiceOrchestrator(config=config), None, None
        except ImportError as exc:
            return None, exc, _tb.format_exc()
        except Exception as exc:
            return None, exc, _tb.format_exc()

    @staticmethod
    def _probe_component_status(orchestrator) -> Dict[str, str]:
        """Read per-component init status from the live orchestrator."""
        status: Dict[str, str] = {}
        try:
            ap = orchestrator.audio_pipeline
            status["audio_pipeline"] = ap.state.value if hasattr(ap, "state") else "unknown"
            if hasattr(ap, "_input_device") and ap._input_device:
                status["audio_input_device"] = str(ap._input_device.name)
            if hasattr(ap, "_output_device") and ap._output_device:
                status["audio_output_device"] = str(ap._output_device.name)
        except Exception as exc:
            status["audio_pipeline"] = f"probe error: {exc}"
        try:
            stt = orchestrator.stt_engine
            status["stt"] = "loaded" if getattr(stt, "_model", None) else "not loaded"
        except Exception as exc:
            status["stt"] = f"probe error: {exc}"
        try:
            tts = orchestrator.tts_engine
            status["tts"] = "loaded" if getattr(tts, "_model", None) else "not loaded"
        except Exception as exc:
            status["tts"] = f"probe error: {exc}"
        try:
            ww = orchestrator.wake_word_detector
            status["wake_word_detector"] = "running" if getattr(ww, "_running", False) else "stopped"
        except Exception as exc:
            status["wake_word_detector"] = f"probe error: {exc}"
        return status

    @staticmethod
    def _collect_audio_stats(orchestrator) -> Dict[str, object]:
        """Read AudioPipeline stats for the wake-word timeout diagnostic."""
        stats: Dict[str, object] = {}
        try:
            ap = orchestrator.audio_pipeline
            ps = ap._stats
            stats["frames_processed"] = ps.audio_frames_processed
            stats["speech_segments_detected"] = ps.speech_segments_detected
            stats["queue_overflows"] = ps.queue_overflow_count
            stats["pipeline_state"] = ap.state.value if hasattr(ap, "state") else "unknown"
        except Exception as exc:
            stats["probe_error"] = str(exc)
        return stats

    @staticmethod
    def _current_bug_ids() -> set:
        """Return the set of bug IDs currently in the tracker."""
        try:
            from bridge.bug_tracker import BugTracker
            bt = BugTracker.get_instance()
            with bt._get_connection() as conn:
                rows = conn.execute("SELECT id FROM bugs").fetchall()
                return {r[0] for r in rows}
        except Exception:
            return set()

    @staticmethod
    def _new_bug_ids(before: set) -> List[int]:
        """Return IDs of bug-tracker entries added since ``before``."""
        try:
            from bridge.bug_tracker import BugTracker
            bt = BugTracker.get_instance()
            with bt._get_connection() as conn:
                rows = conn.execute("SELECT id FROM bugs").fetchall()
                after = {r[0] for r in rows}
            return sorted(after - before)
        except Exception:
            return []

    def _make_error_result(
        self,
        phase: BridgeTestPhase,
        message: str,
        exc: Optional[Exception],
        tb: Optional[str],
        log_capture: _LogCapture,
        bug_ids_before: set,
        start: float,
    ) -> BridgeTestResult:
        self._detach_log_capture(log_capture)
        return BridgeTestResult(
            phase_reached=phase,
            success=False,
            message=message,
            error=exc,
            traceback_str=tb,
            log_lines=log_capture.lines[:],
            new_bug_ids=self._new_bug_ids(bug_ids_before),
            duration_ms=int((time.time() - start) * 1000),
        )

    @staticmethod
    def _safe_stop(orchestrator) -> None:
        try:
            orchestrator.stop()
        except Exception as exc:
            logger.warning("bridge_test_stop_error", error=str(exc))
