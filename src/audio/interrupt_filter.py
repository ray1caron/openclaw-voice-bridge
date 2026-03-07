"""Response filter extension for interruption handling.

Integrates barge-in detection with the response filtering system.
"""

import asyncio
import logging
from typing import Optional, Callable

from bridge.openclaw_middleware import (
    MessageType, Speakability, TaggedMessage, OpenClawMiddleware
)
from .barge_in import BargeInHandler, BargeInState, InterruptionEvent

logger = logging.getLogger(__name__)


class InterruptAwareFilter:
    """Response filter that handles interruptions.
    
    When interruption is detected:
    - Current TTS is cancelled
    - Response buffer is cleared
    - System transitions to listening for new input
    """
    
    def __init__(self, barge_in_handler: BargeInHandler):
        self.barge_in = barge_in_handler
        self._current_response: Optional[str] = None
        self._response_buffer: str = ""
        self._interrupted = False
        
        # Set up interruption callback
        self.barge_in.on_interruption = self._on_interruption
    
    async def _on_interruption(self, event: InterruptionEvent):
        """Handle interruption event."""
        logger.info(f"Response interrupted at {event.latency_ms:.1f}ms")
        
        self._interrupted = True
        self._current_response = None
        self._response_buffer = ""
        
        # Notify TTS to stop
        if self.on_interrupt:
            await self._safe_callback(self.on_interrupt, event)
    
    on_interrupt: Optional[Callable[[InterruptionEvent], None]] = None
    
    async def _safe_callback(self, callback, *args):
        """Safely execute callback."""
        try:
            if callable(callback):
                if asyncio.iscoroutinefunction(callback):
                    await callback(*args)
                else:
                    callback(*args)
        except Exception as e:
            logger.error(f"Interrupt callback error: {e}")
    
    def process_message(self, tagged_msg: TaggedMessage) -> Optional[TaggedMessage]:
        """Process a tagged message with interruption awareness.
        
        Returns None if interrupted, the message otherwise.
        """
        # Check if we were interrupted
        if self._interrupted:
            # Reset for next turn
            self._interrupted = False
            self._response_buffer = ""
            return None
        
        # Only process final messages (check metadata.speakable)
        if tagged_msg.metadata.speakable != Speakability.SPEAK:
            return tagged_msg
        
        # Accumulate response
        self._current_response = tagged_msg.content
        self._response_buffer += tagged_msg.content
        
        return tagged_msg
    
    def is_interrupted(self) -> bool:
        """Check if current response was interrupted."""
        return self._interrupted
    
    def get_buffered_response(self) -> str:
        """Get accumulated response before interruption."""
        return self._response_buffer
    
    def reset(self):
        """Reset for new conversation turn."""
        self._interrupted = False
        self._current_response = None
        self._response_buffer = ""


class InterruptMessage:
    """Message type for interruption signals."""
    
    def __init__(self, event: InterruptionEvent):
        self.event = event
        self.type = "interruption"
    
    def to_dict(self) -> dict:
        return {
            'type': self.type,
            'timestamp': self.event.timestamp.isoformat(),
            'vad_energy': self.event.vad_energy,
            'confidence': self.event.confidence,
            'latency_ms': self.event.latency_ms,
        }


class InterruptAdapter:
    """Adapter for OpenClaw WebSocket interruption handling.
    
    Sends interruption signals to OpenClaw when barge-in is detected.
    """
    
    def __init__(self, websocket_client=None):
        self.ws = websocket_client
        self.barge_in: Optional[BargeInHandler] = None
    
    def attach_barge_in(self, handler: BargeInHandler):
        """Attach barge-in handler with WebSocket forwarding."""
        self.barge_in = handler
        self.barge_in.on_interruption = self._forward_to_openclaw
    
    async def _forward_to_openclaw(self, event: InterruptionEvent):
        """Forward interruption to OpenClaw via WebSocket."""
        interrupt_msg = InterruptMessage(event)
        
        if self.ws and hasattr(self.ws, 'send'):
            try:
                # Send interruption signal
                await self.ws.send(interrupt_msg.to_dict())
                logger.debug("Interrupt signal sent to OpenClaw")
            except Exception as e:
                logger.error(f"Failed to send interrupt signal: {e}")
        else:
            logger.warning("No WebSocket connection for interrupt signal")
    
    async def send_interrupt(self, event: InterruptionEvent):
        """Manually send an interruption signal."""
        await self._forward_to_openclaw(event)
