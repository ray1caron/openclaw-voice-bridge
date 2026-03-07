"""
Error capture decorators and context managers for automatic bug tracking.

Provides convenient ways to automatically capture errors in the bug tracking system
without manually calling BugTracker methods throughout the codebase.

Example usage:

    @capture_errors(component="audio", reraise=False)
    def process_audio(data):
        # Errors are automatically captured
        ...
    
    with capture_context(component="stt", user_context="transcribing audio"):
        # Errors in this block are captured
        result = transcribe(audio)
    
    # Stateful capture for components
    capture = ErrorCapture(component="tts")
    for text in texts:
        capture.run(lambda: synthesize(text))
"""

from __future__ import annotations

import functools
import traceback
from contextlib import contextmanager
from typing import (
    Any,
    Callable,
    Dict,
    Optional,
    TypeVar,
    ParamSpec,
    Union,
)

import structlog

from bridge.bug_tracker import BugTracker, BugSeverity

logger = structlog.get_logger()

P = ParamSpec("P")
T = TypeVar("T")


def capture_errors(
    component: str,
    *,
    severity: BugSeverity = BugSeverity.HIGH,
    title: Optional[str] = None,
    reraise: bool = True,
    user_context: Optional[str] = None,
    session_id: Optional[str] = None,
    default_return: Optional[Any] = None,
) -> Callable[[Callable[P, T]], Callable[P, T]]:
    """
    Decorator to automatically capture exceptions as bugs.
    
    Wraps a function to capture any exceptions that occur and report them
    to the bug tracking system. Can optionally suppress exceptions
    (reraise=False) and return a default value instead.
    
    Args:
        component: Component name where errors occur (e.g., "audio", "stt", "tts")
        severity: Bug severity level (default: HIGH)
        title: Custom bug title (default: uses exception message)
        reraise: Whether to re-raise the exception after capturing (default: True)
        user_context: Additional context to include in bug report
        session_id: Session identifier for tracking
        default_return: Value to return if reraise=False and error occurs
    
    Returns:
        Decorated function that captures errors
    
    Example:
        @capture_errors(component="audio", reraise=False, default_return=None)
        def load_audio(path: str) -> Optional[AudioData]:
            return AudioData.load(path)
        
        # Errors are captured and None is returned
        audio = load_audio("missing.wav")
    
    Note:
        Function name, args, and kwargs are automatically captured in the
        user_context for debugging purposes.
    """
    def decorator(func: Callable[P, T]) -> Callable[P, T]:
        @functools.wraps(func)
        def wrapper(*args: P.args, **kwargs: P.kwargs) -> T:
            # Build context with function details
            context_parts = [f"function={func.__name__}"]
            
            # Safely capture args (limit to avoid giant logs)
            try:
                args_str = ", ".join(repr(a)[:50] for a in args[:5])
                if len(args) > 5:
                    args_str += f", ... ({len(args)} total)"
                context_parts.append(f"args=[{args_str}]")
            except Exception:
                context_parts.append("args=<unable to serialize>")
            
            # Safely capture kwargs
            try:
                kwargs_str = ", ".join(f"{k}={repr(v)[:50]}" for k, v in list(kwargs.items())[:5])
                if len(kwargs) > 5:
                    kwargs_str += f", ... ({len(kwargs)} total)"
                context_parts.append(f"kwargs={{{kwargs_str}}}")
            except Exception:
                context_parts.append("kwargs=<unable to serialize>")
            
            # Combine with user-provided context
            auto_context = " | ".join(context_parts)
            full_context = f"{user_context} | {auto_context}" if user_context else auto_context
            
            try:
                return func(*args, **kwargs)
            except Exception as e:
                # Attempt to capture the bug
                bug_id = _try_capture_error(
                    error=e,
                    component=component,
                    severity=severity,
                    title=title,
                    user_context=full_context,
                    session_id=session_id,
                )
                
                logger.debug(
                    "error_captured_by_decorator",
                    function=func.__name__,
                    error_type=type(e).__name__,
                    bug_id=bug_id,
                    reraise=reraise,
                )
                
                if reraise:
                    raise
                else:
                    return default_return
        
        return wrapper
    
    return decorator


@contextmanager
def capture_context(
    component: str,
    *,
    severity: BugSeverity = BugSeverity.HIGH,
    title: Optional[str] = None,
    user_context: Optional[str] = None,
    session_id: Optional[str] = None,
    reraise: bool = True,
):
    """
    Context manager to capture errors within a code block.
    
    Use this to wrap code blocks where you want automatic error capture
    without using a decorator. Useful for one-off error handling or
    when you need more control over the scope.
    
    Args:
        component: Component name where errors occur
        severity: Bug severity level (default: HIGH)
        title: Custom bug title (default: uses exception message)
        user_context: Additional context to include in bug report
        session_id: Session identifier for tracking
        reraise: Whether to re-raise the exception after capturing (default: True)
    
    Yields:
        None
    
    Example:
        with capture_context(component="audio_pipeline", user_context="processing batch"):
            for audio_file in files:
                process_audio(audio_file)
        
        # If processing fails, error is captured and re-raised
        
        with capture_context(component="api", reraise=False):
            result = call_external_api()
        
        # If API fails, error is captured but execution continues
    """
    try:
        yield
    except Exception as e:
        # Capture the error
        bug_id = _try_capture_error(
            error=e,
            component=component,
            severity=severity,
            title=title,
            user_context=user_context,
            session_id=session_id,
        )
        
        logger.debug(
            "error_captured_by_context",
            component=component,
            error_type=type(e).__name__,
            bug_id=bug_id,
            reraise=reraise,
        )
        
        if reraise:
            raise


class ErrorCapture:
    """
    Stateful error capture for component-wide error handling.
    
    Maintains state across multiple operations, tracking success/failure
    rates and providing accumulated context. Useful for components that
    process multiple items and need consistent error handling.
    
    Example:
        capture = ErrorCapture(
            component="tts",
            user_context="batch synthesis",
            session_id="session-123"
        )
        
        # Process multiple items
        for text in texts:
            result = capture.run(lambda: synthesize(text))
            if result is None:
                print(f"Synthesis failed for: {text}")
        
        # Check stats
        stats = capture.get_stats()
        print(f"Success rate: {stats['success_rate']:.1%}")
        
        # Configure to not re-raise
        capture.reraise = False
        audio = capture.run(lambda: load_audio(path))
    """
    
    def __init__(
        self,
        component: str,
        *,
        severity: BugSeverity = BugSeverity.HIGH,
        user_context: Optional[str] = None,
        session_id: Optional[str] = None,
        reraise: bool = False,
        default_return: Any = None,
    ):
        """
        Initialize error capture for a component.
        
        Args:
            component: Component name for bug reports
            severity: Default bug severity level
            user_context: Context to add to all bug reports
            session_id: Session identifier for tracking
            reraise: Whether to re-raise exceptions (default: False)
            default_return: Value to return when errors occur and reraise=False
        """
        self.component = component
        self.severity = severity
        self.user_context = user_context
        self.session_id = session_id
        self.reraise = reraise
        self.default_return = default_return
        
        # Statistics
        self._total_calls = 0
        self._successful_calls = 0
        self._failed_calls = 0
        self._captured_bug_ids: list[int] = []
    
    def run(
        self,
        func: Callable[[], T],
        *,
        title: Optional[str] = None,
        severity: Optional[BugSeverity] = None,
        context: Optional[str] = None,
    ) -> Union[T, Any]:
        """
        Execute a function with automatic error capture.
        
        Args:
            func: Function to execute (no arguments, use lambda or partial)
            title: Custom bug title (overrides default)
            severity: Bug severity (overrides instance default)
            context: Additional context for this specific call
        
        Returns:
            Function result on success, or default_return on failure when reraise=False
        
        Example:
            capture = ErrorCapture(component="api")
            
            # Simple call
            result = capture.run(lambda: fetch_data())
            
            # With context
            result = capture.run(
                lambda: process_item(item),
                context=f"processing item {item.id}"
            )
        """
        self._total_calls += 1
        
        # Build combined context
        combined_parts = []
        if self.user_context:
            combined_parts.append(self.user_context)
        if context:
            combined_parts.append(context)
        combined_context = " | ".join(combined_parts) if combined_parts else None
        
        try:
            result = func()
            self._successful_calls += 1
            return result
            
        except Exception as e:
            self._failed_calls += 1
            
            # Capture the error
            bug_id = _try_capture_error(
                error=e,
                component=self.component,
                severity=severity or self.severity,
                title=title,
                user_context=combined_context,
                session_id=self.session_id,
            )
            
            if bug_id:
                self._captured_bug_ids.append(bug_id)
            
            logger.debug(
                "error_capture_run_failed",
                component=self.component,
                error_type=type(e).__name__,
                bug_id=bug_id,
                reraise=self.reraise,
            )
            
            if self.reraise:
                raise
            else:
                return self.default_return
    
    @contextmanager
    def context(
        self,
        *,
        title: Optional[str] = None,
        severity: Optional[BugSeverity] = None,
        context: Optional[str] = None,
    ):
        """
        Context manager for error capture with instance defaults.
        
        Use this when you need to wrap a code block with the component's
        pre-configured settings.
        
        Args:
            title: Custom bug title
            severity: Bug severity (overrides instance default)
            context: Additional context for this block
        
        Yields:
            None
        
        Example:
            capture = ErrorCapture(component="audio", reraise=False)
            
            with capture.context(context="loading config"):
                config = load_config()
                validate_config(config)
        """
        self._total_calls += 1
        
        # Build combined context
        combined_parts = []
        if self.user_context:
            combined_parts.append(self.user_context)
        if context:
            combined_parts.append(context)
        combined_context = " | ".join(combined_parts) if combined_parts else None
        
        try:
            yield
            self._successful_calls += 1
            
        except Exception as e:
            self._failed_calls += 1
            
            bug_id = _try_capture_error(
                error=e,
                component=self.component,
                severity=severity or self.severity,
                title=title,
                user_context=combined_context,
                session_id=self.session_id,
            )
            
            if bug_id:
                self._captured_bug_ids.append(bug_id)
            
            logger.debug(
                "error_capture_context_failed",
                component=self.component,
                error_type=type(e).__name__,
                bug_id=bug_id,
                reraise=self.reraise,
            )
            
            if self.reraise:
                raise
    
    def get_stats(self) -> Dict[str, Any]:
        """
        Get statistics about captured errors.
        
        Returns:
            Dictionary with error statistics:
            - total_calls: Total number of calls made
            - successful_calls: Number of successful calls
            - failed_calls: Number of failed calls
            - captured_bugs: Number of bugs captured
            - success_rate: Ratio of successful to total calls
            - bug_ids: List of captured bug IDs
        """
        success_rate = self._successful_calls / self._total_calls if self._total_calls > 0 else 0.0
        
        return {
            "component": self.component,
            "total_calls": self._total_calls,
            "successful_calls": self._successful_calls,
            "failed_calls": self._failed_calls,
            "captured_bugs": len(self._captured_bug_ids),
            "success_rate": success_rate,
            "bug_ids": list(self._captured_bug_ids),
        }
    
    def reset_stats(self) -> None:
        """Reset all statistics counters."""
        self._total_calls = 0
        self._successful_calls = 0
        self._failed_calls = 0
        self._captured_bug_ids.clear()
    
    @property
    def has_errors(self) -> bool:
        """Check if any errors have been captured."""
        return self._failed_calls > 0
    
    @property
    def bug_count(self) -> int:
        """Get the number of bugs captured."""
        return len(self._captured_bug_ids)
    
    def __repr__(self) -> str:
        """Return string representation with stats."""
        return (
            f"ErrorCapture(component={self.component!r}, "
            f"calls={self._total_calls}, "
            f"errors={self._failed_calls}, "
            f"bugs={len(self._captured_bug_ids)})"
        )


def capture_bug(
    error: Exception,
    component: str,
    *,
    severity: BugSeverity = BugSeverity.HIGH,
    title: Optional[str] = None,
    user_context: Optional[str] = None,
    session_id: Optional[str] = None,
) -> Optional[int]:
    """
    Convenience function to capture a bug from an exception.
    
    This is a simple wrapper around BugTracker.capture_error() that
    handles tracker initialization failures gracefully.
    
    Args:
        error: The exception that occurred
        component: Component name where error occurred
        severity: Bug severity level (default: HIGH)
        title: Custom bug title (default: uses exception message)
        user_context: Additional context for debugging
        session_id: Session identifier for tracking
    
    Returns:
        Bug report ID if captured successfully, None if capture failed
    
    Example:
        try:
            result = risky_operation()
        except Exception as e:
            bug_id = capture_bug(e, component="pipeline", user_context="during startup")
            logger.error(f"Bug #{bug_id} captured", exc_info=True)
            raise
    
    Note:
        This function never raises exceptions from capture failures.
        All capture errors are logged but do not interrupt execution.
    """
    return _try_capture_error(
        error=error,
        component=component,
        severity=severity,
        title=title,
        user_context=user_context,
        session_id=session_id,
    )


def _try_capture_error(
    error: Exception,
    component: str,
    severity: BugSeverity,
    title: Optional[str],
    user_context: Optional[str],
    session_id: Optional[str],
) -> Optional[int]:
    """
    Internal helper to safely attempt error capture.
    
    Handles all exceptions from the bug tracker itself, ensuring that
    capture failures never interrupt the calling code.
    
    Args:
        error: Exception to capture
        component: Component name
        severity: Bug severity
        title: Custom title
        user_context: Additional context
        session_id: Session ID
    
    Returns:
        Bug ID if captured, None on failure
    """
    try:
        tracker = BugTracker.get_instance()
        
        bug_id = tracker.capture_error(
            error=error,
            component=component,
            severity=severity,
            title=title,
            user_context=user_context,
            session_id=session_id,
        )
        
        return bug_id
        
    except Exception as capture_error:
        # Log the capture failure but don't propagate
        # The original error is more important than a capture failure
        logger.error(
            "failed_to_capture_bug",
            component=component,
            original_error=type(error).__name__,
            original_message=str(error)[:200],
            capture_error=type(capture_error).__name__,
            capture_message=str(capture_error)[:200],
        )
        
        # Try to log a minimal error record if possible
        try:
            logger.error(
                "bug_capture_failed_original_error",
                component=component,
                severity=severity.value,
                error_type=type(error).__name__,
                error_message=str(error),
                stack_trace=traceback.format_exc(),
                user_context=user_context,
                session_id=session_id,
            )
        except Exception:
            # If even logging fails, just silently return None
            pass
        
        return None