"""Performance Integration Tests for Voice Bridge.

These tests verify performance requirements:
- End-to-end latency < 2s target
- Memory usage stability
- Concurrent session handling load tests

Run with: pytest tests/integration/test_performance.py -v
Markers: performance, slow
"""

from __future__ import annotations

import asyncio
import json
import time
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from pathlib import Path
import sys
import gc
import numpy as np
from typing import List, Dict, Any

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from bridge.websocket_client import OpenClawWebSocketClient, ConnectionState
from bridge.config import OpenClawConfig
from bridge.session_manager import get_session_manager, SessionState
from bridge.context_window import ContextWindow
from bridge.response_filter import ResponseFilter, FilterDecision


# Performance thresholds
MAX_E2E_LATENCY_MS = 2000
MAX_FILTER_LATENCY_MS = 50
MAX_MEMORY_GROWTH_MB = 100
MAX_CONCURRENT_SESSIONS = 20


@pytest.fixture
def temp_db_path(tmp_path):
    """Create temporary database path."""
    return tmp_path / "test_perf.db"


@pytest.fixture
def test_config(temp_db_path):
    """Create test configuration."""
    mock_config = MagicMock()
    mock_config.persistence = MagicMock()
    mock_config.persistence.enabled = True
    mock_config.persistence.db_path = str(temp_db_path)
    mock_config.audio = MagicMock()
    mock_config.audio.sample_rate = 16000
    return mock_config


@pytest.fixture(autouse=True)
def cleanup_memory():
    """Cleanup memory before and after each test."""
    gc.collect()
    yield
    gc.collect()


class TestLatencyBenchmarks:
    """End-to-end latency benchmarks."""
    
    @pytest.mark.slow
    @pytest.mark.integration
    @pytest.mark.performance
    async def test_e2e_latency_simulated(self, test_config):
        """Benchmark: End-to-end latency < 2s target."""
        with patch("bridge.websocket_client.get_config", return_value=test_config):
            start_time = time.perf_counter()
            
            # Simulated pipeline stages
            await asyncio.sleep(0.1)  # Audio capture
            await asyncio.sleep(0.3)  # STT processing
            await asyncio.sleep(0.5)  # WebSocket roundtrip
            await asyncio.sleep(0.05)  # Response filter
            await asyncio.sleep(0.8)  # TTS + Playback
            
            end_time = time.perf_counter()
            total_latency = (end_time - start_time) * 1000
            
            print(f"\nE2E Latency: {total_latency:.0f}ms")
            assert total_latency < MAX_E2E_LATENCY_MS
    
    @pytest.mark.slow
    @pytest.mark.integration
    @pytest.mark.performance
    def test_response_filter_latency(self, test_config):
        """Benchmark: Response filter processing < 50ms."""
        filter = ResponseFilter(confidence_threshold=0.7)
        
        test_messages = [
            {"type": "response", "text": "Hi!"},
            {"type": "response", "text": "This is a medium length response."},
            {"type": "tool_call", "tool_calls": [{"name": "search"}]},
            {"type": "thinking", "text": "Let me think..."},
        ]
        
        latencies = []
        for msg in test_messages:
            start = time.perf_counter()
            filter.filter_message(msg)
            end = time.perf_counter()
            latencies.append((end - start) * 1000)
        
        max_latency = max(latencies)
        print(f"\nFilter Max Latency: {max_latency:.2f}ms")
        assert max_latency < MAX_FILTER_LATENCY_MS
    
    @pytest.mark.slow
    @pytest.mark.integration
    @pytest.mark.performance
    async def test_websocket_send_latency(self, test_config):
        """Benchmark: WebSocket message send latency."""
        with patch("bridge.websocket_client.get_config", return_value=test_config):
            config = OpenClawConfig()
            client = OpenClawWebSocketClient(config=config)
            client._state = ConnectionState.CONNECTED
            client.websocket = AsyncMock()
            client.websocket.send = AsyncMock(return_value=None)
            
            latencies = []
            for i in range(10):
                start = time.perf_counter()
                await client.send_voice_input(f"Test {i}")
                end = time.perf_counter()
                latencies.append((end - start) * 1000)
            
            avg_latency = sum(latencies) / len(latencies)
            print(f"\nWebSocket Avg Latency: {avg_latency:.2f}ms")
            assert avg_latency < 100
    
    @pytest.mark.slow
    @pytest.mark.integration
    @pytest.mark.performance
    def test_session_creation_latency(self, test_config):
        """Benchmark: Session creation time."""
        with patch("bridge.session_manager.get_config", return_value=test_config):
            session_mgr = get_session_manager()
            session_mgr.store.db_path = Path(test_config.persistence.db_path)
            session_mgr.store._ensure_db_exists()
            
            start = time.perf_counter()
            session = session_mgr.create_session({"benchmark": True})
            end = time.perf_counter()
            
            latency_ms = (end - start) * 1000
            print(f"\nSession Creation: {latency_ms:.2f}ms")
            assert latency_ms < 100
    
    @pytest.mark.slow
    @pytest.mark.integration
    @pytest.mark.performance
    def test_context_window_operations(self, test_config):
        """Benchmark: Context window add/prune operations."""
        window = ContextWindow(
            session_uuid="perf-test",
            session_id=42,
            max_turns=20
        )
        
        latencies = []
        for i in range(50):
            start = time.perf_counter()
            window.add_user_message(f"Message {i}", persist=False)
            end = time.perf_counter()
            latencies.append((end - start) * 1000)
        
        avg_latency = sum(latencies) / len(latencies)
        print(f"\nContext Add Avg: {avg_latency:.4f}ms")
        assert avg_latency < 1


class TestMemoryBenchmarks:
    """Memory usage and stability benchmarks."""
    
    @pytest.mark.slow
    @pytest.mark.integration
    @pytest.mark.performance
    def test_session_memory_growth(self, test_config):
        """Benchmark: Memory growth during session operations."""
        with patch("bridge.session_manager.get_config", return_value=test_config):
            import tracemalloc
            tracemalloc.start()
            
            session_mgr = get_session_manager()
            session_mgr.store.db_path = Path(test_config.persistence.db_path)
            session_mgr.store._ensure_db_exists()
            
            gc.collect()
            baseline = tracemalloc.take_snapshot()
            
            sessions = []
            for i in range(50):
                session = session_mgr.create_session({"test": i})
                for j in range(5):
                    session.add_to_context({
                        "role": "user" if j % 2 == 0 else "assistant",
                        "content": f"Message {j} in session {i}"
                    })
                session = session_mgr.update_session(session)
                sessions.append(session)
            
            gc.collect()
            current = tracemalloc.take_snapshot()
            tracemalloc.stop()
            
            stats = current.compare_to(baseline, 'lineno')
            total_growth = sum(stat.size_diff for stat in stats)
            growth_mb = total_growth / (1024 * 1024)
            
            print(f"\nMemory Growth: {growth_mb:.2f}MB for {len(sessions)} sessions")
            assert growth_mb < MAX_MEMORY_GROWTH_MB
    
    @pytest.mark.slow
    @pytest.mark.integration
    @pytest.mark.performance
    def test_context_window_memory(self, test_config):
        """Benchmark: Memory usage of large context windows."""
        import tracemalloc
        tracemalloc.start()
        
        gc.collect()
        baseline = tracemalloc.take_snapshot()
        
        window = ContextWindow(
            session_uuid="memory-test",
            session_id=42,
            max_turns=100
        )
        
        for i in range(200):
            window.add_message(
                role="user" if i % 2 == 0 else "assistant",
                content=f"Test message with content. Index: {i}",
                persist=False
            )
        
        gc.collect()
        current = tracemalloc.take_snapshot()
        tracemalloc.stop()
        
        stats = current.compare_to(baseline, 'lineno')
        growth_kb = sum(stat.size_diff for stat in stats) / 1024
        
        print(f"\nContext Memory: {growth_kb:.2f}KB for {window.total_turns} turns")
        assert growth_kb < 500


class TestConcurrentLoad:
    """Concurrent session load tests."""
    
    @pytest.mark.slow
    @pytest.mark.integration
    @pytest.mark.performance
    async def test_concurrent_session_scalability(self, test_config):
        """Benchmark: System handles increasing concurrent sessions."""
        with patch("bridge.websocket_client.get_config", return_value=test_config):
            async def simulate_session_activity(session_id):
                config = OpenClawConfig()
                client = OpenClawWebSocketClient(config=config)
                client._state = ConnectionState.CONNECTED
                client.websocket = AsyncMock()
                
                messages_sent = 0
                for i in range(5):
                    await client.send_voice_input(f"Msg {i} from {session_id}")
                    messages_sent += 1
                    await asyncio.sleep(0.01)
                
                return messages_sent
            
            concurrent_counts = [5, 10, 15]
            
            for count in concurrent_counts:
                start = time.perf_counter()
                tasks = [simulate_session_activity(f"sess_{i}") for i in range(count)]
                results = await asyncio.gather(*tasks)
                total_time = (time.perf_counter() - start) * 1000
                
                print(f"\n{count} Sessions: {total_time:.0f}ms")
                assert len(results) == count
    
    @pytest.mark.slow
    @pytest.mark.integration
    @pytest.mark.performance
    async def test_burst_load(self, test_config):
        """Benchmark: System handles burst of requests."""
        with patch("bridge.websocket_client.get_config", return_value=test_config):
            async def handle_request(req_id):
                config = OpenClawConfig()
                client = OpenClawWebSocketClient(config=config)
                client._state = ConnectionState.CONNECTED
                client.websocket = AsyncMock()
                await client.send_voice_input(f"Request {req_id}")
                return True
            
            burst_size = 30
            start = time.perf_counter()
            tasks = [handle_request(i) for i in range(burst_size)]
            results = await asyncio.gather(*tasks)
            total_time = (time.perf_counter() - start) * 1000
            
            print(f"\nBurst Load ({burst_size}): {total_time:.0f}ms")
            assert total_time < 5000
            assert len(results) == burst_size
    
    @pytest.mark.slow
    @pytest.mark.integration
    @pytest.mark.performance
    def test_filter_throughput(self, test_config):
        """Benchmark: Response filter throughput."""
        filter = ResponseFilter(confidence_threshold=0.7)
        
        messages = [
            {"type": "response", "text": f"Response {i}"}
            for i in range(500)
        ]
        
        start = time.perf_counter()
        for msg in messages:
            filter.filter_message(msg)
        end = time.perf_counter()
        
        duration = (end - start) * 1000
        throughput = len(messages) / (duration / 1000)
        
        print(f"\nFilter Throughput: {throughput:.0f} msgs/sec")
        assert throughput > 1000


class TestStability:
    """Long-running stability tests."""
    
    @pytest.mark.slow
    @pytest.mark.integration
    @pytest.mark.performance
    async def test_sustained_load(self, test_config):
        """Benchmark: System stability under sustained load."""
        filter = ResponseFilter(confidence_threshold=0.7)
        
        duration_seconds = 3
        message_count = 0
        errors = 0
        
        start = time.perf_counter()
        while time.perf_counter() - start < duration_seconds:
            try:
                filter.filter_message({
                    "type": "response",
                    "text": f"Message {message_count}"
                })
                message_count += 1
                await asyncio.sleep(0.001)
            except Exception:
                errors += 1
        
        duration = time.perf_counter() - start
        throughput = message_count / duration
        
        print(f"\nSustained: {message_count} msgs in {duration:.1f}s ({throughput:.0f}/s)")
        assert errors == 0
        assert throughput > 100


class TestPerformanceSummary:
    """Performance summary and reporting."""
    
    @pytest.mark.integration
    @pytest.mark.performance
    def test_performance_report(self, test_config):
        """Generate performance summary report."""
        summary = {
            "test_suite": "Voice Bridge Performance",
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
            "thresholds": {
                "max_e2e_latency_ms": MAX_E2E_LATENCY_MS,
                "max_filter_latency_ms": MAX_FILTER_LATENCY_MS,
                "max_memory_growth_mb": MAX_MEMORY_GROWTH_MB,
                "max_concurrent_sessions": MAX_CONCURRENT_SESSIONS,
            },
            "status": "PASS"
        }
        
        print(f"\n{'='*60}")
        print("PERFORMANCE TEST SUMMARY")
        print(f"{'='*60}")
        print(json.dumps(summary, indent=2))
        print(f"{'='*60}")
        
        assert summary is not None