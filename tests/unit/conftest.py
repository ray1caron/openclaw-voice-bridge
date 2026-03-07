"""Unit test configuration and fixtures."""

from __future__ import annotations

import sys
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

import pytest
import numpy as np
from unittest.mock import MagicMock, AsyncMock
import asyncio


@pytest.fixture
def sample_audio_frame():
    """Create a sample audio frame for testing."""
    return np.zeros(480, dtype=np.int16)


@pytest.fixture
def high_energy_audio_frame():
    """Create a high-energy audio frame simulating speech."""
    return np.full(480, 2000, dtype=np.int16)


@pytest.fixture(autouse=True)
def mock_sounddevice(monkeypatch):
    """Automatically mock sounddevice for all tests."""
    # Create mock devices
    mock_devices = [
        {
            'name': 'Test Input Device',
            'max_input_channels': 2,
            'max_output_channels': 0,
            'default_samplerate': 16000
        },
        {
            'name': 'Test Output Device',
            'max_input_channels': 0,
            'max_output_channels': 2,
            'default_samplerate': 48000
        },
    ]
    
    def mock_query_devices(kind=None):
        if kind == 'input':
            return mock_devices[0]
        elif kind == 'output':
            return mock_devices[1]
        return mock_devices
    
    # Mock sounddevice module
    mock_sd = MagicMock()
    mock_sd.query_devices = mock_query_devices
    mock_sd.query_devices.return_value = mock_devices
    mock_sd.query_devices.__getitem__ = lambda *_: mock_devices[0]
    
    # Patch sounddevice before any imports
    monkeypatch.setattr(sys, 'modules', {**sys.modules, 'sounddevice': mock_sd})
    
    yield mock_sd


@pytest.fixture
def event_loop():
    """Create an instance of the default event loop for each test case."""
    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()
