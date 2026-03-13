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
    OPENCLAW_CONNECTION = "openclaw_connection"
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
    # Populated on failure; fed into the final fix guide
    issues: List["installer.diagnostic.Issue"] = field(default_factory=list)

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
        from installer.diagnostic import DiagnosticReport, collect_system_info
        self._diag = DiagnosticReport()

        self.logger.info("Starting Voice Bridge installation")
        self._emit_message("🎙️  Voice Bridge Installer")
        self._emit_message("=" * 50)

        # System snapshot shown once at the top
        self._emit_message("\nSystem information:")
        for line in collect_system_info():
            self._emit_message(f"  {line}")

        all_success = True

        # Step 1: Detection
        result = self._run_detection()
        self.results.append(result)
        self._record_step_result(result)
        all_success = all_success and result.success
        for issue in result.issues:
            self._diag.add(issue)

        if not result.can_continue and self.stop_on_error:
            return False

        # Step 2: Cleanup (if needed)
        if result.warnings or "running" in result.message.lower():
            result = self._run_cleanup()
            self.results.append(result)
            self._record_step_result(result)
            all_success = all_success and result.success
            for issue in result.issues:
                self._diag.add(issue)

        # Step 3: Hardware Check
        result = self._run_hardware_check()
        self.results.append(result)
        self._record_step_result(result)
        all_success = all_success and result.success
        for issue in result.issues:
            self._diag.add(issue)

        if not result.can_continue and self.stop_on_error:
            return False

        # Step 4: Dependencies
        result = self._run_dependencies()
        self.results.append(result)
        self._record_step_result(result)
        all_success = all_success and result.success
        for issue in result.issues:
            self._diag.add(issue)

        if not result.can_continue and self.stop_on_error:
            return False

        # Step 5: Configuration
        result = self._run_configuration()
        self.results.append(result)
        self._record_step_result(result)
        all_success = all_success and result.success
        for issue in result.issues:
            self._diag.add(issue)

        # Step 6: OpenClaw Connection
        result = self._run_openclaw_check()
        self.results.append(result)
        self._record_step_result(result)
        all_success = all_success and result.success
        for issue in result.issues:
            self._diag.add(issue)

        # Step 7: Bug Check
        result = self._run_bug_check()
        self.results.append(result)
        self._record_step_result(result)
        all_success = all_success and result.success
        for issue in result.issues:
            self._diag.add(issue)

        # Step 8: Final
        result = self._run_final()
        self.results.append(result)
        self._record_step_result(result)

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
            self.bug_tracker.capture_error(
                error=e,
                component="installer",
                severity=BugSeverity.MEDIUM,
                title="Step failed: cleanup",
            )
            return InstallResult(
                step=InstallStep.CLEANUP,
                success=False,
                message=f"Cleanup failed: {e}",
                can_continue=True,
            )
    
    def _run_hardware_check(self) -> InstallResult:
        """Run hardware validation with audio subsystem context and fix hints."""
        from installer.diagnostic import Issue, detect_audio_subsystem, portaudio_install_hint

        start = time.time()
        self._emit_step_start(InstallStep.HARDWARE_CHECK)
        self._emit_message("\n🔧 Step 3: Checking hardware compatibility...")

        issues: List[Issue] = []

        # Always show audio subsystem so user has context
        audio_sys = detect_audio_subsystem()
        self._emit_message(f"  Audio subsystem: {audio_sys}")

        try:
            from installer.hardware_test import HardwareTester

            tester = HardwareTester()

            if not tester.audio_available:
                self._emit_message("  ❌ sounddevice / numpy not importable")
                issues.append(Issue(
                    step="Hardware Check",
                    title="Audio Python libraries not available",
                    context=[
                        "sounddevice or numpy could not be imported.",
                        f"Audio subsystem: {audio_sys}",
                    ],
                    fix_steps=[
                        "pip install sounddevice numpy",
                        f"If sounddevice fails: {portaudio_install_hint()}",
                        "Then: pip install --force-reinstall sounddevice",
                        "Re-run: python -m installer",
                    ],
                    is_blocking=True,
                ))
                return InstallResult(
                    step=InstallStep.HARDWARE_CHECK,
                    success=False,
                    message="Audio libraries not available",
                    duration_ms=int((time.time() - start) * 1000),
                    can_continue=True,
                    issues=issues,
                )

            # Device discovery
            self._emit_message("  Discovering audio devices...")
            discovery_result = tester.test_device_discovery()
            self._emit_message(f"     {discovery_result}")
            if discovery_result.details:
                self._emit_message(f"     {discovery_result.details}")

            # Input devices
            self._emit_message("  Testing microphone (input)...")
            input_result = tester.test_input_devices()
            self._emit_message(f"     {input_result}")
            if input_result.details:
                self._emit_message(f"     {input_result.details}")

            # Output devices
            self._emit_message("  Testing speakers (output)...")
            output_result = tester.test_output_devices()
            self._emit_message(f"     {output_result}")
            if output_result.details:
                self._emit_message(f"     {output_result.details}")

            duration_ms = int((time.time() - start) * 1000)

            warnings = []
            success = True

            if not discovery_result.passed:
                success = False
                issues.append(Issue(
                    step="Hardware Check",
                    title="No audio devices found",
                    context=[
                        f"Audio subsystem: {audio_sys}",
                        "sounddevice.query_devices() returned no devices.",
                    ],
                    fix_steps=[
                        "Connect a microphone and speakers/headphones.",
                        f"If using PulseAudio/PipeWire: ensure the audio server is running.",
                        "Check devices with: python3 -c \"import sounddevice; print(sounddevice.query_devices())\"",
                        "Re-run: python -m installer",
                    ],
                    is_blocking=True,
                ))

            if not input_result.passed:
                success = False
                warnings.append("No microphone found")
                issues.append(Issue(
                    step="Hardware Check",
                    title="No microphone (input device) found",
                    context=[
                        f"Audio subsystem: {audio_sys}",
                        f"Error: {input_result.message}",
                    ],
                    fix_steps=[
                        "Connect a USB microphone or headset.",
                        "Check microphone is not muted in system mixer: alsamixer or pavucontrol",
                        "Verify it appears in: python3 -c \"import sounddevice; print(sounddevice.query_devices())\"",
                        "Re-run: python -m installer",
                    ],
                    is_blocking=True,
                ))

            if not output_result.passed:
                success = False
                warnings.append("No speaker/output device found")
                issues.append(Issue(
                    step="Hardware Check",
                    title="No speaker (output device) found",
                    context=[
                        f"Audio subsystem: {audio_sys}",
                        f"Error: {output_result.message}",
                    ],
                    fix_steps=[
                        "Connect speakers or headphones.",
                        "Check output is not muted: alsamixer or pavucontrol",
                        "Re-run: python -m installer",
                    ],
                    is_blocking=False,
                ))

            if self.interactive and input_result.passed and output_result.passed:
                self._emit_message("  Run with --test-audio to record & play back audio.")

            if success:
                self._emit_message("  ✅ Audio hardware ready")
            else:
                self._emit_message("  ❌ Hardware check failed — see fix guide at end")

            return InstallResult(
                step=InstallStep.HARDWARE_CHECK,
                success=success,
                message="Hardware check " + ("passed" if success else "failed"),
                duration_ms=duration_ms,
                can_continue=True,
                warnings=warnings,
                issues=issues,
            )

        except Exception as e:
            self.logger.error("Hardware check failed", error=str(e))
            self.bug_tracker.capture_error(
                error=e,
                component="installer",
                severity=BugSeverity.MEDIUM,
                title="Step failed: hardware_check",
            )
            issues.append(Issue(
                step="Hardware Check",
                title=f"Hardware check crashed: {type(e).__name__}",
                context=[str(e), f"Audio subsystem: {audio_sys}"],
                fix_steps=[
                    f"Install PortAudio: {portaudio_install_hint()}",
                    "Reinstall sounddevice: pip install --force-reinstall sounddevice",
                    "Re-run: python -m installer",
                ],
                is_blocking=True,
            ))
            return InstallResult(
                step=InstallStep.HARDWARE_CHECK,
                success=False,
                message=f"Hardware check error: {e}",
                can_continue=True,
                issues=issues,
            )
    
    def _run_dependencies(self) -> InstallResult:
        """Check system dependencies with version info and actionable errors."""
        from installer.diagnostic import (
            Issue, get_installed_version, get_install_cmd, portaudio_install_hint,
            check_portaudio_library,
        )

        start = time.time()
        self._emit_step_start(InstallStep.DEPENDENCIES)
        self._emit_message("\n📦 Step 4: Checking dependencies...")

        issues: List[Issue] = []
        warnings = []

        # ── Python version ────────────────────────────────────────────────
        python_version = sys.version_info
        venv = os.environ.get("VIRTUAL_ENV") or os.environ.get("CONDA_DEFAULT_ENV")
        py_str = f"{python_version.major}.{python_version.minor}.{python_version.micro}"
        venv_str = venv or "system Python"
        self._emit_message(f"  Python  : {py_str}  ({venv_str})")

        if python_version < (3, 9):
            warnings.append(f"Python 3.9+ required, found {py_str}")
            issues.append(Issue(
                step="Dependencies",
                title=f"Python version too old ({py_str})",
                context=[f"Found: Python {py_str}", "Required: Python 3.9 or newer"],
                fix_steps=[
                    "Install Python 3.9+: https://www.python.org/downloads/",
                    "Or via pyenv: pyenv install 3.11 && pyenv local 3.11",
                    f"Re-run installer: python3 -m installer",
                ],
                is_blocking=True,
            ))

        # ── Python packages ───────────────────────────────────────────────
        required_packages = [
            ("pydantic",    None,         "2.0"),
            ("pyyaml",      "yaml",       None),
            ("websockets",  None,         None),
            ("sounddevice", None,         None),
            ("numpy",       None,         None),
            ("aiohttp",     None,         None),
            ("structlog",   None,         None),
            ("faster_whisper", "faster_whisper", None),
        ]

        missing = []
        for package_name, import_name, min_version in required_packages:
            version = get_installed_version(package_name)
            try:
                __import__(import_name or package_name)
                ver_str = f"  ({version})" if version else ""
                self._emit_message(f"  ✅ {package_name}{ver_str}")
            except ImportError:
                self._emit_message(f"  ❌ {package_name} — NOT INSTALLED")
                missing.append(package_name)
                self.bug_tracker.capture_error(
                    error=ImportError(package_name),
                    component="installer",
                    severity=BugSeverity.MEDIUM,
                    title=f"Step failed: dependencies - {package_name}",
                )

        if missing:
            cmd = get_install_cmd(missing)
            issues.append(Issue(
                step="Dependencies",
                title=f"Missing Python packages: {', '.join(missing)}",
                context=[f"Not installed: {', '.join(missing)}"],
                fix_steps=[
                    f"Run: {cmd}",
                    "Then re-run: python -m installer",
                ],
                is_blocking=True,
            ))

        # ── PortAudio C library ───────────────────────────────────────────
        self._emit_message("  Checking PortAudio C library...")
        pa_status = check_portaudio_library()
        if pa_status.startswith("OK"):
            self._emit_message(f"  ✅ PortAudio: {pa_status}")
        elif pa_status.startswith("N/A"):
            self._emit_message(f"  ⏭️  PortAudio: {pa_status}")
        else:
            self._emit_message(f"  ❌ PortAudio: {pa_status}")
            issues.append(Issue(
                step="Dependencies",
                title="PortAudio C library missing or broken",
                context=[
                    f"sounddevice initialisation failed: {pa_status}",
                    "PortAudio is the C library sounddevice uses to talk to audio hardware.",
                ],
                fix_steps=[
                    f"Install PortAudio: {portaudio_install_hint()}",
                    "Then reinstall sounddevice: pip install --force-reinstall sounddevice",
                    "Re-run installer to verify: python -m installer",
                ],
                is_blocking=True,
            ))

        duration_ms = int((time.time() - start) * 1000)
        success = not missing and not any(i.is_blocking for i in issues)

        if success and not warnings:
            self._emit_message("  ✅ All dependencies satisfied")
        elif warnings:
            for w in warnings:
                self._emit_message(f"  ⚠️  {w}")

        return InstallResult(
            step=InstallStep.DEPENDENCIES,
            success=success,
            message="Dependencies OK" if success else f"Missing: {', '.join(missing) or 'PortAudio'}",
            duration_ms=duration_ms,
            can_continue=True,
            warnings=warnings,
            issues=issues,
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
                error_ctx = []
                for section in report.sections:
                    for issue_text in section.issues:
                        if "error" in str(issue_text).lower():
                            error_ctx.append(str(issue_text))
                from installer.diagnostic import Issue
                cfg_issue = Issue(
                    step="Configuration",
                    title="Configuration file has errors",
                    context=[f"Config: {report.config_path or '(defaults)'}"] + error_ctx[:6],
                    fix_steps=[
                        f"Edit: {report.config_path or '~/.voice-bridge/config.yaml'}",
                        "Refer to the project config.yaml for valid values.",
                        "Re-run: python -m installer",
                    ],
                    is_blocking=False,
                )
                return InstallResult(
                    step=InstallStep.CONFIGURATION,
                    success=False,
                    message="Configuration has errors",
                    duration_ms=duration_ms,
                    can_continue=True,
                    issues=[cfg_issue],
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
            self.bug_tracker.capture_error(
                error=e,
                component="installer",
                severity=BugSeverity.MEDIUM,
                title="Step failed: configuration",
            )
            from installer.diagnostic import Issue
            return InstallResult(
                step=InstallStep.CONFIGURATION,
                success=False,
                message=f"Configuration check failed: {e}",
                can_continue=True,
                issues=[Issue(
                    step="Configuration",
                    title=f"Could not load config: {type(e).__name__}",
                    context=[str(e)],
                    fix_steps=[
                        "Check ~/.voice-bridge/config.yaml for YAML syntax errors.",
                        "Delete it to fall back to defaults: rm ~/.voice-bridge/config.yaml",
                        "Re-run: python -m installer",
                    ],
                    is_blocking=False,
                )],
            )
    
    def _run_openclaw_check(self) -> InstallResult:
        """Test the connection to OpenClaw — emit a structured Issue on failure."""
        from installer.diagnostic import Issue
        from installer.openclaw_test import run_openclaw_test, test_openclaw_connection

        start = time.time()
        self._emit_step_start(InstallStep.OPENCLAW_CONNECTION)
        self._emit_message("\n🔌 Step 6: Testing OpenClaw connection...")

        issues: List[Issue] = []

        try:
            hw = run_openclaw_test()
            duration_ms = int((time.time() - start) * 1000)

            self._emit_message(f"  {hw}")
            if hw.details:
                for line in hw.details.splitlines():
                    self._emit_message(f"     {line}")

            if hw.passed:
                return InstallResult(
                    step=InstallStep.OPENCLAW_CONNECTION,
                    success=True,
                    message=hw.message,
                    duration_ms=duration_ms,
                )

            # Build a structured Issue from the hw.details text so it
            # appears clearly in the final fix guide.
            context_lines = [l.strip() for l in (hw.details or "").splitlines() if l.strip()]
            issues.append(Issue(
                step="OpenClaw Connection",
                title=hw.message,
                context=context_lines,
                fix_steps=[
                    "Start OpenClaw and make sure it is listening on the configured host:port.",
                    "Verify with: curl http://<host>:<port>/v1/chat/completions",
                    "Check your config: ~/.voice-bridge/config.yaml  (openclaw.host / openclaw.port)",
                    "If auth_token is required, set it: export OPENCLAW_GATEWAY_TOKEN=your_token",
                    "Re-run installer to confirm: python -m installer",
                ],
                is_blocking=True,
            ))

            self.bug_tracker.capture_error(
                error=Exception(hw.message),
                component="installer",
                severity=BugSeverity.HIGH,
                title=f"Step failed: openclaw_connection - {hw.message}",
            )
            return InstallResult(
                step=InstallStep.OPENCLAW_CONNECTION,
                success=False,
                message=hw.message,
                duration_ms=duration_ms,
                can_continue=True,
                issues=issues,
            )

        except Exception as e:
            self.logger.error("OpenClaw connection check failed", error=str(e))
            self.bug_tracker.capture_error(
                error=e,
                component="installer",
                severity=BugSeverity.HIGH,
                title="Step failed: openclaw_connection crashed",
            )
            issues.append(Issue(
                step="OpenClaw Connection",
                title=f"OpenClaw check crashed: {type(e).__name__}",
                context=[str(e)],
                fix_steps=[
                    "Check that bridge.config can be imported (dependencies OK?).",
                    "Re-run: python -m installer",
                ],
                is_blocking=True,
            ))
            return InstallResult(
                step=InstallStep.OPENCLAW_CONNECTION,
                success=False,
                message=f"OpenClaw check error: {e}",
                duration_ms=int((time.time() - start) * 1000),
                can_continue=True,
                issues=issues,
            )

    def _run_bug_check(self) -> InstallResult:
        """Check bug tracker for known issues."""
        start = time.time()
        self._emit_step_start(InstallStep.BUG_CHECK)
        self._emit_message("\n🐛 Step 7: Checking known issues...")
        
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
        """Final installation step — summary + fix guide."""
        start = time.time()
        self._emit_step_start(InstallStep.FINAL)
        self._emit_message("\nStep 8: Installation Summary")
        self._emit_message("=" * 50)

        duration_ms = int((time.time() - start) * 1000)

        # Per-step status table
        for result in self.results:
            icon = "✅" if result.success else "❌"
            self._emit_message(f"  {icon} {result.step.value}: {result.message}")

        self._emit_message("")

        diag = getattr(self, "_diag", None)
        failed = sum(1 for r in self.results if not r.success)

        if failed == 0:
            self._emit_message("🎉 All checks passed — Voice Bridge is ready!")
            self._emit_message("")
            self._emit_message("Next steps:")
            self._emit_message("  1. Start the bridge  : python -m bridge.main")
            self._emit_message("  2. Or use the script : ./run_bridge.sh")
            self._emit_message("  3. Say 'computer' to activate after it starts")
        else:
            # Print the fix guide
            if diag and diag.has_issues:
                for line in diag.render().splitlines():
                    self._emit_message(line)
            else:
                self._emit_message(f"⚠️  {failed} step(s) failed — review the output above.")

            if diag and diag.has_blocking:
                self._emit_message(
                    "  The bridge will not work until the BLOCKING issues above are resolved."
                )
            else:
                self._emit_message(
                    "  You may start the bridge, but some features may not work correctly."
                )
            self._emit_message("")
            self._emit_message(
                "  Re-run this installer after making fixes:  python -m installer"
            )

        return InstallResult(
            step=InstallStep.FINAL,
            success=failed == 0,
            message="Installation complete" if failed == 0 else f"{failed} issue(s) found",
            duration_ms=duration_ms,
        )
    
    def _record_step_result(self, result: InstallResult) -> None:
        """Record every install step outcome to the diagnostic events and bugs tables.

        Called after every step so that a complete audit trail is always
        available in the database — regardless of which step failed or why.
        """
        metadata: dict = {
            "success": result.success,
            "message": result.message,
            "can_continue": result.can_continue,
        }
        if result.warnings:
            metadata["warnings"] = result.warnings
        if result.issues:
            metadata["issues"] = [
                {"title": i.title, "blocking": i.is_blocking}
                for i in result.issues
            ]

        self.bug_tracker.record_event(
            component="installer",
            event_type="install_step",
            trigger=result.step.value,
            duration_ms=float(result.duration_ms) if result.duration_ms else None,
            metadata=metadata,
        )

        # For blocking failures, also write to the bugs table so they
        # appear in `python -m installer --show-bugs` on the next run.
        if not result.success:
            for issue in (result.issues or []):
                if issue.is_blocking:
                    self.bug_tracker.capture_error(
                        error=Exception(issue.title),
                        component="installer",
                        severity=BugSeverity.HIGH,
                        title=f"[{result.step.value}] {issue.title}",
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