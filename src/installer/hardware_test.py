"""Hardware Testing for Voice Bridge Installation.

Provides interactive testing for:
- Microphone input validation
- Speaker output validation
- Audio device compatibility
- Real-time audio feedback
"""

from __future__ import annotations

import asyncio
import tempfile
import wave
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import List, Optional, Tuple

import structlog

logger = structlog.get_logger()


class TestStatus(Enum):
    """Status of a hardware test."""
    PENDING = "pending"
    RUNNING = "running"
    PASSED = "passed"
    FAILED = "failed"
    SKIPPED = "skipped"
    ERROR = "error"


@dataclass
class HardwareTestResult:
    """Result of a hardware test."""
    test_name: str
    status: TestStatus
    message: str
    details: Optional[str] = None
    duration_ms: int = 0
    device_name: Optional[str] = None
    device_index: Optional[int] = None
    error: Optional[Exception] = None
    
    @property
    def passed(self) -> bool:
        return self.status == TestStatus.PASSED
    
    @property
    def failed(self) -> bool:
        return self.status in (TestStatus.FAILED, TestStatus.ERROR)
    
    def __str__(self) -> str:
        status_icon = {
            TestStatus.PENDING: "⏳",
            TestStatus.RUNNING: "🔄",
            TestStatus.PASSED: "✅",
            TestStatus.FAILED: "❌",
            TestStatus.SKIPPED: "⏭️",
            TestStatus.ERROR: "💥",
        }.get(self.status, "❓")
        
        result = f"{status_icon} {self.test_name}: {self.message}"
        if self.device_name:
            result += f" [{self.device_name}]"
        return result


class HardwareTester:
    """Tests audio hardware for Voice Bridge compatibility."""
    
    def __init__(self):
        """Initialize the hardware tester."""
        self.logger = structlog.get_logger()
        self._audio_available = False
        self._sounddevice = None
        self._numpy = None
        self._last_recording = None
        self._last_sample_rate = None
        self._check_dependencies()
    
    def _check_dependencies(self) -> None:
        """Check if audio dependencies are available."""
        try:
            import sounddevice as sd
            self._sounddevice = sd
            self._audio_available = True
        except ImportError:
            self.logger.warning("sounddevice not available - audio tests will be skipped")
            self._audio_available = False
        
        try:
            import numpy
            self._numpy = numpy
        except ImportError:
            self.logger.warning("numpy not available - some tests will be limited")
    
    @property
    def audio_available(self) -> bool:
        """Check if audio testing is available."""
        return self._audio_available
    
    def run_all_tests(self, interactive: bool = True) -> List[HardwareTestResult]:
        """Run all hardware tests.
        
        Args:
            interactive: Whether to prompt user for interactive tests
            
        Returns:
            List of test results
        """
        results = []
        
        # Run non-interactive tests first
        results.append(self.test_device_discovery())
        
        if not self._audio_available:
            results.append(HardwareTestResult(
                test_name="Audio Dependencies",
                status=TestStatus.SKIPPED,
                message="Audio libraries not available",
                details="Install sounddevice and numpy for audio testing",
            ))
            return results
        
        # Test input devices
        input_result = self.test_input_devices()
        results.append(input_result)
        
        # Test output devices
        output_result = self.test_output_devices()
        results.append(output_result)
        
        # Interactive tests
        if interactive:
            # Test microphone recording
            if input_result.passed:
                results.append(self.test_microphone_recording())
            
            # Test speaker playback
            if output_result.passed:
                results.append(self.test_speaker_playback())
        
        return results
    
    def test_device_discovery(self) -> HardwareTestResult:
        """Test that audio devices can be discovered."""
        import time
        start = time.time()
        
        if not self._audio_available:
            return HardwareTestResult(
                test_name="Device Discovery",
                status=TestStatus.SKIPPED,
                message="Audio libraries not available",
            )
        
        try:
            devices = self._sounddevice.query_devices()
            
            input_devices = [d for d in devices if d.get("max_input_channels", 0) > 0]
            output_devices = [d for d in devices if d.get("max_output_channels", 0) > 0]
            
            duration = int((time.time() - start) * 1000)
            
            return HardwareTestResult(
                test_name="Device Discovery",
                status=TestStatus.PASSED,
                message=f"Found {len(input_devices)} input, {len(output_devices)} output devices",
                details=f"Total devices: {len(devices)}",
                duration_ms=duration,
            )
            
        except Exception as e:
            self.logger.error("Device discovery failed", error=str(e))
            return HardwareTestResult(
                test_name="Device Discovery",
                status=TestStatus.ERROR,
                message="Failed to discover audio devices",
                error=e,
            )
    
    def test_input_devices(self) -> HardwareTestResult:
        """Test that input devices are available and valid."""
        import time
        start = time.time()
        
        if not self._audio_available:
            return HardwareTestResult(
                test_name="Input Devices",
                status=TestStatus.SKIPPED,
                message="Audio libraries not available",
            )
        
        try:
            devices = self._sounddevice.query_devices()
            input_devices = []
            
            for i, d in enumerate(devices):
                if d.get("max_input_channels", 0) > 0:
                    input_devices.append((i, d))
            
            duration = int((time.time() - start) * 1000)
            
            if not input_devices:
                return HardwareTestResult(
                    test_name="Input Devices",
                    status=TestStatus.FAILED,
                    message="No input devices found",
                    details="Please connect a microphone and try again",
                    duration_ms=duration,
                )
            
            # Get default input device
            default_input = self._sounddevice.default.device[0]
            default_device = devices[default_input] if default_input >= 0 else None
            
            device_name = default_device.get("name", "Unknown") if default_device else "None"
            device_info = f"Default: [{default_input}] {device_name}"
            
            return HardwareTestResult(
                test_name="Input Devices",
                status=TestStatus.PASSED,
                message=f"Found {len(input_devices)} input device(s)",
                details=device_info,
                duration_ms=duration,
                device_name=device_name,
                device_index=default_input,
            )
            
        except Exception as e:
            self.logger.error("Input device test failed", error=str(e))
            return HardwareTestResult(
                test_name="Input Devices",
                status=TestStatus.ERROR,
                message="Failed to test input devices",
                error=e,
            )
    
    def test_output_devices(self) -> HardwareTestResult:
        """Test that output devices are available and valid."""
        import time
        start = time.time()
        
        if not self._audio_available:
            return HardwareTestResult(
                test_name="Output Devices",
                status=TestStatus.SKIPPED,
                message="Audio libraries not available",
            )
        
        try:
            devices = self._sounddevice.query_devices()
            output_devices = []
            
            for i, d in enumerate(devices):
                if d.get("max_output_channels", 0) > 0:
                    output_devices.append((i, d))
            
            duration = int((time.time() - start) * 1000)
            
            if not output_devices:
                return HardwareTestResult(
                    test_name="Output Devices",
                    status=TestStatus.FAILED,
                    message="No output devices found",
                    details="Please connect speakers/headphones and try again",
                    duration_ms=duration,
                )
            
            # Get default output device
            default_output = self._sounddevice.default.device[1]
            default_device = devices[default_output] if default_output >= 0 else None
            
            device_name = default_device.get("name", "Unknown") if default_device else "None"
            device_info = f"Default: [{default_output}] {device_name}"
            
            return HardwareTestResult(
                test_name="Output Devices",
                status=TestStatus.PASSED,
                message=f"Found {len(output_devices)} output device(s)",
                details=device_info,
                duration_ms=duration,
                device_name=device_name,
                device_index=default_output,
            )
            
        except Exception as e:
            self.logger.error("Output device test failed", error=str(e))
            return HardwareTestResult(
                test_name="Output Devices",
                status=TestStatus.ERROR,
                message="Failed to test output devices",
                error=e,
            )
    
    def test_microphone_recording(self, duration_seconds: float = 3.0) -> HardwareTestResult:
        """Test microphone by recording a short sample.
        
        Args:
            duration_seconds: How long to record in seconds
            
        Returns:
            Test result with recording analysis
        """
        import time
        
        if not self._audio_available:
            return HardwareTestResult(
                test_name="Microphone Recording",
                status=TestStatus.SKIPPED,
                message="Audio libraries not available",
            )
        
        start = time.time()
        
        try:
            # Get default input device
            default_input = self._sounddevice.default.device[0]
            devices = self._sounddevice.query_devices()
            device_name = devices[default_input].get("name", "Unknown") if default_input >= 0 else "Unknown"
            
            self.logger.info("Starting microphone test", device=device_name, duration=duration_seconds)
            
            # Record audio
            sample_rate = 16000
            channels = 1
            
            recording = self._sounddevice.rec(
                int(duration_seconds * sample_rate),
                samplerate=sample_rate,
                channels=channels,
                device=default_input,
            )
            
            self._sounddevice.wait()  # Wait for recording to finish
            
            # Store recording for potential playback
            self._last_recording = recording
            self._last_sample_rate = sample_rate
            
            # Analyze recording
            if self._numpy:
                audio_data = recording.flatten()
                
                # Calculate audio metrics
                rms = self._numpy.sqrt(self._numpy.mean(audio_data ** 2))
                peak = self._numpy.max(self._numpy.abs(audio_data))
                silence_threshold = 0.01
                silent_samples = self._numpy.sum(self._numpy.abs(audio_data) < silence_threshold)
                silence_ratio = silent_samples / len(audio_data)
                
                duration_ms = int((time.time() - start) * 1000)
                
                # Determine if recording was successful
                if rms < 0.001:
                    status = TestStatus.FAILED
                    message = "No audio detected - microphone may be muted"
                    details = f"RMS: {rms:.6f}, Peak: {peak:.6f}"
                elif silence_ratio > 0.95:
                    status = TestStatus.FAILED
                    message = "Almost complete silence detected"
                    details = f"Silence ratio: {silence_ratio:.1%}"
                else:
                    status = TestStatus.PASSED
                    message = "Microphone recording successful"
                    details = f"RMS: {rms:.4f}, Peak: {peak:.4f}, Silence: {silence_ratio:.1%}"
                
                return HardwareTestResult(
                    test_name="Microphone Recording",
                    status=status,
                    message=message,
                    details=details,
                    duration_ms=duration_ms,
                    device_name=device_name,
                    device_index=default_input,
                )
            else:
                duration_ms = int((time.time() - start) * 1000)
                return HardwareTestResult(
                    test_name="Microphone Recording",
                    status=TestStatus.PASSED,
                    message="Recording completed (no numpy for analysis)",
                    duration_ms=duration_ms,
                    device_name=device_name,
                )
                
        except Exception as e:
            self.logger.error("Microphone test failed", error=str(e))
            return HardwareTestResult(
                test_name="Microphone Recording",
                status=TestStatus.ERROR,
                message="Failed to record from microphone",
                error=e,
            )
    
    def test_speaker_playback(self, frequency: int = 440, duration_seconds: float = 1.0) -> HardwareTestResult:
        """Test speakers by playing a test tone.
        
        Args:
            frequency: Frequency of test tone in Hz
            duration_seconds: Duration of test tone in seconds
            
        Returns:
            Test result with playback status
        """
        import time
        
        if not self._audio_available:
            return HardwareTestResult(
                test_name="Speaker Playback",
                status=TestStatus.SKIPPED,
                message="Audio libraries not available",
            )
        
        if not self._numpy:
            return HardwareTestResult(
                test_name="Speaker Playback",
                status=TestStatus.SKIPPED,
                message="numpy not available for tone generation",
            )
        
        start = time.time()
        
        try:
            # Get default output device
            default_output = self._sounddevice.default.device[1]
            devices = self._sounddevice.query_devices()
            device_name = devices[default_output].get("name", "Unknown") if default_output >= 0 else "Unknown"
            
            self.logger.info("Starting speaker test", device=device_name, frequency=frequency)
            
            # Generate test tone
            sample_rate = 44100
            t = self._numpy.linspace(0, duration_seconds, int(sample_rate * duration_seconds), False)
            tone = self._numpy.sin(2 * self._numpy.pi * frequency * t) * 0.5  # 50% volume
            
            # Play tone
            self._sounddevice.play(tone, sample_rate, device=default_output)
            self._sounddevice.wait()
            
            duration_ms = int((time.time() - start) * 1000)
            
            return HardwareTestResult(
                test_name="Speaker Playback",
                status=TestStatus.PASSED,
                message=f"Test tone ({frequency}Hz) played successfully",
                details=f"Duration: {duration_seconds}s, Device: [{default_output}]",
                duration_ms=duration_ms,
                device_name=device_name,
                device_index=default_output,
            )
            
        except Exception as e:
            self.logger.error("Speaker test failed", error=str(e))
            return HardwareTestResult(
                test_name="Speaker Playback",
                status=TestStatus.ERROR,
                message="Failed to play test tone",
                error=e,
            )
    
    def play_last_recording(self) -> HardwareTestResult:
        """Play back the last recorded audio through speakers.
        
        Returns:
            Test result with playback status
        """
        import time
        
        if not self._audio_available:
            return HardwareTestResult(
                test_name="Recording Playback",
                status=TestStatus.SKIPPED,
                message="Audio libraries not available",
            )
        
        if self._last_recording is None:
            return HardwareTestResult(
                test_name="Recording Playback",
                status=TestStatus.SKIPPED,
                message="No recording available - record first",
            )
        
        start = time.time()
        
        try:
            # Get default output device
            default_output = self._sounddevice.default.device[1]
            devices = self._sounddevice.query_devices()
            device_name = devices[default_output].get("name", "Unknown") if default_output >= 0 else "Unknown"
            
            self.logger.info("Playing back recording", device=device_name)
            
            # Normalize audio for playback (optional boost)
            if self._numpy:
                audio = self._last_recording.copy()
                max_val = self._numpy.max(self._numpy.abs(audio))
                if max_val > 0:
                    # Boost volume slightly for playback
                    audio = audio * (0.8 / max_val) if max_val < 0.5 else audio
            else:
                audio = self._last_recording
            
            # Play recording
            self._sounddevice.play(audio, self._last_sample_rate, device=default_output)
            self._sounddevice.wait()
            
            duration_ms = int((time.time() - start) * 1000)
            duration_sec = len(self._last_recording) / self._last_sample_rate
            
            return HardwareTestResult(
                test_name="Recording Playback",
                status=TestStatus.PASSED,
                message="Recording played successfully",
                details=f"Duration: {duration_sec:.1f}s, Device: [{default_output}]",
                duration_ms=duration_ms,
                device_name=device_name,
                device_index=default_output,
            )
            
        except Exception as e:
            self.logger.error("Recording playback failed", error=str(e))
            return HardwareTestResult(
                test_name="Recording Playback",
                status=TestStatus.ERROR,
                message="Failed to play recording",
                error=e,
            )
    
    def test_full_duplex(self) -> HardwareTestResult:
        """Test simultaneous input/output (full duplex).
        
        This tests whether the system can handle recording and playing
        at the same time, which is required for real-time voice interaction.
        """
        import time
        
        if not self._audio_available or not self._numpy:
            return HardwareTestResult(
                test_name="Full Duplex",
                status=TestStatus.SKIPPED,
                message="Audio libraries not available",
            )
        
        start = time.time()
        
        try:
            sample_rate = 16000
            duration = 0.5  # 500ms
            
            # Generate a short tone
            t = self._numpy.linspace(0, duration, int(sample_rate * duration), False)
            tone = self._numpy.sin(2 * self._numpy.pi * 440 * t) * 0.3
            
            # Start recording
            recording = self._sounddevice.rec(
                int(duration * sample_rate * 2),  # Record longer than playback
                samplerate=sample_rate,
                channels=1,
            )
            
            # Play tone while recording
            self._sounddevice.play(tone, sample_rate)
            self._sounddevice.wait()
            
            # Wait for recording to finish
            self._sounddevice.wait()
            
            duration_ms = int((time.time() - start) * 1000)
            
            return HardwareTestResult(
                test_name="Full Duplex",
                status=TestStatus.PASSED,
                message="Simultaneous input/output successful",
                details="System can handle full-duplex audio",
                duration_ms=duration_ms,
            )
            
        except Exception as e:
            self.logger.error("Full duplex test failed", error=str(e))
            return HardwareTestResult(
                test_name="Full Duplex",
                status=TestStatus.ERROR,
                message="Full duplex test failed",
                details="System may not support simultaneous input/output",
                error=e,
            )
    
    def get_device_recommendations(self) -> Tuple[Optional[int], Optional[int]]:
        """Get recommended input and output device indices.
        
        Returns:
            Tuple of (input_device_index, output_device_index)
        """
        if not self._audio_available:
            return None, None
        
        try:
            devices = self._sounddevice.query_devices()
            
            # Find best input device
            input_devices = [
                (i, d) for i, d in enumerate(devices)
                if d.get("max_input_channels", 0) > 0
            ]
            
            # Prefer devices with "USB" or "mic" in name
            input_idx = None
            for i, d in input_devices:
                name = d.get("name", "").lower()
                if "usb" in name or "mic" in name or "microphone" in name:
                    input_idx = i
                    break
            
            if input_idx is None and input_devices:
                # Use default or first available
                default = self._sounddevice.default.device[0]
                input_idx = default if default >= 0 else input_devices[0][0]
            
            # Find best output device
            output_devices = [
                (i, d) for i, d in enumerate(devices)
                if d.get("max_output_channels", 0) > 0
            ]
            
            output_idx = None
            for i, d in output_devices:
                name = d.get("name", "").lower()
                if "usb" in name or "speaker" in name or "headphone" in name:
                    output_idx = i
                    break
            
            if output_idx is None and output_devices:
                default = self._sounddevice.default.device[1]
                output_idx = default if default >= 0 else output_devices[0][0]
            
            return input_idx, output_idx
            
        except Exception as e:
            self.logger.error("Failed to get device recommendations", error=str(e))
            return None, None


def test_microphone(duration_seconds: float = 3.0) -> HardwareTestResult:
    """Convenience function to test microphone.
    
    Args:
        duration_seconds: Recording duration
        
    Returns:
        Test result
    """
    tester = HardwareTester()
    return tester.test_microphone_recording(duration_seconds)


def test_speakers(frequency: int = 440, duration_seconds: float = 1.0) -> HardwareTestResult:
    """Convenience function to test speakers.
    
    Args:
        frequency: Test tone frequency in Hz
        duration_seconds: Duration in seconds
        
    Returns:
        Test result
    """
    tester = HardwareTester()
    return tester.test_speaker_playback(frequency, duration_seconds)


def run_hardware_tests(interactive: bool = True) -> List[HardwareTestResult]:
    """Run all hardware tests.
    
    Args:
        interactive: Whether to run interactive tests
        
    Returns:
        List of all test results
    """
    tester = HardwareTester()
    return tester.run_all_tests(interactive)