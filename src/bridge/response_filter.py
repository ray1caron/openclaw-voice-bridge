"""
Response Filter for OpenClaw Voice Bridge

Filters OpenClaw responses to ensure only final, user-facing responses
reach TTS. Blocks tool calls, thinking messages, and internal processing.
"""
import enum
import re
import time
from dataclasses import dataclass, field
from typing import Callable, Optional, Any
from collections import deque

import structlog

logger = structlog.get_logger()


class ResponseType(enum.Enum):
    """Classification of OpenClaw response types."""
    FINAL = "final"              # User-facing response (speak)
    THINKING = "thinking"          # Chain of thought (silent)
    TOOL_CALL = "tool_call"        # Tool execution (silent)
    PLANNING = "planning"          # Internal planning (silent)
    PROGRESS = "progress"          # Progress update (silent)
    ERROR = "error"                # Error message (speak if user-facing)
    UNKNOWN = "unknown"            # Unknown type (use heuristics)


class FilterDecision(enum.Enum):
    """Decision for a message."""
    SPEAK = "speak"                # Pass to TTS
    SILENT = "silent"              # Drop/filter out
    QUEUE = "queue"                # Hold for potential later


@dataclass
class FilteredMessage:
    """A message that has been processed by the filter."""
    original: dict
    response_type: ResponseType
    decision: FilterDecision
    confidence: float
    filtered_text: Optional[str] = None
    timestamp: float = field(default_factory=time.time)
    speak_score: float = 0.0       # 0-1, likelihood this should be spoken


@dataclass
class FilterStats:
    """Statistics for filtering operations."""
    total_messages: int = 0
    spoken_messages: int = 0
    silent_messages: int = 0
    queued_messages: int = 0
    avg_confidence: float = 0.0
    type_counts: dict = field(default_factory=lambda: {t.value: 0 for t in ResponseType})


class ResponseFilter:
    """
    Filters OpenClaw responses to determine what should be spoken.
    
    Uses a combination of:
    - Explicit message type detection from OpenClaw protocol
    - Heuristic pattern matching for "thinking" indicators
    - Confidence scoring for ambiguous messages
    - Queue management for buffering potential responses
    
    The key principle: Only final, user-facing responses reach TTS.
    """
    
    # Heuristic patterns for detecting non-speech content
    THINKING_PATTERNS = [
        # Starting phrases
        r"(?i)^(let me|allow me to|i'll|i will|let's|ok[\.,]|okay[\.,]|so[\.,]|now[\.,]|first[\.,])\s",
        r"(?i)^(hmm|uhh?|well|actually|wait|hold on)[\.,\s]",
        
        # Tool call markers
        r"\[\s*(?:Tool Call|tool|function call|calling)[^\]]*\]",
        r'\{\s*"(?:tool|function)":\s*',
        
        # Progress indicators
        r"(?i)(processing|computing|analyzing|searching|fetching|loading)",
        r"(?i)(thinking|pondering|considering|evaluating)",
        
        # Continuation markers
        r"\(\.\.\.(?:more|continuing|to be continued)\.\.\.\)$",
        r"\[continuing\]$",
        
        # Planning language
        r"(?i)(step \d+|first|next|then|finally|lastly)[\.,:]",
        r"(?i)(i need to|i should|i'm going to|plan:|strategy:)",
    ]
    
    FINAL_PATTERNS = [
        # Direct response indicators
        r"(?i)^(here (?:is|are)|the answer|in summary|to answer your question)",
        r"(?i)^(yes[\.,]|no[\.,]|correct|exactly|that's right)",
    ]
    
    def __init__(
        self,
        confidence_threshold: float = 0.7,
        queue_size: int = 10,
        on_filtered: Optional[Callable[[FilteredMessage], None]] = None,
    ):
        """
        Initialize the response filter.
        
        Args:
            confidence_threshold: Minimum confidence to speak (0-1)
            queue_size: Max messages to queue for potential speaking
            on_filtered: Callback when message is filtered
        """
        self.confidence_threshold = confidence_threshold
        self.on_filtered = on_filtered
        
        # Message queue for buffering
        self._queue: deque[FilteredMessage] = deque(maxlen=queue_size)
        self._last_spoken_time: Optional[float] = None
        self.stats = FilterStats()
        
        # Compile regex patterns
        self._thinking_patterns = [re.compile(p) for p in self.THINKING_PATTERNS]
        self._final_patterns = [re.compile(p) for p in self.FINAL_PATTERNS]
        
        logger.info(
            "Response filter initialized",
            confidence_threshold=confidence_threshold,
            queue_size=queue_size,
        )
    
    def filter_message(self, message: dict) -> FilteredMessage:
        """
        Process an OpenClaw message and determine if it should be spoken.
        
        Args:
            message: Raw OpenClaw response message
            
        Returns:
            FilteredMessage with decision and metadata
        """
        self.stats.total_messages += 1
        
        # Extract text content
        text = self._extract_text(message)
        
        # Detect response type
        response_type = self._detect_type(message, text)
        self.stats.type_counts[response_type.value] += 1
        
        # Calculate confidence and decision
        confidence, decision = self._evaluate_message(message, text, response_type)
        
        # Create filtered message
        filtered = FilteredMessage(
            original=message,
            response_type=response_type,
            decision=decision,
            confidence=confidence,
            filtered_text=text if decision == FilterDecision.SPEAK else None,
            speak_score=self._calculate_speak_score(message, text, response_type, confidence),
        )
        
        # Update statistics
        if decision == FilterDecision.SPEAK:
            self.stats.spoken_messages += 1
            self._last_spoken_time = time.time()
        elif decision == FilterDecision.SILENT:
            self.stats.silent_messages += 1
        else:  # QUEUE
            self.stats.queued_messages += 1
            self._queue.append(filtered)
        
        # Update running average
        n = self.stats.total_messages
        self.stats.avg_confidence = (
            (self.stats.avg_confidence * (n - 1) + confidence) / n
        )
        
        # Notify callback
        if self.on_filtered:
            try:
                self.on_filtered(filtered)
            except Exception as e:
                logger.error("Filter callback failed", error=str(e))
        
        logger.debug(
            "Message filtered",
            response_type=response_type.value,
            decision=decision.value,
            confidence=confidence,
            text_preview=text[:50] if text else None,
        )
        
        return filtered
    
    def _extract_text(self, message: dict) -> Optional[str]:
        """Extract text content from message."""
        # Primary: direct text field
        text = message.get("text")
        if text:
            return text.strip()
        
        # Alternative: content field
        content = message.get("content")
        if isinstance(content, str):
            return content.strip()
        
        # Nested: content.text
        if isinstance(content, dict):
            return content.get("text", "").strip()
        
        # Response field
        response = message.get("response")
        if isinstance(response, str):
            return response.strip()
        
        return None
    
    def _detect_type(self, message: dict, text: Optional[str]) -> ResponseType:
        """
        Detect the type of OpenClaw response.
        
        Checks explicit type markers first, then falls back to heuristics.
        """
        # Check explicit message type field
        msg_type = message.get("type")
        if msg_type:
            type_mapping = {
                "tool_call": ResponseType.TOOL_CALL,
                "tool_result": ResponseType.TOOL_CALL,
                "thinking": ResponseType.THINKING,
                "plan": ResponseType.PLANNING,
                "planning": ResponseType.PLANNING,
                "progress": ResponseType.PROGRESS,
                "final": ResponseType.FINAL,
                "response": ResponseType.FINAL,
                "error": ResponseType.ERROR,
            }
            if msg_type in type_mapping:
                return type_mapping[msg_type]
        
        # Check for tool calls in message
        if "tool_calls" in message or "function_call" in message:
            return ResponseType.TOOL_CALL
        
        # Check for planning indicators
        if msg_type in ("plan", "strategy", "steps"):
            return ResponseType.PLANNING
        
        # Fallback: use heuristics on text
        if text:
            # Check for thinking patterns
            for pattern in self._thinking_patterns:
                if pattern.search(text):
                    return ResponseType.THINKING
            
            # Check for final patterns
            for pattern in self._final_patterns:
                if pattern.match(text):
                    return ResponseType.FINAL
        
        return ResponseType.UNKNOWN
    
    def _evaluate_message(
        self,
        message: dict,
        text: Optional[str],
        response_type: ResponseType,
    ) -> tuple[float, FilterDecision]:
        """
        Evaluate message and return (confidence, decision).
        """
        # Explicit type-based filtering
        if response_type == ResponseType.FINAL:
            return 0.95, FilterDecision.SPEAK
        
        if response_type == ResponseType.ERROR:
            # Errors are spoken if they have user-facing content
            if text and len(text) > 10:
                return 0.8, FilterDecision.SPEAK
            return 0.5, FilterDecision.SILENT
        
        if response_type in (ResponseType.TOOL_CALL, ResponseType.PLANNING):
            return 0.99, FilterDecision.SILENT
        
        if response_type == ResponseType.THINKING:
            return 0.95, FilterDecision.SILENT
        
        if response_type == ResponseType.PROGRESS:
            # Check if this is a significant progress update
            if text and ("complete" in text.lower() or "done" in text.lower()):
                return 0.6, FilterDecision.QUEUE
            return 0.7, FilterDecision.SILENT
        
        # UNKNOWN: use heuristics
        if text is None:
            return 0.3, FilterDecision.SILENT
        
        text_lower = text.lower()
        
        # Positive indicators for speaking
        speak_indicators = [
            text.endswith("?"),  # Questions likely need responses
            text.endswith("!"),  # Exclamations suggest finality
            len(text) > 50 and not any(p.search(text) for p in self._thinking_patterns),
            any(p.match(text) for p in self._final_patterns),
        ]
        speak_score = sum(speak_indicators) / len(speak_indicators)
        
        if speak_score >= 0.5:
            return 0.7 + (speak_score * 0.25), FilterDecision.SPEAK
        
        # Default: silent
        return 0.4, FilterDecision.SILENT
    
    def _calculate_speak_score(
        self,
        message: dict,
        text: Optional[str],
        response_type: ResponseType,
        confidence: float,
    ) -> float:
        """Calculate a 0-1 score for how likely this should be spoken."""
        # Clamp confidence to valid range
        confidence = max(0.0, min(1.0, confidence))
        
        if response_type == ResponseType.FINAL:
            return 1.0
        if response_type in (ResponseType.TOOL_CALL, ResponseType.PLANNING, ResponseType.THINKING):
            return 0.0
        
        if text is None:
            return 0.0
        
        # Length score: longer responses are more likely final
        length_score = min(len(text) / 100, 1.0)
        
        # Punctuation score
        punct_score = 0.0
        if text.endswith((".", "!", "?")):
            punct_score = 0.3
        
        # Combine scores
        final_score = (confidence * 0.6) + (length_score * 0.2) + (punct_score * 0.2)
        
        # Ensure result is clamped
        return max(0.0, min(1.0, final_score))
    
    def get_next_to_speak(self) -> Optional[FilteredMessage]:
        """
        Get the next message from the queue that should be spoken.
        
        This is useful for draining the queue when we detect a final response.
        """
        while self._queue:
            msg = self._queue.popleft()
            if msg.speak_score >= self.confidence_threshold:
                return msg
        return None
    
    def clear_queue(self) -> int:
        """Clear the message queue. Returns number of messages cleared."""
        count = len(self._queue)
        self._queue.clear()
        logger.info("Queue cleared", cleared_count=count)
        return count
    
    def get_stats(self) -> dict[str, Any]:
        """Get filter statistics."""
        return {
            "total_messages": self.stats.total_messages,
            "spoken_messages": self.stats.spoken_messages,
            "silent_messages": self.stats.silent_messages,
            "queued_messages": self.stats.queued_messages,
            "queue_length": len(self._queue),
            "avg_confidence": round(self.stats.avg_confidence, 3),
            "type_counts": self.stats.type_counts,
            "speak_rate": (
                self.stats.spoken_messages / self.stats.total_messages
                if self.stats.total_messages > 0 else 0
            ),
        }
    
    def reset_stats(self) -> None:
        """Reset all statistics."""
        self.stats = FilterStats()
        self._queue.clear()
        logger.info("Stats reset")


class ResponseFilterManager:
    """
    High-level manager that integrates filtering with TTS triggering.
    
    Acts as the bridge between the WebSocket client and the TTS system,
    ensuring only appropriate responses are spoken.
    """
    
    def __init__(
        self,
        filter_config: Optional[dict] = None,
        on_speak: Optional[Callable[[str], None]] = None,
    ):
        filter_config = filter_config or {}
        self.filter = ResponseFilter(
            confidence_threshold=filter_config.get("confidence_threshold", 0.7),
            queue_size=filter_config.get("queue_size", 10),
            on_filtered=self._on_filtered,
        )
        self.on_speak = on_speak
        self._last_final_time: Optional[float] = None
        
        logger.info("Response filter manager initialized")
    
    def _on_filtered(self, msg: FilteredMessage) -> None:
        """Handle filtered message."""
        if msg.decision == FilterDecision.SPEAK and msg.filtered_text and self.on_speak:
            try:
                self.on_speak(msg.filtered_text)
                self._last_final_time = time.time()
            except Exception as e:
                logger.error("Speak callback failed", error=str(e))
    
    def process_message(self, message: dict) -> Optional[str]:
        """
        Process a message and optionally return text to speak.
        
        Returns:
            Text to speak if message passes filter, None otherwise
        """
        filtered = self.filter.filter_message(message)
        
        if filtered.decision == FilterDecision.SPEAK:
            return filtered.filtered_text
        
        return None
    
    def flush_queue(self) -> list[str]:
        """
        Flush the queue and return any messages that should be spoken.
        
        Useful when ending a session or on explicit request.
        """
        texts = []
        while True:
            msg = self.filter.get_next_to_speak()
            if msg is None:
                break
            if msg.filtered_text:
                texts.append(msg.filtered_text)
        return texts
    
    def should_interrupt(self, new_message: dict) -> bool:
        """
        Determine if a new message should interrupt current speech.
        
        Currently interrupts on:
        - New final responses
        - Control messages (interrupt action)
        """
        msg_type = new_message.get("type")
        if msg_type == "control":
            return new_message.get("action") == "interrupt"
        
        # Check if this is likely a new final response
        text = self.filter._extract_text(new_message)
        if text and self.filter._detect_type(new_message, text) == ResponseType.FINAL:
            return True
        
        return False
