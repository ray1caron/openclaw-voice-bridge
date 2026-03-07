"""
Tool Chain Manager for Multi-Step Tool Handling

Manages sequential tool calls in voice conversations, maintaining context
across tool executions and aggregating results for coherent final responses.
"""
import enum
import asyncio
import time
from dataclasses import dataclass, field
from typing import Optional, Dict, Any, List, Tuple, Callable, Coroutine, Union
from collections import deque

import structlog

from bridge.openclaw_middleware import (
    OpenClawMiddleware,
    TaggedMessage,
    MessageType,
    Speakability,
    wrap_tool_execution,
)
from bridge.middleware_integration import MiddlewareResponseFilter

logger = structlog.get_logger()


class ToolChainState(enum.Enum):
    """States for tool chain execution."""
    IDLE = "idle"                    # No active chain
    RUNNING = "running"              # Executing tools
    PAUSED = "paused"                # Interrupted, waiting
    COMPLETED = "completed"          # All tools done
    ERROR = "error"                  # Error occurred
    TIMEOUT = "timeout"              # Chain timed out


class ToolResultStatus(enum.Enum):
    """Status of individual tool execution."""
    PENDING = "pending"              # Not yet executed
    RUNNING = "running"              # Currently executing
    SUCCESS = "success"              # Completed successfully
    ERROR = "error"                  # Failed with error
    CANCELLED = "cancelled"          # Cancelled (e.g., interruption)
    TIMEOUT = "timeout"              # Timed out


@dataclass
class ToolStep:
    """A single step in a tool chain."""
    tool_name: str
    params: Dict[str, Any]
    description: Optional[str] = None
    depends_on: Optional[List[int]] = None  # Indices of prerequisite steps
    timeout_seconds: float = 30.0
    
    # Execution results (populated during execution)
    status: ToolResultStatus = field(default=ToolResultStatus.PENDING)
    result: Any = None
    error: Optional[str] = None
    start_time: Optional[float] = None
    end_time: Optional[float] = None
    
    @property
    def duration(self) -> Optional[float]:
        """Execution duration in seconds."""
        if self.start_time and self.end_time:
            return self.end_time - self.start_time
        return None
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "tool_name": self.tool_name,
            "params": self.params,
            "description": self.description,
            "depends_on": self.depends_on,
            "timeout_seconds": self.timeout_seconds,
            "status": self.status.value,
            "result": self.result,
            "error": self.error,
            "duration": self.duration,
        }


@dataclass
class ToolChainResult:
    """Result of executing a tool chain."""
    success: bool
    steps: List[ToolStep]
    final_result: Any = None
    aggregated_output: Optional[str] = None
    error_message: Optional[str] = None
    total_duration: float = 0.0
    state: ToolChainState = ToolChainState.IDLE
    
    @property
    def completed_steps(self) -> int:
        """Number of successfully completed steps."""
        return sum(1 for s in self.steps if s.status == ToolResultStatus.SUCCESS)
    
    @property
    def failed_steps(self) -> int:
        """Number of failed steps."""
        return sum(1 for s in self.steps if s.status == ToolResultStatus.ERROR)
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "success": self.success,
            "state": self.state.value,
            "completed_steps": self.completed_steps,
            "failed_steps": self.failed_steps,
            "total_duration": self.total_duration,
            "final_result": self.final_result,
            "aggregated_output": self.aggregated_output,
            "error_message": self.error_message,
            "steps": [s.to_dict() for s in self.steps],
        }


class ToolChainManager:
    """
    Manages sequential tool calls in voice conversations.
    
    Handles:\n    - Sequential tool execution with dependency resolution\n    - Context preservation across tool executions\n    - Result aggregation for final response\n    - Interruption handling during tool chains\n    - Timeout handling for long-running chains\n    - Error recovery and graceful degradation\n    
    Example usage:\n        manager = ToolChainManager()\n        \n        # Define a chain\n        steps = [\n            ToolStep(\"search\", {\"query\": \"AI news\"}),\n            ToolStep(\"summarize\", {\"text\": \"...\"}, depends_on=[0]),\n        ]\n        \n        # Execute\n        result = await manager.execute_chain(steps)\n    """
    
    def __init__(
        self,
        middleware: Optional[OpenClawMiddleware] = None,
        max_chain_length: int = 5,
        default_timeout: float = 30.0,
        on_step_complete: Optional[Callable[[ToolStep], None]] = None,
        on_chain_complete: Optional[Callable[[ToolChainResult], None]] = None,
        session_id: Optional[str] = None,
    ):
        """
        Initialize the tool chain manager.
        
        Args:
            middleware: OpenClawMiddleware instance (or create new)
            max_chain_length: Maximum number of tools in a chain\n            default_timeout: Default timeout per tool in seconds\n            on_step_complete: Callback when a step completes\n            on_chain_complete: Callback when chain completes\n        """
        self.middleware = middleware or OpenClawMiddleware()
        self.max_chain_length = max_chain_length
        self.default_timeout = default_timeout
        self.on_step_complete = on_step_complete
        self.on_chain_complete = on_chain_complete
        self.session_id = session_id
        
        self._state = ToolChainState.IDLE
        self._current_chain: Optional[List[ToolStep]] = None
        self._interrupted = False
        self._chain_start_time: Optional[float] = None
        
        logger.info(
            "tool_chain_manager.initialized",
            max_chain_length=max_chain_length,
            default_timeout=default_timeout,
            session_id=session_id,
        )
    
    @property
    def state(self) -> ToolChainState:
        """Current state of the tool chain manager."""
        return self._state
    
    @property
    def is_running(self) -> bool:
        """Check if a chain is currently running."""
        return self._state == ToolChainState.RUNNING
    
    def validate_chain(self, steps: List[ToolStep]) -> Tuple[bool, Optional[str]]:
        """
        Validate a tool chain before execution.
        
        Returns:
            (is_valid, error_message)
        """
        if not steps:
            return False, "Chain cannot be empty"
        
        if len(steps) > self.max_chain_length:
            return False, f"Chain too long ({len(steps)} > {self.max_chain_length})"
        
        # Build dependency graph and check for circular dependencies
        n = len(steps)
        adj = [[] for _ in range(n)]  # adjacency list
        
        for i, step in enumerate(steps):
            if step.depends_on:
                for dep_idx in step.depends_on:
                    if dep_idx < 0 or dep_idx >= n:
                        return False, f"Step {i} has invalid dependency {dep_idx}"
                    if dep_idx >= i:
                        return False, f"Step {i} depends on future step {dep_idx}"
                    adj[dep_idx].append(i)  # dep_idx must complete before i
        
        # Detect cycles using DFS
        WHITE, GRAY, BLACK = 0, 1, 2
        color = [WHITE] * n
        
        def dfs(node: int) -> bool:
            color[node] = GRAY
            for neighbor in adj[node]:
                if color[neighbor] == GRAY:
                    return True  # Back edge found = cycle
                if color[neighbor] == WHITE and dfs(neighbor):
                    return True
            color[node] = BLACK
            return False
        
        for i in range(n):
            if color[i] == WHITE:
                if dfs(i):
                    return False, "Circular dependency detected in chain"
        
        return True, None
    
    async def execute_chain(
        self,
        steps: List[ToolStep],
        tool_registry: Optional[Dict[str, Callable]] = None,
    ) -> ToolChainResult:
        """
        Execute a tool chain.
        
        Args:
            steps: List of tool steps to execute
            tool_registry: Mapping of tool names to callable functions
            
        Returns:
            ToolChainResult with execution results
        """
        # Validate chain
        is_valid, error = self.validate_chain(steps)
        if not is_valid:
            return ToolChainResult(
                success=False,
                steps=[],
                error_message=error,
                state=ToolChainState.ERROR,
            )
        
        # Check for interruption
        if self._interrupted:
            logger.warning("Chain execution interrupted before start")
            return ToolChainResult(
                success=False,
                steps=[],
                error_message="Chain interrupted",
                state=ToolChainState.ERROR,
            )
        
        # Initialize chain
        self._state = ToolChainState.RUNNING
        self._current_chain = steps
        self._chain_start_time = time.time()
        
        logger.info(
            "chain.execution_started",
            step_count=len(steps),
        )
        
        # Execute steps in order
        for i, step in enumerate(steps):
            if self._interrupted:
                logger.warning("Chain interrupted during execution", step=i)
                step.status = ToolResultStatus.CANCELLED
                self._state = ToolChainState.ERROR
                break
            
            # Wait for dependencies
            if step.depends_on:
                for dep_idx in step.depends_on:
                    dep_step = steps[dep_idx]
                    if dep_step.status != ToolResultStatus.SUCCESS:
                        step.status = ToolResultStatus.ERROR
                        step.error = f"Dependency {dep_idx} failed"
                        logger.error("Dependency failed", step=i, dependency=dep_idx)
                        break
                if step.status == ToolResultStatus.ERROR:
                    continue
            
            # Execute the step
            await self._execute_step(step, tool_registry)
            
            # Notify callback
            if self.on_step_complete:
                try:
                    self.on_step_complete(step)
                except Exception as e:
                    logger.error("Step complete callback failed", error=str(e))
        
        # Calculate results
        total_duration = time.time() - self._chain_start_time
        success = all(
            s.status == ToolResultStatus.SUCCESS 
            for s in steps
        ) and not self._interrupted
        
        # Determine final state
        if self._interrupted:
            final_state = ToolChainState.ERROR
        elif success:
            final_state = ToolChainState.COMPLETED
        else:
            final_state = ToolChainState.ERROR
        
        # Aggregate output
        aggregated = self._aggregate_results(steps)
        
        result = ToolChainResult(
            success=success,
            steps=steps,
            total_duration=total_duration,
            state=final_state,
            aggregated_output=aggregated,
            error_message=None if success else "One or more steps failed",
        )
        
        self._state = final_state
        self._current_chain = None
        
        # Notify callback
        if self.on_chain_complete:
            try:
                self.on_chain_complete(result)
            except Exception as e:
                logger.error("Chain complete callback failed", error=str(e))
        
        logger.info(
            "chain.execution_completed",
            success=success,
            step_count=len(steps),
            duration=total_duration,
        )
        
        return result
    
    async def _execute_step(
        self,
        step: ToolStep,
        tool_registry: Optional[Dict[str, Callable]] = None,
    ) -> None:
        """
        Execute a single tool step.
        
        Args:
            step: The step to execute
            tool_registry: Optional registry of tool functions
        """
        step.status = ToolResultStatus.RUNNING
        step.start_time = time.time()
        
        logger.info(
            "step.execution_started",
            tool=step.tool_name,
            step_params=step.params,
        )
        
        try:
            # Create tool call message via middleware
            call_msg = self.middleware.create_tool_call_message(
                step.tool_name,
                step.params,
            )
            
            # Execute the tool
            if tool_registry and step.tool_name in tool_registry:
                tool_fn = tool_registry[step.tool_name]
                
                # Wrap with timeout
                result = await asyncio.wait_for(
                    tool_fn(**step.params),
                    timeout=step.timeout_seconds,
                )
            else:
                # No tool registry or tool not found - simulate
                logger.warning(
                    "Tool not found in registry, simulating",
                    tool=step.tool_name,
                )
                result = {"status": "simulated", "tool": step.tool_name}
            
            # Create tool result message
            result_msg = self.middleware.create_tool_result_message(
                step.tool_name,
                result,
            )
            
            step.result = result
            step.status = ToolResultStatus.SUCCESS
            step.end_time = time.time()
            
            logger.info(
                "step.execution_completed",
                tool=step.tool_name,
                duration=step.duration,
            )
            
        except asyncio.TimeoutError:
            step.status = ToolResultStatus.TIMEOUT
            step.error = f"Timeout after {step.timeout_seconds}s"
            step.end_time = time.time()
            logger.error("step.timeout", tool=step.tool_name, timeout=step.timeout_seconds)
            
        except Exception as e:
            step.status = ToolResultStatus.ERROR
            step.error = str(e)
            step.end_time = time.time()
            logger.error("step.execution_failed", tool=step.tool_name, error=str(e))
    
    def interrupt(self) -> None:
        """Interrupt the current tool chain."""
        logger.warning("chain.interrupt_requested")
        self._interrupted = True
    
    def reset(self) -> None:
        """Reset the manager state."""
        self._state = ToolChainState.IDLE
        self._current_chain = None
        self._interrupted = False
        self._chain_start_time = None
        logger.info("chain_manager.reset")
    
    def _aggregate_results(self, steps: List[ToolStep]) -> str:
        """
        Aggregate tool results into a coherent output.
        
        This is a simple implementation - can be enhanced with LLM-based
        summarization for more complex aggregation.
        
        Args:
            steps: List of executed steps
            
        Returns:
            Aggregated output string
        """
        successful = [s for s in steps if s.status == ToolResultStatus.SUCCESS]
        
        if not successful:
            return "No tools completed successfully."
        
        # Simple aggregation - join results
        parts = []
        for step in successful:
            if isinstance(step.result, dict):
                # Extract key information from dict result
                result_str = step.result.get("summary") or step.result.get("result") or str(step.result)
            else:
                result_str = str(step.result)
            
            if step.description:
                parts.append(f"{step.description}: {result_str}")
            else:
                parts.append(f"{step.tool_name}: {result_str}")
        
        return "; ".join(parts)


# Convenience function for simple tool chain execution
async def execute_tool_chain(
    steps: List[ToolStep],
    tool_registry: Dict[str, Callable],
    session_id: Optional[str] = None,
    max_chain_length: int = 5,
    on_step_complete: Optional[Callable[[ToolStep], None]] = None,
) -> ToolChainResult:
    """
    Convenience function to execute a tool chain.
    
    Args:
        steps: List of tool steps to execute
        tool_registry: Mapping of tool names to callable functions
        session_id: Optional session ID
        max_chain_length: Maximum chain length
        on_step_complete: Callback when step completes
        
    Returns:
        ToolChainResult with execution results
    """
    manager = ToolChainManager(
        session_id=session_id,
        max_chain_length=max_chain_length,
        on_step_complete=on_step_complete,
    )
    return await manager.execute_chain(steps, tool_registry)


# Global manager instance
_tool_chain_manager: Optional[ToolChainManager] = None


def get_tool_chain_manager() -> ToolChainManager:
    """Get or create global tool chain manager.
    
    Returns:
        ToolChainManager instance
    """
    global _tool_chain_manager
    if _tool_chain_manager is None:
        _tool_chain_manager = ToolChainManager()
    return _tool_chain_manager
