"""
Tests for Tool Chain Manager

Tests multi-step tool handling, context preservation, and result aggregation.
"""
import pytest
import asyncio
from unittest.mock import Mock, AsyncMock, patch

from bridge.tool_chain_manager import (
    ToolChainManager,
    ToolStep,
    ToolChainResult,
    ToolChainState,
    ToolResultStatus,
    execute_tool_chain,
)
from bridge.openclaw_middleware import OpenClawMiddleware


class TestToolStep:
    """Test ToolStep dataclass."""
    
    def test_create_step(self):
        """Test creating a tool step."""
        step = ToolStep(
            tool_name="search",
            params={"query": "weather"},
            description="Search for weather",
            timeout_seconds=10.0,
        )
        
        assert step.tool_name == "search"
        assert step.params == {"query": "weather"}
        assert step.description == "Search for weather"
        assert step.timeout_seconds == 10.0
        assert step.status == ToolResultStatus.PENDING
    
    def test_step_duration(self):
        """Test calculating step duration."""
        step = ToolStep(tool_name="test", params={})
        
        # No duration before execution
        assert step.duration is None
        
        # Simulate execution
        step.start_time = 1000.0
        step.end_time = 1005.5
        
        assert step.duration == 5.5
    
    def test_step_to_dict(self):
        """Test serializing step to dict."""
        step = ToolStep(
            tool_name="search",
            params={"q": "test"},
            description="Test search",
        )
        step.status = ToolResultStatus.SUCCESS
        step.result = {"items": ["a", "b"]}
        step.start_time = 1000.0
        step.end_time = 1002.0
        
        d = step.to_dict()
        
        assert d["tool_name"] == "search"
        assert d["params"] == {"q": "test"}
        assert d["status"] == "success"
        assert d["result"] == {"items": ["a", "b"]}
        assert d["duration"] == 2.0


class TestToolChainResult:
    """Test ToolChainResult dataclass."""
    
    def test_create_result(self):
        """Test creating a chain result."""
        steps = [
            ToolStep("search", {}, status=ToolResultStatus.SUCCESS),
            ToolStep("summarize", {}, status=ToolResultStatus.ERROR),
        ]
        
        result = ToolChainResult(
            success=False,
            steps=steps,
            error_message="Summarization failed",
            state=ToolChainState.ERROR,
        )
        
        assert result.success is False
        assert len(result.steps) == 2
        assert result.error_message == "Summarization failed"
        assert result.state == ToolChainState.ERROR
        assert result.completed_steps == 1
        assert result.failed_steps == 1
    
    def test_result_to_dict(self):
        """Test serializing result to dict."""
        steps = [ToolStep("test", {}, status=ToolResultStatus.SUCCESS)]
        result = ToolChainResult(
            success=True,
            steps=steps,
            final_result={"data": "value"},
            total_duration=5.0,
            state=ToolChainState.COMPLETED,
        )
        
        d = result.to_dict()
        
        assert d["success"] is True
        assert d["completed_steps"] == 1
        assert d["failed_steps"] == 0
        assert d["final_result"] == {"data": "value"}
        assert d["total_duration"] == 5.0


class TestToolChainManager:
    """Test ToolChainManager class."""
    
    def test_initialization(self):
        """Test creating manager."""
        manager = ToolChainManager(session_id="test-session")
        
        assert manager.session_id == "test-session"
        assert manager.state == ToolChainState.IDLE
        assert manager.max_chain_length == 5
        assert not manager.is_running
    
    def test_validate_empty_chain(self):
        """Test validating empty chain."""
        manager = ToolChainManager()
        is_valid, error = manager.validate_chain([])
        
        assert is_valid is False
        assert "empty" in error.lower()
    
    def test_validate_chain_too_long(self):
        """Test validating chain exceeding max length."""
        manager = ToolChainManager(max_chain_length=3)
        steps = [
            ToolStep(f"tool_{i}", {})
            for i in range(5)
        ]
        
        is_valid, error = manager.validate_chain(steps)
        
        assert is_valid is False
        assert "too long" in error.lower()
    
    def test_validate_circular_dependency(self):
        """Test validating chain with circular dependency."""
        manager = ToolChainManager()
        # True circular: step 0 depends on step 1, step 1 depends on step 0
        steps = [
            ToolStep("tool1", {}, depends_on=[1]),  # Depends on tool2 (future)
            ToolStep("tool2", {}, depends_on=[0]),  # Depends on tool1 (previous)
        ]
        
        is_valid, error = manager.validate_chain(steps)
        
        assert is_valid is False
        # Step 0 depending on step 1 is detected as "future step" first
        assert "future" in error.lower() or "depends" in error.lower()

    def test_validate_backward_dependency(self):
        """Test validating chain with backward only dependency (no cycle)."""
        manager = ToolChainManager()
        steps = [
            ToolStep("tool1", {}, depends_on=[1]),  # Depends on future step only
            ToolStep("tool2", {}),
        ]
        
        is_valid, error = manager.validate_chain(steps)
        
        assert is_valid is False
        assert "future" in error.lower() or "depends" in error.lower()
    
    def test_validate_valid_chain(self):
        """Test validating valid chain."""
        manager = ToolChainManager()
        steps = [
            ToolStep("search", {"query": "test"}),
            ToolStep("summarize", {"text": "..."}, depends_on=[0]),
        ]
        
        is_valid, error = manager.validate_chain(steps)
        
        assert is_valid is True
        assert error is None
    
    @pytest.mark.asyncio
    async def test_interrupt_chain(self):
        """Test interrupting a running chain."""
        manager = ToolChainManager()
        
        # Set up as running
        manager._state = ToolChainState.RUNNING
        manager._interrupted = False
        
        # Interrupt
        manager.interrupt()
        
        assert manager._interrupted is True
    
    def test_reset(self):
        """Test resetting manager state."""
        manager = ToolChainManager()
        manager._state = ToolChainState.RUNNING
        manager._interrupted = True
        manager._current_chain = [ToolStep("test", {})]
        
        manager.reset()
        
        assert manager.state == ToolChainState.IDLE
        assert manager._interrupted is False
        assert manager._current_chain is None
    
    def test_aggregate_results(self):
        """Test result aggregation."""
        manager = ToolChainManager()
        
        steps = [
            ToolStep("search", {}, status=ToolResultStatus.SUCCESS, result={"items": ["a", "b"]}),
            ToolStep("format", {}, status=ToolResultStatus.SUCCESS, result={"formatted": "a, b"}),
        ]
        
        aggregated = manager._aggregate_results(steps)
        
        assert "search" in aggregated
        assert "format" in aggregated
        assert "a, b" in aggregated


class TestExecuteToolChain:
    """Test the execute_tool_chain convenience function."""
    
    @pytest.mark.asyncio
    @pytest.mark.timeout(5)  # Prevent hanging tests
    async def test_execute_simple_chain(self):
        """Test executing a simple tool chain."""
        # Mock tool registry
        async def mock_search(query: str):
            return {"results": [f"Result for {query}"]}
        
        registry = {"search": mock_search}
        
        steps = [
            ToolStep("search", {"query": "test"}),
        ]
        
        result = await execute_tool_chain(
            steps=steps,
            tool_registry=registry,
            session_id="test-session",
        )
        
        assert result.success is True
        assert result.state == ToolChainState.COMPLETED
        assert len(result.steps) == 1
        assert result.steps[0].status == ToolResultStatus.SUCCESS
    
    @pytest.mark.asyncio
    @pytest.mark.timeout(5)  # Prevent hanging tests
    async def test_execute_with_dependencies(self):
        """Test executing chain with dependencies."""
        async def mock_search(query: str):
            return {"text": f"Results for {query}"}
        
        async def mock_summarize(text: str):
            return {"summary": f"Summary of: {text[:20]}..."}
        
        registry = {
            "search": mock_search,
            "summarize": mock_summarize,
        }
        
        steps = [
            ToolStep("search", {"query": "AI news"}),
            ToolStep("summarize", {"text": "..."}, depends_on=[0]),
        ]
        
        result = await execute_tool_chain(
            steps=steps,
            tool_registry=registry,
        )
        
        assert result.success is True
        assert result.steps[0].status == ToolResultStatus.SUCCESS
        assert result.steps[1].status == ToolResultStatus.SUCCESS
    
    @pytest.mark.asyncio
    @pytest.mark.timeout(5)  # Prevent hanging tests
    async def test_execute_with_error(self):
        """Test handling tool execution error."""
        async def failing_tool():
            raise ValueError("Tool failed!")
        
        registry = {"failing_tool": failing_tool}
        
        steps = [
            ToolStep("failing_tool", {}),
        ]
        
        result = await execute_tool_chain(
            steps=steps,
            tool_registry=registry,
        )
        
        assert result.success is False
        assert result.state == ToolChainState.ERROR
        assert result.steps[0].status == ToolResultStatus.ERROR
        assert "Tool failed!" in result.steps[0].error
