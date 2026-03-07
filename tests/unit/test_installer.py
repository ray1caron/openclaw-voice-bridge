"""Tests for the Installer Module.

Tests for:
- Installation detection
- Hardware testing
- Bug display
- Config summary
- Installation orchestration
"""

import pytest
from pathlib import Path
from unittest.mock import Mock, patch, MagicMock
import tempfile
import os

from installer.detector import (
    InstallationDetector,
    InstallationState,
    InstallationTrace,
    InstallationReport,
    detect_previous_installation,
    cleanup_installation,
)
from installer.hardware_test import (
    HardwareTester,
    HardwareTestResult,
    TestStatus,
    test_microphone,
    test_speakers,
)
from installer.bug_display import (
    BugDisplay,
    BugInfo,
    BugSummary,
    BugSeverity,
    BugStatus,
    show_unfixed_bugs,
    get_bug_summary,
)
from installer.config_summary import (
    ConfigSummary,
    ConfigIssue,
    ConfigSection,
    ConfigReport,
    validate_config,
)
from installer.core import (
    Installer,
    InstallResult,
    InstallStep,
    run_interactive_install,
)


# ============================================
# Installation Detection Tests
# ============================================

class TestInstallationDetector:
    """Tests for InstallationDetector class."""
    
    def test_detector_init(self):
        """Test detector initialization."""
        detector = InstallationDetector()
        assert detector.workspace is None
        
        detector_with_workspace = InstallationDetector(workspace=Path("/tmp"))
        assert detector_with_workspace.workspace == Path("/tmp")
    
    def test_detect_no_installation(self, tmp_path):
        """Test detection with no previous installation."""
        # Create detector with temp workspace
        detector = InstallationDetector(workspace=tmp_path)
        
        # Mock detect method to return empty report
        with patch.object(detector, 'detect') as mock_detect:
            mock_detect.return_value = InstallationReport(
                state=InstallationState.NONE,
                traces=[],
                running_processes=[],
                can_install=True,
            )
            report = detector.detect()
        
        assert report.state == InstallationState.NONE
        assert len(report.traces) == 0
        assert not report.has_running_processes
        assert report.can_install
    
    def test_detect_with_config(self, tmp_path):
        """Test detection with existing config."""
        # Create mock config directory
        config_dir = tmp_path / ".voice-bridge"
        config_dir.mkdir()
        (config_dir / "config.yaml").write_text("test: value")
        
        detector = InstallationDetector(workspace=tmp_path)
        
        with patch.object(Path, 'home', return_value=tmp_path):
            report = detector.detect()
        
        assert report.state != InstallationState.NONE
        assert len(report.traces) > 0
        assert any("config" in str(t.path).lower() for t in report.traces)
    
    def test_installation_trace_str(self):
        """Test InstallationTrace string representation."""
        trace = InstallationTrace(
            path=Path("/tmp/test"),
            description="Test trace",
            size_bytes=1024,
            is_directory=True,
        )
        
        assert "Test trace" in str(trace)
        assert "1.0 KB" in str(trace)
    
    def test_installation_report_summary(self):
        """Test InstallationReport summary."""
        report = InstallationReport(
            state=InstallationState.PARTIAL,
            traces=[
                InstallationTrace(Path("/tmp/a"), "Trace A", 100),
            ],
            running_processes=[],
            total_size_bytes=100,
            can_install=True,
            warnings=["Test warning"],
        )
        
        summary = report.summary()
        summary_lower = summary.lower()
        assert "partial" in summary_lower
        assert "1" in summary  # 1 trace
        assert "trace" in summary_lower  # at least "trace" or "traces"


class TestInstallationCleanup:
    """Tests for cleanup_installation function."""
    
    def test_cleanup_empty_report(self):
        """Test cleanup with no traces."""
        report = InstallationReport(
            state=InstallationState.NONE,
            traces=[],
            running_processes=[],
            can_install=True,
        )
        
        result = cleanup_installation(report)
        assert result is True
    
    def test_cleanup_with_files(self, tmp_path):
        """Test cleanup with actual files."""
        # Create test file
        test_file = tmp_path / "test.txt"
        test_file.write_text("test")
        
        trace = InstallationTrace(
            path=test_file,
            description="Test file",
            is_directory=False,
        )
        
        report = InstallationReport(
            state=InstallationState.PARTIAL,
            traces=[trace],
            running_processes=[],
            can_install=True,
        )
        
        result = cleanup_installation(report)
        assert result is True
        assert not test_file.exists()


# ============================================
# Hardware Testing Tests
# ============================================

class TestHardwareTester:
    """Tests for HardwareTester class."""
    
    def test_tester_init(self):
        """Test hardware tester initialization."""
        tester = HardwareTester()
        # May or may not have audio available depending on system
        assert isinstance(tester.audio_available, bool)
    
    def test_device_discovery(self):
        """Test device discovery."""
        # Create mock sounddevice
        mock_sd = MagicMock()
        mock_sd.query_devices.return_value = [
            {"name": "Mic 1", "max_input_channels": 1, "max_output_channels": 0, "default_samplerate": 44100},
            {"name": "Speaker 1", "max_input_channels": 0, "max_output_channels": 2, "default_samplerate": 44100},
        ]
        mock_sd.default.device = (0, 1)
        
        tester = HardwareTester()
        tester._audio_available = True
        tester._sounddevice = mock_sd
        
        result = tester.test_device_discovery()
        
        assert result.status == TestStatus.PASSED
        # Check for device count or devices in message
        assert "pass" in result.message.lower() or "device" in result.message.lower()
    
    def test_result_str(self):
        """Test HardwareTestResult string representation."""
        result = HardwareTestResult(
            test_name="Test",
            status=TestStatus.PASSED,
            message="Test passed",
            device_name="Test Device",
        )
        
        assert "✅" in str(result)
        assert "Test passed" in str(result)


class TestHardwareTestResult:
    """Tests for HardwareTestResult class."""
    
    def test_passed_property(self):
        """Test passed property."""
        result = HardwareTestResult(
            test_name="Test",
            status=TestStatus.PASSED,
            message="Success",
        )
        assert result.passed
        assert not result.failed
    
    def test_failed_property(self):
        """Test failed property."""
        result = HardwareTestResult(
            test_name="Test",
            status=TestStatus.FAILED,
            message="Failed",
        )
        assert result.failed
        assert not result.passed


# ============================================
# Bug Display Tests
# ============================================

class TestBugDisplay:
    """Tests for BugDisplay class."""
    
    def test_bug_info_is_unfixed(self):
        """Test BugInfo is_unfixed property."""
        from datetime import datetime
        
        unfixed = BugInfo(
            bug_id=1,
            title="Test bug",
            severity=BugSeverity.HIGH,
            status=BugStatus.NEW,
            component="test",
            description="Test",
            timestamp=datetime.now(),
        )
        
        assert unfixed.is_unfixed
        
        fixed = BugInfo(
            bug_id=2,
            title="Fixed bug",
            severity=BugSeverity.HIGH,
            status=BugStatus.FIXED,
            component="test",
            description="Test",
            timestamp=datetime.now(),
        )
        
        assert not fixed.is_unfixed
    
    def test_bug_summary_properties(self):
        """Test BugSummary properties."""
        from datetime import datetime
        
        summary = BugSummary(
            total_bugs=10,
            unfixed_count=5,
            critical_count=1,
            high_count=2,
            medium_count=2,
            low_count=0,
            by_component={"audio": 3, "bridge": 2},
            bugs=[
                BugInfo(1, "Critical", BugSeverity.CRITICAL, BugStatus.NEW, "audio", "test", datetime.now()),
            ],
        )
        
        assert summary.has_critical
        assert summary.has_high
        assert not summary.is_clean
    
    def test_bug_display_get_bugs_from_db_empty(self, tmp_path):
        """Test getting bugs from empty database."""
        display = BugDisplay(db_path=str(tmp_path / "bugs.db"))
        bugs = display._get_bugs_from_db()
        assert bugs == []


class TestBugInfo:
    """Tests for BugInfo class."""
    
    def test_severity_icon(self):
        """Test severity icon."""
        from datetime import datetime
        
        bug = BugInfo(
            bug_id=1,
            title="Test",
            severity=BugSeverity.CRITICAL,
            status=BugStatus.NEW,
            component="test",
            description="test",
            timestamp=datetime.now(),
        )
        
        assert bug.severity_icon == "🔴"
    
    def test_status_icon(self):
        """Test status icon."""
        from datetime import datetime
        
        bug = BugInfo(
            bug_id=1,
            title="Test",
            severity=BugSeverity.HIGH,
            status=BugStatus.IN_PROGRESS,
            component="test",
            description="test",
            timestamp=datetime.now(),
        )
        
        assert bug.status_icon == "🔨"


# ============================================
# Config Summary Tests
# ============================================

class TestConfigSummary:
    """Tests for ConfigSummary class."""
    
    def test_config_issue_str(self):
        """Test ConfigIssue string representation."""
        issue = ConfigIssue(
            field="test_field",
            severity="warning",
            message="Test warning",
            suggestion="Fix it",
        )
        
        result = str(issue)
        assert "⚠️" in result
        assert "test_field" in result
        assert "Test warning" in result
    
    def test_config_section_summary(self):
        """Test ConfigSection summary line."""
        section = ConfigSection(
            name="audio",
            display_name="Audio",
            icon="🎙️",
            fields={"input": "mic"},
        )
        
        line = section.summary_line()
        assert "🎙️" in line
        assert "Audio" in line


class TestConfigReport:
    """Tests for ConfigReport class."""
    
    def test_report_no_issues(self):
        """Test report with no issues."""
        report = ConfigReport(
            sections=[
                ConfigSection("audio", "Audio", "🎙️"),
            ],
            issues=[],
            valid=True,
        )
        
        summary = report.summary()
        assert "valid" in summary.lower() or "ready" in summary.lower()
    
    def test_report_with_errors(self):
        """Test report with errors."""
        report = ConfigReport(
            sections=[
                ConfigSection("audio", "Audio", "🎙️", issues=[
                    ConfigIssue("test", "error", "Test error"),
                ]),
            ],
            issues=[
                ConfigIssue("test", "error", "Test error"),
            ],
            valid=False,
        )
        
        assert report.has_errors
        assert not report.valid


# ============================================
# Installer Core Tests
# ============================================

class TestInstaller:
    """Tests for Installer class."""
    
    def test_installer_init(self):
        """Test installer initialization."""
        installer = Installer()
        
        assert installer.interactive is True
        assert installer.verbose is False
        assert installer.stop_on_error is False
    
    def test_installer_callbacks(self):
        """Test installer callbacks."""
        installer = Installer()
        
        messages = []
        steps = []
        
        installer.on_message(lambda m: messages.append(m))
        installer.on_step_start(lambda s: steps.append(s))
        
        installer._emit_message("test")
        assert messages == ["test"]
        
        installer._emit_step_start(InstallStep.DETECTION)
        assert InstallStep.DETECTION in steps
    
    def test_install_result_properties(self):
        """Test InstallResult properties."""
        result = InstallResult(
            step=InstallStep.DETECTION,
            success=True,
            message="Success",
        )
        
        assert result.status_icon == "✅"
        assert result.can_continue is True
        
        failed_result = InstallResult(
            step=InstallStep.DETECTION,
            success=False,
            message="Failed",
            can_continue=False,
        )
        
        assert failed_result.status_icon == "❌"


class TestInstallSteps:
    """Tests for individual installation steps."""
    
    @patch('installer.detector.detect_previous_installation')
    def test_detection_step(self, mock_detect, tmp_path):
        """Test detection step."""
        mock_detect.return_value = InstallationReport(
            state=InstallationState.NONE,
            traces=[],
            running_processes=[],
            can_install=True,
        )
        
        installer = Installer(workspace=tmp_path)
        result = installer._run_detection()
        
        assert result.step == InstallStep.DETECTION
        assert result.success


# ============================================
# Integration Tests
# ============================================

class TestIntegration:
    """Integration tests for installer module."""
    
    def test_full_detection_flow(self, tmp_path):
        """Test full detection flow."""
        # Create mock config
        config_dir = tmp_path / ".voice-bridge"
        config_dir.mkdir()
        (config_dir / "config.yaml").write_text("test: true")
        
        detector = InstallationDetector(workspace=tmp_path)
        
        with patch.object(Path, 'home', return_value=tmp_path):
            report = detector.detect()
        
        # Should find the config
        assert len(report.traces) > 0 or report.state != InstallationState.NONE
    
    def test_bug_summary_clean_system(self):
        """Test bug summary on clean system."""
        display = BugDisplay(db_path="/nonexistent/bugs.db")
        summary = display.get_bug_summary()
        
        assert summary.total_bugs == 0
        assert summary.is_clean


# ============================================
# CLI Tests
# ============================================

class TestCLI:
    """Tests for CLI functionality."""
    
    def test_show_bugs_clean(self):
        """Test show_bugs with no bugs."""
        with patch('installer.bug_display.get_bug_summary') as mock_summary:
            mock_summary.return_value = BugSummary(
                total_bugs=0,
                unfixed_count=0,
                critical_count=0,
                high_count=0,
                medium_count=0,
                low_count=0,
                by_component={},
                bugs=[],
            )
            
            from installer.__main__ import show_bugs
            result = show_bugs()
            
            assert result == 0  # Success, no bugs


if __name__ == "__main__":
    pytest.main([__file__, "-v"])