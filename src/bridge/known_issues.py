"""
Known issues detection module.

Detects known problematic states and creates structured bug reports.
This module provides pattern-based detection for recurring issues,
enabling consistent tracking and faster debugging.

Key Features:
- Pattern-based issue detection
- Automatic bug report creation
- Session-level detection tracking
- Singleton pattern for consistent state
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional, Dict, List, Set, Any, Callable
from enum import Enum

import structlog

from bridge.bug_tracker import BugTracker, BugSeverity

logger = structlog.get_logger()


class KnownIssueError(Exception):
    """
    Exception raised when a known issue is detected.
    
    This exception provides context about the detected issue,
    making it easier to handle known problems gracefully.
    
    Attributes:
        issue_key: The key identifying the known issue
        component: System component where issue occurred
        severity: Bug severity level
        hint: Debugging hint for resolving the issue
        
    Example:
        >>> try:
        ...     raise KnownIssueError("wake_word_zero_scores", "wake_word")
        ... except KnownIssueError as e:
        ...     print(f"Known issue: {e.issue_key} - {e.hint}")
    """
    
    def __init__(
        self,
        issue_key: str,
        component: str,
        message: Optional[str] = None,
    ) -> None:
        """
        Initialize known issue error.
        
        Args:
            issue_key: Key identifying the known issue in KNOWN_ISSUES
            component: Component where issue was detected
            message: Optional custom message (defaults to issue title)
        """
        self.issue_key = issue_key
        self.component = component
        
        # Look up issue details
        issue_info = KNOWN_ISSUES.get(issue_key)
        if issue_info:
            self.severity = issue_info["severity"]
            self.title = issue_info["title"]
            self.description = issue_info["description"]
            self.hint = issue_info["hint"]
        else:
            self.severity = BugSeverity.MEDIUM
            self.title = f"Unknown issue: {issue_key}"
            self.description = f"Unregistered known issue: {issue_key}"
            self.hint = "This issue is not yet documented"
        
        super().__init__(message or self.title)


@dataclass
class IssueDetection:
    """
    Record of a detected known issue.
    
    Tracks when and where an issue was detected, along with
    the associated bug report ID if captured.
    """
    issue_key: str
    session_id: str
    timestamp: str
    context: Dict[str, Any]
    bug_id: Optional[int] = None
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert detection record to dictionary."""
        return {
            "issue_key": self.issue_key,
            "session_id": self.session_id,
            "timestamp": self.timestamp,
            "context": self.context,
            "bug_id": self.bug_id,
        }


# Define known issues with their patterns and metadata
KNOWN_ISSUES: Dict[str, Dict[str, Any]] = {
    "wake_word_zero_scores": {
        "component": "wake_word",
        "severity": BugSeverity.HIGH,
        "title": "Wake word detection producing zero confidence scores",
        "description": (
            "The wake word detector is generating zero or near-zero confidence "
            "scores for all audio frames. This indicates the model is not properly "
            "initialized or the audio stream contains no valid speech data."
        ),
        "hint": (
            "Check: (1) wake word model loaded correctly, (2) audio stream is valid, "
            "(3) sample rate matches model expectations, (4) audio is not all silence."
        ),
    },
    
    "audio_device_not_found": {
        "component": "audio",
        "severity": BugSeverity.HIGH,
        "title": "Audio device not found or unavailable",
        "description": (
            "The requested audio device is not available. This may occur when "
            "the device is disconnected, in use by another application, or "
            "the device index is incorrect."
        ),
        "hint": (
            "Check: (1) device is physically connected, (2) no other app is using it, "
            "(3) device index is valid, (4) use 'list_devices' to see available devices."
        ),
    },
    
    "stt_model_load_failed": {
        "component": "stt",
        "severity": BugSeverity.CRITICAL,
        "title": "Speech-to-text model failed to load",
        "description": (
            "The STT model could not be loaded. This prevents all speech "
            "recognition functionality. Causes may include missing model files, "
            "incompatible model format, insufficient memory, or corrupted files."
        ),
        "hint": (
            "Check: (1) model path is correct, (2) model files exist and are readable, "
            "(3) sufficient system memory available, (4) model format is supported."
        ),
    },
    
    "http_timeout": {
        "component": "network",
        "severity": BugSeverity.MEDIUM,
        "title": "HTTP request timeout",
        "description": (
            "An HTTP request exceeded the configured timeout. This may indicate "
            "network issues, an overloaded server, or an unreasonably low timeout value."
        ),
        "hint": (
            "Check: (1) network connectivity, (2) server is responsive, "
            "(3) timeout value is appropriate for the operation, "
            "(4) consider retry logic or increasing timeout."
        ),
    },
    
    "vad_silence_loop": {
        "component": "audio",
        "severity": BugSeverity.MEDIUM,
        "title": "VAD stuck in silence detection loop",
        "description": (
            "Voice Activity Detection appears stuck reporting continuous silence "
            "or is unable to transition to speech state. Audio may be muted, "
            "or VAD sensitivity may need adjustment."
        ),
        "hint": (
            "Check: (1) microphone is not muted, (2) audio levels are non-zero, "
            "(3) VAD sensitivity threshold, (4) audio pipeline is correctly configured."
        ),
    },
    
    "tts_queue_overflow": {
        "component": "tts",
        "severity": BugSeverity.LOW,
        "title": "TTS queue overflow - messages dropped",
        "description": (
            "The text-to-speech queue reached capacity and messages were dropped. "
            "This typically occurs when TTS synthesis cannot keep up with incoming "
            "text messages."
        ),
        "hint": (
            "Consider: (1) increasing queue size, (2) increasing TTS processing rate, "
            "(3) batching messages, (4) implementing backpressure."
        ),
    },
    
    "session_state_corrupted": {
        "component": "session",
        "severity": BugSeverity.HIGH,
        "title": "Session state corruption detected",
        "description": (
            "Session state has been corrupted or is in an inconsistent state. "
            "This may cause unexpected behavior in voice processing pipeline."
        ),
        "hint": (
            "Check: (1) session lifecycle management, (2) concurrent access issues, "
            "(3) state serialization/deserialization, (4) consider session reset."
        ),
    },
    
    "openclaw_connection_lost": {
        "component": "openclaw",
        "severity": BugSeverity.HIGH,
        "title": "Lost connection to OpenClaw gateway",
        "description": (
            "The WebSocket connection to OpenClaw gateway was lost. "
            "Voice commands cannot be processed until connection is restored."
        ),
        "hint": (
            "Check: (1) gateway process is running, (2) network connectivity, "
            "(3) firewall rules, (4) reconnection logic is functioning."
        ),
    },
}


class KnownIssues:
    """
    Known issues detection and tracking manager.
    
    Detects known problematic states and creates structured bug reports.
    Maintains a per-session detection history for analysis.
    
    Example:
        >>> issues = KnownIssues()
        >>> if issues.detect_and_capture("wake_word_zero_scores", context, session_id):
        ...     print("Known issue detected and logged")
        >>> 
        >>> if issues.has_detected("wake_word_zero_scores"):
        ...     print("Issue previously detected in this session")
    """
    
    def __init__(self, bug_tracker: Optional[BugTracker] = None) -> None:
        """
        Initialize known issues manager.
        
        Args:
            bug_tracker: Optional BugTracker instance (singleton used if not provided)
        """
        self.bug_tracker = bug_tracker or BugTracker.get_instance()
        self._detections: Dict[str, List[IssueDetection]] = {}
        self._detection_counts: Dict[str, int] = {}
        
        logger.debug(
            "known_issues_initialized",
            tracked_issues=len(KNOWN_ISSUES),
        )
    
    @property
    def tracked_issues(self) -> Dict[str, Dict[str, Any]]:
        """
        Get the dictionary of tracked known issues.
        
        Returns:
            Dictionary mapping issue keys to their metadata
        """
        return KNOWN_ISSUES.copy()
    
    def detect_and_capture(
        self,
        issue_key: str,
        context: Dict[str, Any],
        session_id: str,
        user_context: Optional[str] = None,
        auto_create_github: bool = False,
    ) -> Optional[int]:
        """
        Detect a known issue and capture it as a bug report.
        
        Validates the issue key, creates a bug report with full context,
        and records the detection for this session.
        
        Args:
            issue_key: Key identifying the known issue (must exist in KNOWN_ISSUES)
            context: Additional context about the detection (state, values, etc.)
            session_id: Session identifier where detection occurred
            user_context: Optional human-readable context description
            auto_create_github: Whether to automatically create a GitHub issue
            
        Returns:
            Bug report ID if successfully captured, None if issue_key is invalid
            
        Raises:
            ValueError: If issue_key does not exist in KNOWN_ISSUES
            
        Example:
            >>> bug_id = issues.detect_and_capture(
            ...     issue_key="wake_word_zero_scores",
            ...     context={"scores": [0.0, 0.0, 0.0], "frame_count": 100},
            ...     session_id="sess_123",
            ...     user_context="Detected during morning startup",
            ... )
        """
        # Validate issue key
        if issue_key not in KNOWN_ISSUES:
            logger.warning(
                "unknown_issue_key",
                issue_key=issue_key,
                valid_keys=list(KNOWN_ISSUES.keys()),
            )
            # Still capture it but mark as unknown
            return self._capture_unknown_issue(issue_key, context, session_id)
        
        issue_info = KNOWN_ISSUES[issue_key]
        
        # Create detection record
        detection = IssueDetection(
            issue_key=issue_key,
            session_id=session_id,
            timestamp=datetime.now().isoformat(),
            context=context,
        )
        
        # Create bug report
        try:
            bug_id = self.bug_tracker.capture_error(
                error=KnownIssueError(issue_key, issue_info["component"]),
                component=issue_info["component"],
                severity=issue_info["severity"],
                title=issue_info["title"],
                user_context=user_context or self._format_user_context(issue_info, context),
                session_id=session_id,
            )
            
            detection.bug_id = bug_id
            
            # Track detection
            self._record_detection(detection)
            
            logger.info(
                "known_issue_captured",
                issue_key=issue_key,
                component=issue_info["component"],
                severity=issue_info["severity"].value,
                bug_id=bug_id,
                session_id=session_id,
            )
            
            return bug_id
            
        except Exception as e:
            logger.error(
                "failed_to_capture_known_issue",
                issue_key=issue_key,
                error=str(e),
            )
            raise
    
    def _capture_unknown_issue(
        self,
        issue_key: str,
        context: Dict[str, Any],
        session_id: str,
    ) -> Optional[int]:
        """
        Capture an unknown issue key as a generic bug.
        
        This handles cases where a issue_key is passed that isn't in KNOWN_ISSUES.
        Creates a generic bug report to ensure tracking.
        
        Args:
            issue_key: The unknown issue key
            context: Detection context
            session_id: Session identifier
            
        Returns:
            Bug report ID or None
        """
        detection = IssueDetection(
            issue_key=issue_key,
            session_id=session_id,
            timestamp=datetime.now().isoformat(),
            context=context,
        )
        
        try:
            bug_id = self.bug_tracker.capture_error(
                error=ValueError(f"Unknown issue key: {issue_key}"),
                component="unknown",
                severity=BugSeverity.MEDIUM,
                title=f"Unregistered known issue: {issue_key}",
                user_context=f"Context: {context}",
                session_id=session_id,
            )
            
            detection.bug_id = bug_id
            self._record_detection(detection)
            
            return bug_id
            
        except Exception as e:
            logger.error(
                "failed_to_capture_unknown_issue",
                issue_key=issue_key,
                error=str(e),
            )
            return None
    
    def _format_user_context(
        self,
        issue_info: Dict[str, Any],
        context: Dict[str, Any],
    ) -> str:
        """
        Format user context string for bug report.
        
        Args:
            issue_info: Known issue metadata
            context: Detection context
            
        Returns:
            Formatted user context string
        """
        lines = [
            f"Known Issue: {issue_info['title']}",
            f"",
            f"Description: {issue_info['description']}",
            f"",
            f"Debug Hint: {issue_info['hint']}",
            f"",
            f"Detection Context:",
        ]
        
        for key, value in context.items():
            lines.append(f"  {key}: {value}")
        
        return "\n".join(lines)
    
    def _record_detection(self, detection: IssueDetection) -> None:
        """
        Record a detection in session history.
        
        Args:
            detection: Detection record to store
        """
        session_id = detection.session_id
        
        if session_id not in self._detections:
            self._detections[session_id] = []
        
        self._detections[session_id].append(detection)
        
        # Update detection count
        key = f"{session_id}:{detection.issue_key}"
        self._detection_counts[key] = self._detection_counts.get(key, 0) + 1
    
    def has_detected(
        self,
        issue_key: str,
        session_id: Optional[str] = None,
    ) -> bool:
        """
        Check if an issue has been detected (in session or globally).
        
        Args:
            issue_key: Key identifying the known issue
            session_id: Optional session to check within (checks all if not provided)
            
        Returns:
            True if issue has been detected, False otherwise
            
        Example:
            >>> if issues.has_detected("wake_word_zero_scores", session_id="sess_123"):
            ...     print("Issue already logged in this session")
            >>> 
            >>> if issues.has_detected("audio_device_not_found"):
            ...     print("Issue has been detected at some point")
        """
        if session_id:
            # Check within specific session
            session_detections = self._detections.get(session_id, [])
            return any(d.issue_key == issue_key for d in session_detections)
        else:
            # Check all sessions
            return any(
                issue_key == detection.issue_key
                for detections in self._detections.values()
                for detection in detections
            )
    
    def get_detections(
        self,
        session_id: Optional[str] = None,
        issue_key: Optional[str] = None,
    ) -> List[IssueDetection]:
        """
        Get detection records, optionally filtered.
        
        Args:
            session_id: Filter to specific session
            issue_key: Filter to specific issue type
            
        Returns:
            List of matching detection records
        """
        if session_id:
            detections = self._detections.get(session_id, [])
        else:
            detections = [
                d
                for session_detections in self._detections.values()
                for d in session_detections
            ]
        
        if issue_key:
            detections = [d for d in detections if d.issue_key == issue_key]
        
        return detections
    
    def get_detection_count(
        self,
        issue_key: str,
        session_id: Optional[str] = None,
    ) -> int:
        """
        Get count of detections for an issue.
        
        Args:
            issue_key: Issue key to count
            session_id: Optional session to filter by
            
        Returns:
            Number of times the issue has been detected
        """
        if session_id:
            key = f"{session_id}:{issue_key}"
            return self._detection_counts.get(key, 0)
        else:
            return sum(
                count for key, count in self._detection_counts.items()
                if key.endswith(f":{issue_key}")
            )
    
    def clear_session(self, session_id: str) -> int:
        """
        Clear detection history for a session.
        
        Args:
            session_id: Session to clear
            
        Returns:
            Number of detections cleared
        """
        if session_id not in self._detections:
            return 0
        
        count = len(self._detections[session_id])
        del self._detections[session_id]
        
        # Clear detection counts for this session
        keys_to_remove = [
            k for k in self._detection_counts
            if k.startswith(f"{session_id}:")
        ]
        for key in keys_to_remove:
            del self._detection_counts[key]
        
        logger.debug(
            "session_detections_cleared",
            session_id=session_id,
            count=count,
        )
        
        return count
    
    def get_summary(self) -> Dict[str, Any]:
        """
        Get summary statistics of all detections.
        
        Returns:
            Dictionary with detection statistics
        """
        total_detections = sum(len(d) for d in self._detections.values())
        sessions = list(self._detections.keys())
        
        # Count by issue type
        issue_counts: Dict[str, int] = {}
        for detection in self.get_detections():
            issue_counts[detection.issue_key] = issue_counts.get(detection.issue_key, 0) + 1
        
        # Count by component
        component_counts: Dict[str, int] = {}
        for issue_key, count in issue_counts.items():
            issue_info = KNOWN_ISSUES.get(issue_key, {"component": "unknown"})
            comp = issue_info.get("component", "unknown")
            component_counts[comp] = component_counts.get(comp, 0) + count
        
        return {
            "total_detections": total_detections,
            "sessions_affected": len(sessions),
            "issue_counts": issue_counts,
            "component_counts": component_counts,
            "tracked_issues": len(KNOWN_ISSUES),
        }


# Singleton instance
_known_issues_instance: Optional[KnownIssues] = None


def get_known_issues() -> KnownIssues:
    """
    Get the singleton KnownIssues instance.
    
    Creates the instance on first call and returns the same instance
    on subsequent calls. This ensures consistent state tracking across
    the application.
    
    Returns:
        The singleton KnownIssues instance
        
    Example:
        >>> issues = get_known_issues()
        >>> issues.detect_and_capture("audio_device_not_found", context, session_id)
    """
    global _known_issues_instance
    
    if _known_issues_instance is None:
        _known_issues_instance = KnownIssues()
        logger.info("known_issues_singleton_created")
    
    return _known_issues_instance


def reset_known_issues() -> None:
    """
    Reset the singleton instance (primarily for testing).
    
    This clears the singleton instance, allowing a fresh instance
    to be created on the next call to get_known_issues().
    
    Warning:
        This also clears all detection history. Use with caution.
    """
    global _known_issues_instance
    _known_issues_instance = None
    logger.debug("known_issues_singleton_reset")