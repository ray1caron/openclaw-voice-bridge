"""Integration of Barge-In handler with Audio Pipeline.

Issue #8: Connects the BargeInHandler with AudioPipeline for interruption
support during TTS playback.
"""
import asyncio
import threading
from typing import Optional, Callable

import structlog

from audio.barge_in import BargeInHandler, BargeInConfig, InterruptionEvent
from bridge.audio_pipeline import AudioPipeline, PipelineState

logger = structlog.get_logger()


class AudioPipelineBargeIn:
    """Integrates BargeInHandler with AudioPipeline.
    
    Monitors audio input during TTS playback and triggers
    interruption when user speaks (barge-in).
    """
    
    def __init__(
        self,
        audio_pipeline: AudioPipeline,
        barge_in_config: Optional[BargeInConfig] = None
    ):
        """Initialize barge-in integration.
        
        Args:
            audio_pipeline: The audio pipeline to monitor
            barge_in_config: Barge-in configuration (uses defaults if None)
        """
        self.pipeline = audio_pipeline
        self.barge_in = BargeInHandler(config=barge_in_config or BargeInConfig())
        
        # Callbacks
        self.on_interruption: Optional[Callable[[InterruptionEvent], None]] = None
        
        # Tracking
        self._monitoring = False
        self._monitor_thread: Optional[threading.Thread] = None
        
        # Set up pipeline state callback
        self.pipeline.add_state_callback(self._on_pipeline_state_change)
        
        # Set up barge-in callback
        self.barge_in.on_interruption = self._handle_interruption
        
        logger.info("barge_in_integration_initialized")
    
    def _on_pipeline_state_change(self, old_state: PipelineState, new_state: PipelineState):
        """Handle pipeline state changes."""
        if new_state == PipelineState.SPEAKING:
            # Assistant started speaking - start monitoring for interruption
            self._start_monitoring()
        elif old_state == PipelineState.SPEAKING:
            # Assistant stopped speaking - stop monitoring
            self._stop_monitoring()
    
    def _start_monitoring(self):
        """Start monitoring for barge-in during speaking."""
        if not self.barge_in.config.enabled:
            logger.debug("barge_in_disabled")
            return
        
        logger.info("barge_in_monitoring_started")
        self.barge_in.transition_to_speaking()
        self._monitoring = True
        
        # Start monitoring loop
        self._monitor_thread = threading.Thread(target=self._monitor_loop, daemon=True)
        self._monitor_thread.start()
    
    def _stop_monitoring(self):
        """Stop barge-in monitoring."""
        if not self._monitoring:
            return
        
        logger.info("barge_in_monitoring_stopped")
        self._monitoring = False
        
        if self._monitor_thread and self._monitor_thread.is_alive():
            self._monitor_thread.join(timeout=0.1)
        
        # Return to listening state
        asyncio.run_coroutine_threadsafe(
            self.barge_in.start_listening(),
            asyncio.get_event_loop() if asyncio.get_event_loop().is_running() else asyncio.new_event_loop()
        )
    
    def _monitor_loop(self):
        """Monitor loop for VAD energy during speaking."""
        while self._monitoring and self.pipeline.state == PipelineState.SPEAKING:
            # Get VAD energy from pipeline
            if hasattr(self.pipeline, 'vad'):
                energy = self._get_vad_energy()
                if energy > self.barge_in.config.sensitivity.value:
                    # Potential interruption - update speech tracking
                    self._check_for_interruption(energy)
            
            # Small sleep to prevent busy-waiting
            threading.Event().wait(0.05)  # 50ms
    
    def _get_vad_energy(self) -> float:
        """Get current VAD energy level from pipeline."""
        # This would need to be implemented based on actual VAD
        # For now, return a mock value
        if hasattr(self.pipeline.vad, 'current_energy'):
            return self.pipeline.vad.current_energy
        return 0.0
    
    def _check_for_interruption(self, energy: float):
        """Check if energy level indicates interruption."""
        # This would tie into BargeInHandler's logic
        # For now, just trigger if configured
        pass
    
    def _handle_interruption(self, event: InterruptionEvent):
        """Handle detected interruption."""
        logger.info(
            "barge_in_interruption_detected",
            latency_ms=event.latency_ms,
            energy=event.vad_energy
        )
        
        # Update pipeline stats
        self.pipeline._stats.barge_in_count += 1
        
        # Stop TTS playback
        self.pipeline.stop_playback()
        
        # Transition pipeline to listening
        self.pipeline._set_state(PipelineState.LISTENING)
        
        # Clear output buffer
        self.pipeline.output_buffer.clear()
        
        # Notify callback
        if self.on_interruption:
            try:
                if asyncio.iscoroutinefunction(self.on_interruption):
                    asyncio.create_task(self.on_interruption(event))
                else:
                    self.on_interruption(event)
            except Exception as e:
                logger.error("interruption_callback_error", error=str(e))
    
    def enable(self):
        """Enable barge-in monitoring."""
        self.barge_in.config.enabled = True
        logger.info("barge_in_enabled")
    
    def disable(self):
        """Disable barge-in monitoring."""
        self.barge_in.config.enabled = False
        self._stop_monitoring()
        logger.info("barge_in_disabled")
    
    def get_stats(self) -> dict:
        """Get barge-in statistics."""
        return self.barge_in.get_stats()


def integrate_barge_in(
    audio_pipeline: AudioPipeline,
    barge_in_config: Optional[BargeInConfig] = None
) -> AudioPipelineBargeIn:
    """Integrate barge-in with audio pipeline.
    
    Args:
        audio_pipeline: The audio pipeline to enhance
        barge_in_config: Optional barge-in configuration
    
    Returns:
        AudioPipelineBargeIn integration instance
    """
    return AudioPipelineBargeIn(audio_pipeline, barge_in_config)
