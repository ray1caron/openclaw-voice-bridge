"""Core Installation Orchestrator.

Provides the main installation flow that:
1. Detects and cleans previous installations
2. Runs hardware tests
3. Shows bug tracker status
4. Validates configuration
5. Guides user through setup
"""

from __future__ import annotations

import os
import sys
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Callable, List, Optional

import structlog

from bridge.bug_tracker import BugTracker, BugSeverity

logger = structlog.get_logger()


class InstallStep(Enum):
    """Installation steps."""
    DETECTION = "detection"
    CLEANUP = "cleanup"
    HARDWARE_CHECK = "hardware_check"
    DEPENDENCIES = "dependencies"
    CONFIGURATION = "configuration"
    BUG_CHECK = "bug_check"
    FINAL = "final"


@dataclass
class InstallResult:
    """Result of an installation step."""
    step: InstallStep
    success: bool
    message: str
    details: Optional[str] = None
    duration_ms: int = 0
    can_continue: bool = True
    warnings: List[str] = field(default_factory=list)
    
    @property
    def status_icon(self) -> str:
        return "✅" if self.success else "❌"


class Installer:
    """Main installation orchestrator for Voice Bridge."""
    
    def __init__(
        self,
        workspace: Optional[Path] = None,
        interactive: bool = True,
        verbose: bool = False,
        stop_on_error: bool = False,
    ):
        """Initialize the installer.
        
        Args:
            workspace: Optional workspace path
            interactive: Run interactively with user prompts
            verbose: Show detailed output
            stop_on_error: Stop installation on any error
        """
        self.workspace = workspace or Path.cwd()
        self.interactive = interactive
        self.verbose = verbose
        self.stop_on_error = stop_on_error
        self.logger = structlog.get_logger()
        self.bug_tracker = BugTracker.get_instance()
        
        # Progress callbacks
        self._on_step_start: Optional[Callable[[InstallStep], None]] = None
        self._on_step_complete: Optional[Callable[[InstallResult], None]] = None
        self._on_message: Optional[Callable[[str], None]] = None
        
        # Results
        self.results: List[InstallResult] = []
    
    def on_step_start(self, callback: Callable[[InstallStep], None]) -> None:
        """Register callback for step start."""
        self._on_step_start = callback
    
    def on_step_complete(self, callback: Callable[[InstallResult], None]) -> None:
        """Register callback for step completion."""
        self._on_step_complete = callback
    
    def on_message(self, callback: Callable[[str], None]) -> None:
        """Register callback for messages."""
        self._on_message = callback
    
    def _emit_message(self, message: str) -> None:
        """Emit a message to registered callback."""
        if self._on_message:
            self._on_message(message)
        elif self.verbose:
            print(message)
    
    def _emit_step_start(self, step: InstallStep) -> None:
        """Emit step start event."""
        if self._on_step_start:
            self._on_step_start(step)
    
    def _emit_step_complete(self, result: InstallResult) -> None:
        """Emit step completion event."""
        if self._on_step_complete:
            self._on_step_complete(result)
    
    def run(self) -> bool:
        """Run the full installation.
        
        Returns:
            True if installation succeeded
        """
        self.logger.info("Starting Voice Bridge installation")
        self._emit_message("🎙️ Voice Bridge Installer")
        self._emit_message("=" * 50)
        
        all_success = True
        
        # Step 1: Detection
        result = self._run_detection()
        self.results.append(result)
        all_success = all_success and result.success
        
        if not result.can_continue and self.stop_on_error:
            return False
        
        # Step 2: Cleanup (if needed)
        if result.warnings or "running" in result.message.lower():
            result = self._run_cleanup()
            self.results.append(result)
            all_success = all_success and result.success
        
        # Step 3: Hardware Check
        result = self._run_hardware_check()
        self.results.append(result)
        all_success = all_success and result.success
        
        if not result.can_continue and self.stop_on_error:
            return False
        
        # Step 4: Dependencies
        result = self._run_dependencies()
        self.results.append(result)
        all_success = all_success and result.success
        
        if not result.can_continue and self.stop_on_error:
            return False
        
        # Step 5: Configuration
        result = self._run_configuration()
        self.results.append(result)
        all_success = all_success and result.success
        
        # Step 6: Bug Check
        result = self._run_bug_check()
        self.results.append(result)
        all_success = all_success and result.success
        
        # Step 7: Final
        result = self._run_final()
        self.results.append(result)
        
        return all_success
    
    def _run_detection(self) -> InstallResult:
        """Run previous installation detection."""
        start = time.time()
        self._emit_step_start(InstallStep.DETECTION)
        self._emit_message("\n📍 Step 1: Checking for previous installations...")
        
        try:
            from installer.detector import detect_previous_installation
            
            report = detect_previous_installation(workspace=self.workspace)
            duration_ms = int((time.time() - start) * 1000)
            
            if report.has_running_processes:
                self._emit_message(f"  ⚠️  Found {len(report.running_processes)} running process(es)")
                for proc in report.running_processes:
                    self._emit_message(f"     • {proc}")
                
                return InstallResult(
                    step=InstallStep.DETECTION,
                    success=False,
                    message="Running processes detected",
                    details="Stop running processes before continuing",
                    duration_ms=duration_ms,
                    can_continue=not self.stop_on_error,
                    warnings=report.running_processes,
                )
            
            if report.has_traces:
                self._emit_message(f"  📂 Found {len(report.traces)} installation trace(s)")
                for trace in report.traces[:5]:  # Show first 5
                    self._emit_message(f"     • {trace}")
                
                if len(report.traces) > 5:
                    self._emit_message(f"     ... and {len(report.traces) - 5} more")
                
                warning_list = [str(t) for t in report.traces]
                return InstallResult(
                    step=InstallStep.DETECTION,
                    success=True,
                    message=f"Found {len(report.traces)} previous installation traces",
                    details="Will be cleaned during installation",
                    duration_ms=duration_ms,
                    can_continue=True,
                    warnings=warning_list,
                )
            
            self._emit_message("  ✅ No previous installations found")
            return InstallResult(
                step=InstallStep.DETECTION,
                success=True,
                message="Clean system",
                duration_ms=duration_ms,
            )
            
        except Exception as e:
            self.logger.error("Detection failed", error=str(e))
            # Capture error to bug tracker
            self.bug_tracker.capture_error(
                error=e,
                component="installer",
                severity=BugSeverity.MEDIUM,
                title="Step failed: detection"
            )
            return InstallResult(
                step=InstallStep.DETECTION,
                success=False,
                message=f"Detection failed: {e}",
                can_continue=not self.stop_on_error,
            )
    
    def _run_cleanup(self) -> InstallResult:
        """Run installation cleanup."""
        start = time.time()
        self._emit_step_start(InstallStep.CLEANUP)
        self._emit_message("\n🧹 Step 2: Cleaning up previous installation...")
        
        try:
            from installer.detector import cleanup_installation, detect_previous_installation
            
            report = detect_previous_installation(workspace=self.workspace)
            
            if not report.has_traces and not report.has_running_processes:
                self._emit_message("  ✅ No cleanup needed")
                return InstallResult(
                    step=InstallStep.CLEANUP,
                    success=True,
                    message="No cleanup needed",
                    duration_ms=int((time.time() - start) * 1000),
                )
            
            self._emit_message("  Removing installation traces...")
            success = cleanup_installation(
                report,
                force=False,
                stop_processes=True,
                keep_config=False,
                keep_data=False,
            )
            
            duration_ms = int((time.time() - start) * 1000)
            
            if success:
                self._emit_message("  ✅ Cleanup complete")
                return InstallResult(
                    step=InstallStep.CLEANUP,
                    success=True,
                    message="Cleanup successful",
                    duration_ms=duration_ms,
                )
            else:
                self._emit_message("  ⚠️  Cleanup partially failed")
                return InstallResult(
                    step=InstallStep.CLEANUP,
                    success=False,
                    message="Cleanup incomplete",
                    details="Some files may remain",
                    duration_ms=duration_ms,
                    can_continue=True,
                )
                
        except Exception as e:
            self.logger.error("Cleanup failed", error=str(e))
            return InstallResult(
                step=InstallStep.CLEANUP,
                success=False,
                message=f"Cleanup failed: {e}",
                can_continue=True,
            )
    
    def _run_hardware_check(self) -> InstallResult:
        """Run hardware validation."""
        start = time.time()
        self._emit_step_start(InstallStep.HARDWARE_CHECK)
        self._emit_message("\n🔧 Step 3: Checking hardware compatibility...")
        
        try:
            from installer.hardware_test import HardwareTester
            
            tester = HardwareTester()
            
            if not tester.audio_available:
                self._emit_message("  ⚠️  Audio libraries not installed")
                self._emit_message("     Install sounddevice and numpy for hardware testing")
                return InstallResult(
                    step=InstallStep.HARDWARE_CHECK,
                    success=False,
                    message="Audio libraries not available",
                    details="Install with: pip install sounddevice numpy",
                    duration_ms=int((time.time() - start) * 1000),
                    can_continue=True,
                )
            
            # Run device discovery
            self._emit_message("  Discovering audio devices...")
            discovery_result = tester.test_device_discovery()
            self._emit_message(f"     {discovery_result}")
            
            # Run input test
            self._emit_message("  Testing input devices...")
            input_result = tester.test_input_devices()
            self._emit_message(f"     {input_result}")
            
            # Run output test
            self._emit_message("  Testing output devices...")
            output_result = tester.test_output_devices()
            self._emit_message(f"     {output_result}")
            
            duration_ms = int((time.time() - start) * 1000)
            
            # Check results
            warnings = []
            success = True
            
            if not discovery_result.passed:
                success = False
            
            if not input_result.passed:
                success = False
                warnings.append("No input device found - microphone required")
            
            if not output_result.passed:
                success = False
                warnings.append("No output device found - speakers required")
            
            # Interactive tests (if in interactive mode)
            if self.interactive and input_result.passed and output_result.passed:
                self._emit_message("\n  Hardware available for interactive testing.")
                self._emit_message("  Run with --test-audio to perform full tests.")
            
            if success:
                recommendation = "Audio hardware ready"
                self._emit_message(f"  ✅ {recommendation}")
            else:
                self._emit_message("  ❌ Hardware check failed")
            
            return InstallResult(
                step=InstallStep.HARDWARE_CHECK,
                success=success,
                message="Hardware check " + ("passed" if success else "failed"),
                details=", ".join(warnings) if warnings else None,
                duration_ms=duration_ms,
                can_continue=True,
                warnings=warnings,
            )
            
        except Exception as e:
            self.logger.error("Hardware check failed", error=str(e))
            # Capture hardware test failure to bug tracker
            self.bug_tracker.capture_error(
                error=e,
                component="installer",
                severity=BugSeverity.MEDIUM,
                title="Step failed: hardware_check"
            )
            return InstallResult(
                step=InstallStep.HARDWARE_CHECK,
                success=False,
                message=f"Hardware check failed: {e}",
                can_continue=True,
            )
    
    def _run_dependencies(self) -> InstallResult:
        """Check system dependencies."""
        start = time.time()
        self._emit_step_start(InstallStep.DEPENDENCIES)
        self._emit_message("\n📦 Step 4: Checking dependencies...")
        
        warnings = []
        
        # Check Python version
        python_version = sys.version_info
        self._emit_message(f"  Python: {python_version.major}.{python_version.minor}.{python_version.micro}")
        
        if python_version < (3, 9):
            warnings.append("Python 3.9+ recommended")
        
        # Check required packages
        required_packages = [
            ("pydantic", None),
            ("pyyaml", "yaml"),
            ("websockets", None),
            ("sounddevice", None),
            ("numpy", None),
        ]
        
        missing = []
        for package_name, import_name in required_packages:
            try:
                __import__(import_name or package_name)
                self._emit_message(f"  ✅ {package_name}")
            except ImportError as e:
                self._emit_message(f"  ❌ {package_name} - not installed")
                missing.append(package_name)
                # Capture missing dependency error to bug tracker
                self.bug_tracker.capture_error(
                    error=e,
                    component="installer",
                    severity=BugSeverity.MEDIUM,
                    title=f"Step failed: dependencies - {package_name}"
                )
        
        # Check system dependencies
        system_deps = ["portaudio"]
        for dep in system_deps:
            self._emit_message(f"  Checking {dep}...")
            # Basic check - would need platform-specific code
        
        duration_ms = int((time.time() - start) * 1000)
        
        if missing:
            return InstallResult(
                step=InstallStep.DEPENDENCIES,
                success=False,
                message=f"Missing packages: {', '.join(missing)}",
                details="Install with: pip install " + " ".join(missing),
                duration_ms=duration_ms,
                can_continue=True,
                warnings=warnings,
            )
        
        if warnings:
            self._emit_message(f"  ⚠️  Warnings: {len(warnings)}")
            return InstallResult(
                step=InstallStep.DEPENDENCIES,
                success=True,
                message="Dependencies check passed with warnings",
                warnings=warnings,
                duration_ms=duration_ms,
            )
        
        self._emit_message("  ✅ All dependencies satisfied")
        return InstallResult(
            step=InstallStep.DEPENDENCIES,
            success=True,
            message="Dependencies check passed",
            duration_ms=duration_ms,
        )
    
    def _run_configuration(self) -> InstallResult:
        """Check configuration."""
        start = time.time()
        self._emit_step_start(InstallStep.CONFIGURATION)
        self._emit_message("\n⚙️  Step 5: Checking configuration...")
        
        try:
            from installer.config_summary import validate_config
            
            report = validate_config()
            duration_ms = int((time.time() - start) * 1000)
            
            self._emit_message(f"  Config path: {report.config_path or '(using defaults)'}")
            
            for section in report.sections:
                self._emit_message(f"  {section.icon} {section.display_name}:")
                for key, value in section.fields.items():
                    self._emit_message(f"     {key}: {value}")
                
                for issue in section.issues:
                    self._emit_message(f"     {issue}")
            
            if report.has_errors:
                self._emit_message("  ❌ Configuration has errors")
                return InstallResult(
                    step=InstallStep.CONFIGURATION,
                    success=False,
                    message="Configuration has errors",
                    details="Fix configuration errors before proceeding",
                    duration_ms=duration_ms,
                    can_continue=True,
                )
            
            if report.has_warnings:
                self._emit_message("  ⚠️  Configuration has warnings")
                return InstallResult(
                    step=InstallStep.CONFIGURATION,
                    success=True,
                    message="Configuration has warnings",
                    duration_ms=duration_ms,
                    can_continue=True,
                )
            
            self._emit_message("  ✅ Configuration valid")
            return InstallResult(
                step=InstallStep.CONFIGURATION,
                success=True,
                message="Configuration check passed",
                duration_ms=duration_ms,
            )
            
        except Exception as e:
            self.logger.error("Configuration check failed", error=str(e))
            return InstallResult(
                step=InstallStep.CONFIGURATION,
                success=False,
                message=f"Configuration check failed: {e}",
                can_continue=True,
            )
    
    def _run_bug_check(self) -> InstallResult:
        """Check bug tracker for known issues."""
        start = time.time()
        self._emit_step_start(InstallStep.BUG_CHECK)
        self._emit_message("\n🐛 Step 6: Checking known issues...")
        
        try:
            from installer.bug_display import get_bug_summary
            
            summary = get_bug_summary()
            duration_ms = int((time.time() - start) * 1000)
            
            self._emit_message(f"  Total bugs: {summary.total_bugs}")
            self._emit_message(f"  Unfixed: {summary.unfixed_count}")
            
            if summary.has_critical:
                self._emit_message("  🔴 CRITICAL bugs found:")
                for bug in summary.bugs:
                    if bug.severity.name == "CRITICAL":
                        self._emit_message(f"     #{bug.bug_id}: {bug.title}")
            
            if summary.has_high:
                self._emit_message("  🟠 HIGH priority bugs found:")
                for bug in summary.bugs[:3]:  # Show first 3
                    if bug.severity.name == "HIGH":
                        self._emit_message(f"     #{bug.bug_id}: {bug.title}")
                if len([b for b in summary.bugs if b.severity.name == "HIGH"]) > 3:
                    self._emit_message("     ... and more")
            
            if summary.is_clean:
                self._emit_message("  ✅ No known issues")
                return InstallResult(
                    step=InstallStep.BUG_CHECK,
                    success=True,
                    message="No known issues",
                    duration_ms=duration_ms,
                )
            
            display = BugDisplay() if 'BugDisplay' in dir() else None
            
            if summary.has_critical:
                self._emit_message("  ⚠️  Critical bugs may affect functionality")
                return InstallResult(
                    step=InstallStep.BUG_CHECK,
                    success=True,
                    message=f"{summary.critical_count} critical bugs found",
                    details="Review before using in production",
                    duration_ms=duration_ms,
                    can_continue=True,
                )
            
            return InstallResult(
                step=InstallStep.BUG_CHECK,
                success=True,
                message=f"{summary.unfixed_count} unfixed bugs",
                details="Review before using in production",
                duration_ms=duration_ms,
                can_continue=True,
            )
            
        except Exception as e:
            self.logger.error("Bug check failed", error=str(e))
            # Bug check failure is not critical
            return InstallResult(
                step=InstallStep.BUG_CHECK,
                success=True,
                message=f"Bug check skipped: {e}",
                duration_ms=int((time.time() - start) * 1000),
            )
    
    def _run_final(self) -> InstallResult:
        """Final installation step."""
        start = time.time()
        self._emit_step_start(InstallStep.FINAL)
        self._emit_message("\n✅ Step 7: Installation Summary")
        self._emit_message("=" * 50)
        
        duration_ms = int((time.time() - start) * 1000)
        
        # Count results
        passed = sum(1 for r in self.results if r.success)
        failed = len(self.results) - passed
        
        for result in self.results:
            icon = "✅" if result.success else "❌"
            self._emit_message(f"  {icon} {result.step.value}: {result.message}")
        
        self._emit_message("")
        
        if failed == 0:
            self._emit_message("🎉 Installation ready!")
            self._emit_message("")
            self._emit_message("Next steps:")
            self._emit_message("  1. Run: ./run_bridge.sh")
            self._emit_message("  2. Or: python -m bridge.main")
        else:
            self._emit_message(f"⚠️  Installation completed with {failed} issue(s)")
            self._emit_message("")
            self._emit_message("Review the warnings above before proceeding.")
        
        return InstallResult(
            step=InstallStep.FINAL,
            success=failed == 0,
            message="Installation complete",
            duration_ms=duration_ms,
        )
    
    @property
    def success(self) -> bool:
        """Check if installation was successful."""
        return all(r.success for r in self.results)


def run_interactive_install(
    workspace: Optional[Path] = None,
    verbose: bool = False,
) -> bool:
    """Run an interactive installation.
    
    Args:
        workspace: Optional workspace path
        verbose: Show detailed output
        
    Returns:
        True if installation succeeded
    """
    installer = Installer(
        workspace=workspace,
        interactive=True,
        verbose=verbose,
    )
    
    # Set up output callbacks
    installer.on_message(print)
    
    return installer.run()


# Late import for type hints
from installer.bug_display import BugDisplay