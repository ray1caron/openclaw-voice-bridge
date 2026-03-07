"""
Unit tests for audio_buffer module.
"""
import threading
import time

import numpy as np
import pytest

from bridge.audio_buffer import AudioBuffer


class TestAudioBuffer:
    """Test cases for AudioBuffer class."""
    
    def test_init_default(self):
        """Test buffer initialization with defaults."""
        buffer = AudioBuffer()
        
        assert buffer.max_frames == 20
        assert buffer.frame_size == 480
        assert buffer.dtype == np.int16
        assert buffer.is_empty
        assert not buffer.is_full
        assert buffer.frame_count == 0
    
    def test_init_custom(self):
        """Test buffer initialization with custom parameters."""
        buffer = AudioBuffer(max_frames=50, frame_size=1024, dtype=np.float32)
        
        assert buffer.max_frames == 50
        assert buffer.frame_size == 1024
        assert buffer.dtype == np.float32
    
    def test_write_read(self):
        """Test basic write and read operations."""
        buffer = AudioBuffer(max_frames=5, frame_size=10)
        
        # Write a frame
        frame = np.ones(10, dtype=np.int16)
        result = buffer.write(frame)
        
        assert result is True
        assert buffer.frame_count == 1
        assert not buffer.is_empty
        
        # Read the frame
        read_frame = buffer.read()
        
        assert read_frame is not None
        assert len(read_frame) == 10
        assert buffer.frame_count == 0
        assert buffer.is_empty
    
    def test_write_non_blocking_full(self):
        """Test non-blocking write when buffer is full."""
        buffer = AudioBuffer(max_frames=2, frame_size=10)
        
        # Fill buffer
        frame = np.ones(10, dtype=np.int16)
        assert buffer.write(frame, block=False) is True
        assert buffer.write(frame, block=False) is True
        
        # Try to write to full buffer (non-blocking)
        result = buffer.write(frame, block=False)
        
        assert result is False
        assert buffer.frame_count == 2
        assert buffer.stats['overflow_count'] == 1
    
    def test_read_non_blocking_empty(self):
        """Test non-blocking read when buffer is empty."""
        buffer = AudioBuffer(max_frames=5, frame_size=10)
        
        result = buffer.read(block=False)
        
        assert result is None
        assert buffer.stats['underflow_count'] == 1
    
    def test_frame_size_mismatch(self):
        """Test handling of mismatched frame sizes."""
        buffer = AudioBuffer(max_frames=5, frame_size=10)
        
        # Write frame that's too small (should be padded)
        small_frame = np.ones(5, dtype=np.int16)
        buffer.write(small_frame)
        
        read_frame = buffer.read()
        assert len(read_frame) == 10  # Padded to expected size
        
        # Write frame that's too large (should be truncated)
        buffer.clear()
        large_frame = np.ones(15, dtype=np.int16)
        buffer.write(large_frame)
        
        read_frame = buffer.read()
        assert len(read_frame) == 10  # Truncated to expected size
    
    def test_clear(self):
        """Test buffer clear operation."""
        buffer = AudioBuffer(max_frames=5, frame_size=10)
        
        # Add frames
        frame = np.ones(10, dtype=np.int16)
        buffer.write(frame)
        buffer.write(frame)
        buffer.write(frame)
        
        assert buffer.frame_count == 3
        
        # Clear
        cleared = buffer.clear()
        
        assert cleared == 3
        assert buffer.frame_count == 0
        assert buffer.is_empty
    
    def test_peek(self):
        """Test peek operation."""
        buffer = AudioBuffer(max_frames=5, frame_size=10)
        
        # Peek empty buffer
        assert buffer.peek() is None
        
        # Add frame
        frame = np.arange(10, dtype=np.int16)
        buffer.write(frame)
        
        # Peek (should not remove)
        peeked = buffer.peek()
        assert peeked is not None
        assert len(peeked) == 10
        assert buffer.frame_count == 1  # Still there
        
        # Verify peeked is a copy
        assert not np.shares_memory(peeked, frame)
    
    def test_read_multiple(self):
        """Test reading multiple frames at once."""
        buffer = AudioBuffer(max_frames=5, frame_size=10)
        
        # Add frames
        for i in range(4):
            frame = np.full(10, i, dtype=np.int16)
            buffer.write(frame)
        
        # Read multiple
        frames = buffer.read_multiple(3)
        
        assert len(frames) == 3
        assert buffer.frame_count == 1
        
        # Verify content
        for i, frame in enumerate(frames):
            assert np.all(frame == i)
    
    def test_thread_safety(self):
        """Test thread-safe operations."""
        buffer = AudioBuffer(max_frames=100, frame_size=10)
        results = {"written": 0, "read": 0}
        
        def writer():
            for i in range(50):
                frame = np.full(10, i, dtype=np.int16)
                if buffer.write(frame, block=True, timeout=1.0):
                    results["written"] += 1
        
        def reader():
            for _ in range(50):
                frame = buffer.read(block=True, timeout=1.0)
                if frame is not None:
                    results["read"] += 1
        
        # Run concurrently
        writer_thread = threading.Thread(target=writer)
        reader_thread = threading.Thread(target=reader)
        
        writer_thread.start()
        reader_thread.start()
        writer_thread.join()
        reader_thread.join()
        
        # Verify results
        assert results["written"] > 0
        assert results["read"] > 0
    
    def test_context_manager(self):
        """Test context manager protocol."""
        frame = np.ones(10, dtype=np.int16)
        
        with AudioBuffer(max_frames=5, frame_size=10) as buffer:
            buffer.write(frame)
            assert buffer.frame_count == 1
        
        # After exit, buffer should be cleared
        assert buffer.frame_count == 0
    
    def test_stats_tracking(self):
        """Test statistics tracking."""
        buffer = AudioBuffer(max_frames=2, frame_size=10)
        
        # Write to fill
        frame = np.ones(10, dtype=np.int16)
        buffer.write(frame)
        buffer.write(frame)
        
        # Overflow (non-blocking write when full returns False, not counted)
        buffer.write(frame, block=False)
        
        # Read
        buffer.read()
        buffer.read()
        
        # Underflow
        buffer.read(block=False)
        
        stats = buffer.stats
        assert stats['overflow_count'] == 1
        assert stats['underflow_count'] == 1
        assert stats['total_written'] == 2  # Only 2 successful writes
        assert stats['total_read'] == 2
