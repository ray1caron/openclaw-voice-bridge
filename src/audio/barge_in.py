"""Barge-In / Interruption handling for voice bridge.

Issue #8: Implements interruption detection and handling
when user speaks while assistant is responding.
"""

import asyncio
import logging
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Optional, Callable
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)


class BargeInState(Enum):
    """State machine for barge-in detection."""
    IDLE = auto()
    LISTENING = auto()
    SPEAKING = auto()
    INTERRUPTING = auto()


class BargeInSensitivity(Enum):
    """Sensitivity levels for interruption detection."""
    LOW = 0.7      # High threshold - harder to interrupt
    MEDIUM = 0.5   # Balanced
    HIGH = 0.3     # Low threshold - easier to interrupt


@dataclass
class BargeInConfig:
    """Configuration for barge-in detection."""
    enabled: bool = True
    sensitivity: BargeInSensitivity = BargeInSensitivity.MEDIUM
    min_speech_ms: int = 250          # Minimum speech duration to trigger interrupt
    cooldown_ms: int = 500            # Cooldown after interrupt before new detection
    vad_threshold: float = 0.5
    
    # Latency targets
    max_interrupt_latency_ms: float = 100.0


@dataclass
class InterruptionEvent:
    """Event fired when barge-in is detected."""
    timestamp: datetime
    vad_energy: float
    confidence: float
    latency_ms: float
    

class BargeInHandler:
    """Handles interruption detection and state management.
    
    When user speaks during assistant output:
    1. VAD detects speech energy above threshold
    2. Confirm minimum speech duration (debounce)
    3. Trigger interruption event
    4. Cancel current TTS/audio output
    5. Transition back to listening state
    """
    
    def __init__(
        self,
        config: BargeInConfig = None,
        vad_callback: Optional[Callable[[], float]] = None
    ):
        self.config = config or BargeInConfig()
        self.vad_callback = vad_callback
        
        self.state = BargeInState.IDLE
        self._state_lock = asyncio.Lock()
        
        # Event handlers
        self.on_interruption: Optional[Callable[[InterruptionEvent], None]] = None
        self.on_state_change: Optional[Callable[[BargeInState, BargeInState], None]] = None
        
        # Tracking
        self.speech_start: Optional[datetime] = None
        self.last_interrupt: Optional[datetime] = None
        self.interrupt_count = 0
        
        # Statistics
        self.stats = {
            'interruptions_detected': 0,
            'avg_latency_ms': 0.0,
            'total_latency_ms': 0.0,
        }
        
        # Background task
        self._monitor_task: Optional[asyncio.Task] = None
        self._running = False
    
    async def start(self):
        """Start the barge-in monitor."""
        if not self.config.enabled:
            logger.info("Barge-in disabled, not starting monitor")
            return
            
        self._running = True
        self._monitor_task = asyncio.create_task(self._monitor_loop())
        logger.info("Barge-in monitor started");
    
    async def stop(self):
        """Stop the barge-in monitor."""
        self._running = False
        if self._monitor_task:
            self._monitor_task.cancel()
            try:
                await self._monitor_task
            except asyncio.CancelledError:
                pass
        logger.info("Barge-in monitor stopped");
    
    async def _monitor_loop(self):
        """Main monitoring loop for barge-in detection."""
        while self._running:
            try:
                await self._check_for_interruption()
                await asyncio.sleep(0.05)  # 50ms polling
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in barge-in monitor: {e}")
    
    async def _check_for_interruption(self):
        """Check if user is interrupting current speech."""
        async with self._state_lock:
            # Only check during SPEAKING state
            if self.state != BargeInState.SPEAKING:
                return
            
            # Check cooldown
            if self.last_interrupt:
                elapsed = (datetime.now() - self.last_interrupt).total_seconds() * 1000
                if elapsed < self.config.cooldown_ms:
                    return
            
            # Get VAD energy (requires callback)
            if self.vad_callback:
                energy = self.vad_callback()
                threshold = self.config.sensitivity.value
                
                if energy > threshold:
                    await self._handle_speech_detected(energy)
    
    async def _handle_speech_detected(self, energy: float):
        """Handle detected speech during assistant output."""
        now = datetime.now()
        
        if not self.speech_start:
            self.speech_start = now
            return
        
        # Check minimum duration
        duration_ms = (now - self.speech_start).total_seconds() * 1000
        if duration_ms < self.config.min_speech_ms:
            return
        
        # Confirm interruption
        await self._trigger_interruption(energy, duration_ms)
    
    async def _trigger_interruption(self, energy: float, duration_ms: float):
        """Trigger the interruption event."""
        now = datetime.now()
        
        # Calculate latency from speech start
        latency_ms = (now - self.speech_start).total_seconds() * 1000
        
        # Update state
        old_state = self.state
        self.state = BargeInState.INTERRUPTING
        
        if self.on_state_change:
            await self._safe_callback(self.on_state_change, old_state, self.state)
        
        # Update tracking
        self.last_interrupt = now
        self.speech_start = None  # Reset
        self.interrupt_count += 1
        
        # Update stats
        self.stats['interruptions_detected'] += 1
        self.stats['total_latency_ms'] += latency_ms
        self.stats['avg_latency_ms'] = (
            self.stats['total_latency_ms'] / self.stats['interruptions_detected']
        )
        
        # Create event
        event = InterruptionEvent(
            timestamp=now,
            vad_energy=energy,
            confidence=min(1.0, duration_ms / self.config.min_speech_ms),
            latency_ms=latency_ms
        )
        
        logger.info(
            f"Barge-in detected: energy={energy:.3f}, latency={latency_ms:.1f}ms, "
            f"confidence={event.confidence:.2f}"
        );
        
        # Notify handler
        if self.on_interruption:
            await self._safe_callback(self.on_interruption, event)
    
    async def _safe_callback(self, callback, *args):
        """Safely execute a callback."""
        try:
            if asyncio.iscoroutinefunction(callback):
                await callback(*args)
            else:
                callback(*args)
        except Exception as e:
            logger.error(f"Error in barge-in callback: {e}")
    
    # State transitions
    
    async def transition_to(self, new_state: BargeInState):
        """Transition to a new state."""
        async with self._state_lock:
            old_state = self.state
            self.state = new_state
            
            # Reset speech detection on state change
            if new_state != BargeInState.SPEAKING:
                self.speech_start = None
            
            if self.on_state_change and old_state != new_state:
                await self._safe_callback(self.on_state_change, old_state, new_state)
            
            logger.debug(f"Barge-in state: {old_state.name} -> {new_state.name}");
    
    async def start_speaking(self):
        """Call when assistant starts speaking."""
        await self.transition_to(BargeInState.SPEAKING)
        self.speech_start = None  # Reset any partial speech detection
    
    async def start_listening(self):
        """Call when assistant starts listening."""
        await self.transition_to(BargeInState.LISTENING)
    
    async def go_idle(self):
        """Call when conversation is idle."""
        await self.transition_to(BargeInState.IDLE)
    
    async def clear_interrupt(self):
        """Clear the interrupting state and return to listening."""
        if self.state == BargeInState.INTERRUPTING:
            await self.transition_to(BargeInState.LISTENING)
    
    def get_stats(self) -> dict:
        """Get barge-in statistics."""
        return {
            **self.stats,
            'current_state': self.state.name,
            'interrupt_count': self.interrupt_count,
            'latency_target_met': self.stats['avg_latency_ms'] <= self.config.max_interrupt_latency_ms
            if self.stats['interruptions_detected'] > 0 else None,
        }
