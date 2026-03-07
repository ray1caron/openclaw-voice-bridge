"""
Standardized Result type for error handling.

This module provides a Result type inspired by Rust's Result enum,
offering consistent error handling across the voice bridge components.

Export: Result, BridgeError, ErrorSeverity
"""

from dataclasses import dataclass, field
from typing import Generic, TypeVar, Optional, Any
from enum import Enum

T = TypeVar('T')


class ErrorSeverity(Enum):
    """Error severity levels for categorizing failures."""
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


@dataclass
class BridgeError:
    """Standardized error representation for the voice bridge.
    
    Attributes:
        code: Unique error code identifier (e.g., "AUDIO_001", "VAD_TIMEOUT")
        message: Human-readable error description
        severity: Error severity level (WARNING, ERROR, CRITICAL)
        component: Component where the error originated (e.g., "audio_capture", "vad")
        exception: Optional underlying exception that caused this error
        context: Additional contextual information as key-value pairs
    """
    code: str
    message: str
    severity: ErrorSeverity = ErrorSeverity.ERROR
    component: str = "unknown"
    exception: Optional[Exception] = None
    context: dict = field(default_factory=dict)
    
    def __str__(self) -> str:
        return f"[{self.component}] {self.code}: {self.message}"
    
    def __repr__(self) -> str:
        return (
            f"BridgeError(code={self.code!r}, message={self.message!r}, "
            f"severity={self.severity}, component={self.component!r})"
        )


@dataclass
class Result(Generic[T]):
    """Standard result type for operations.
    
    A Result is either a success (Ok) containing data, or a failure (Err)
    containing a BridgeError. This pattern is inspired by Rust's Result enum
    and provides explicit error handling without exceptions.
    
    Attributes:
        success: True if operation succeeded, False if it failed
        data: The result data if successful
        error: The error information if failed
    
    Example:
        >>> def divide(a: int, b: int) -> Result[float]:
        ...     if b == 0:
        ...         return Result.fail(BridgeError(
        ...             code="DIV_ZERO",
        ...             message="Division by zero",
        ...             component="math"
        ...         ))
        ...     return Result.ok(a / b)
        ...
        >>> result = divide(10, 2)
        >>> if result.is_ok():
        ...     print(result.unwrap())
        5.0
    """
    success: bool
    data: Optional[T] = None
    error: Optional[BridgeError] = None
    
    @classmethod
    def ok(cls, data: T) -> "Result[T]":
        """Create a successful result containing data.
        
        Args:
            data: The result data
            
        Returns:
            A Result with success=True and the provided data
        """
        return cls(success=True, data=data)
    
    @classmethod
    def fail(cls, error: BridgeError) -> "Result[T]":
        """Create a failed result containing an error.
        
        Args:
            error: The error that occurred
            
        Returns:
            A Result with success=False and the provided error
        """
        return cls(success=False, error=error)
    
    @classmethod
    def from_exception(
        cls, 
        exc: Exception, 
        component: str, 
        code: str = "UNKNOWN"
    ) -> "Result[T]":
        """Create a failed result from an exception.
        
        Convenience method for wrapping exceptions into Results.
        
        Args:
            exc: The exception that occurred
            component: Component where the exception was caught
            code: Error code (defaults to "UNKNOWN")
            
        Returns:
            A Result with success=False and a BridgeError wrapping the exception
        """
        return cls(
            success=False,
            error=BridgeError(
                code=code,
                message=str(exc),
                severity=ErrorSeverity.ERROR,
                component=component,
                exception=exc,
            )
        )
    
    def is_ok(self) -> bool:
        """Check if the result is successful.
        
        Returns:
            True if success, False otherwise
        """
        return self.success
    
    def is_error(self) -> bool:
        """Check if the result is an error.
        
        Returns:
            True if error, False otherwise
        """
        return not self.success
    
    def unwrap(self) -> T:
        """Extract the data from a successful result.
        
        Raises:
            RuntimeError: If called on an error result
            
        Returns:
            The contained data
        """
        if not self.success:
            raise RuntimeError(f"Result is error: {self.error}")
        return self.data
    
    def unwrap_or(self, default: T) -> T:
        """Extract the data or return a default value.
        
        Args:
            default: Value to return if result is an error
            
        Returns:
            The contained data if successful, or the default value
        """
        return self.data if self.success else default
    
    def __repr__(self) -> str:
        if self.success:
            return f"Result.ok({self.data!r})"
        return f"Result.fail({self.error!r})"


__all__ = ["Result", "BridgeError", "ErrorSeverity"]