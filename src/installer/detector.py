"""Previous Installation Detection and Cleanup.

Detects and cleans up traces of previous Voice Bridge installations:
- Running processes
- Configuration files
- Virtual environments
- Database files
- Cache directories
"""

from __future__ import annotations

import os
import signal
import subprocess
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import List, Optional

import structlog

logger = structlog.get_logger()


class InstallationState(Enum):
    """State of previous installation."""
    NONE = "none"
    CLEAN = "clean"
    PARTIAL = "partial"
    ACTIVE = "active"
    UNKNOWN = "unknown"


@dataclass
class InstallationTrace:
    """A trace of a previous installation."""
    path: Path
    description: str
    size_bytes: int = 0
    is_directory: bool = False
    is_running: bool = False
    
    def __str__(self) -> str:
        size_str = f" ({self._format_size(self.size_bytes)})" if self.size_bytes else ""
        status = " [RUNNING]" if self.is_running else ""
        return f"{self.path}{size_str}{status} - {self.description}"
    
    @staticmethod
    def _format_size(size: int) -> str:
        """Format size in human-readable format."""
        for unit in ["B", "KB", "MB", "GB"]:
            if size < 1024:
                return f"{size:.1f} {unit}"
            size /= 1024
        return f"{size:.1f} TB"


@dataclass
class InstallationReport:
    """Full report of installation state."""
    state: InstallationState
    traces: List[InstallationTrace] = field(default_factory=list)
    running_processes: List[str] = field(default_factory=list)
    total_size_bytes: int = 0
    can_install: bool = True
    warnings: List[str] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)
    
    @property
    def has_traces(self) -> bool:
        return len(self.traces) > 0
    
    @property
    def has_running_processes(self) -> bool:
        return len(self.running_processes) > 0
    
    def summary(self) -> str:
        """Generate a summary string."""
        lines = [
            f"Installation State: {self.state.value.upper()}",
            f"Traces Found: {len(self.traces)}",
            f"Running Processes: {len(self.running_processes)}",
            f"Total Size: {self._format_size(self.total_size_bytes)}",
        ]
        if self.warnings:
            lines.append(f"Warnings: {len(self.warnings)}")
        if self.errors:
            lines.append(f"Errors: {len(self.errors)}")
        return "\n".join(lines)
    
    @staticmethod
    def _format_size(size: int) -> str:
        """Format size in human-readable format."""
        for unit in ["B", "KB", "MB", "GB"]:
            if size < 1024:
                return f"{size:.1f} {unit}"
            size /= 1024
        return f"{size:.1f} TB"


class InstallationDetector:
    """Detects previous Voice Bridge installations."""
    
    # Known installation paths
    CONFIG_PATHS = [
        Path.home() / ".voice-bridge",
        Path.home() / ".config" / "voice-bridge-v2",
        Path.home() / ".config" / "voice-bridge",
        Path.home() / ".voice-bridge-v2",
        Path.home() / ".voice-bridge-v3",
        Path.home() / ".voice-bridge-v4",
    ]
    
    DATA_PATHS = [
        Path.home() / ".local" / "share" / "voice-bridge",
        Path.home() / ".local" / "share" / "voice-bridge-v2",
        Path.home() / ".local" / "share" / "voice-bridge-v3",
        Path.home() / ".local" / "share" / "voice-bridge-v4",
    ]
    
    CACHE_PATHS = [
        Path.home() / ".cache" / "voice-bridge",
        Path.home() / ".cache" / "voice-bridge-v2",
    ]
    
    STATE_PATHS = [
        Path.home() / ".local" / "state" / "voice-bridge",
        Path.home() / ".local" / "state" / "voice-bridge-v2",
    ]
    
    VENV_PATTERNS = [
        "venv",
        ".venv",
        "voice-bridge-venv",
    ]
    
    # Process patterns to check
    PROCESS_PATTERNS = [
        "bridge.main",
        "voice-bridge",
        "python.*bridge",
    ]
    
    def __init__(self, workspace: Optional[Path] = None):
        """Initialize the detector.
        
        Args:
            workspace: Optional workspace path to check for venvs
        """
        self.workspace = workspace
        self.logger = structlog.get_logger()
    
    def detect(self) -> InstallationReport:
        """Run full installation detection.
        
        Returns:
            InstallationReport with all found traces
        """
        self.logger.info("Starting installation detection...")
        
        traces: List[InstallationTrace] = []
        running_processes: List[str] = []
        warnings: List[str] = []
        errors: List[str] = []
        
        # Check for running processes first
        running_processes = self._find_running_processes()
        
        # Check config paths
        for path in self.CONFIG_PATHS:
            if path.exists():
                trace = self._create_trace(path, "Configuration directory")
                traces.append(trace)
        
        # Check data paths (databases, etc.)
        for path in self.DATA_PATHS:
            if path.exists():
                trace = self._create_trace(path, "Data directory")
                traces.append(trace)
        
        # Check cache paths
        for path in self.CACHE_PATHS:
            if path.exists():
                trace = self._create_trace(path, "Cache directory")
                traces.append(trace)
        
        # Check state paths (logs)
        for path in self.STATE_PATHS:
            if path.exists():
                trace = self._create_trace(path, "State directory")
                traces.append(trace)
        
        # Check for virtual environments
        venv_traces = self._find_venvs()
        traces.extend(venv_traces)
        
        # Check for specific files
        file_traces = self._find_config_files()
        traces.extend(file_traces)
        
        # Determine state
        state = self._determine_state(traces, running_processes)
        
        # Calculate total size
        total_size = sum(t.size_bytes for t in traces)
        
        # Determine if can install
        can_install = len(running_processes) == 0
        if running_processes:
            warnings.append("Running processes must be stopped before installation")
        
        # Add warnings for partial installations
        if state == InstallationState.PARTIAL:
            warnings.append("Incomplete installation detected - some files may be missing")
        
        self.logger.info(
            "Detection complete",
            state=state.value,
            traces=len(traces),
            processes=len(running_processes),
        )
        
        return InstallationReport(
            state=state,
            traces=traces,
            running_processes=running_processes,
            total_size_bytes=total_size,
            can_install=can_install,
            warnings=warnings,
            errors=errors,
        )
    
    def _find_running_processes(self) -> List[str]:
        """Find running Voice Bridge processes."""
        running = []
        
        try:
            # Use pgrep to find processes
            for pattern in self.PROCESS_PATTERNS:
                try:
                    result = subprocess.run(
                        ["pgrep", "-f", pattern],
                        capture_output=True,
                        text=True,
                        timeout=5,
                    )
                    if result.returncode == 0:
                        pids = result.stdout.strip().split("\n")
                        for pid in pids:
                            if pid:
                                # Get process name
                                try:
                                    proc_result = subprocess.run(
                                        ["ps", "-p", pid, "-o", "comm="],
                                        capture_output=True,
                                        text=True,
                                        timeout=2,
                                    )
                                    proc_name = proc_result.stdout.strip() if proc_result.returncode == 0 else "unknown"
                                    running.append(f"PID {pid}: {proc_name}")
                                except Exception:
                                    running.append(f"PID {pid}")
                except subprocess.TimeoutExpired:
                    self.logger.warning("Timeout checking processes", pattern=pattern)
                except FileNotFoundError:
                    # pgrep not available, try alternative
                    running.extend(self._find_processes_alternative())
                    break
        except Exception as e:
            self.logger.error("Error finding processes", error=str(e))
        
        return running
    
    def _find_processes_alternative(self) -> List[str]:
        """Alternative method to find running processes when pgrep unavailable."""
        running = []
        
        try:
            # Check /proc for python processes running bridge
            proc_path = Path("/proc")
            if proc_path.exists():
                for pid_dir in proc_path.iterdir():
                    if pid_dir.name.isdigit():
                        try:
                            cmdline_path = pid_dir / "cmdline"
                            if cmdline_path.exists():
                                cmdline = cmdline_path.read_text()
                                if "bridge" in cmdline and "python" in cmdline:
                                    running.append(f"PID {pid_dir.name}: python bridge process")
                        except (PermissionError, FileNotFoundError):
                            pass
        except Exception as e:
            self.logger.warning("Alternative process check failed", error=str(e))
        
        return running
    
    def _find_venvs(self) -> List[InstallationTrace]:
        """Find virtual environments."""
        traces = []
        
        # Check home directory for venvs
        home = Path.home()
        for venv_name in self.VENV_PATTERNS:
            venv_path = home / venv_name
            if venv_path.exists() and (venv_path / "bin" / "activate").exists():
                trace = self._create_trace(venv_path, "Virtual environment")
                traces.append(trace)
        
        # Check .voice-bridge venv
        voice_bridge_venv = home / ".voice-bridge" / "venv"
        if voice_bridge_venv.exists():
            trace = self._create_trace(voice_bridge_venv, "Voice Bridge virtual environment")
            traces.append(trace)
        
        # Check workspace if provided
        if self.workspace:
            for venv_name in self.VENV_PATTERNS + ["venv", ".venv"]:
                venv_path = self.workspace / venv_name
                if venv_path.exists() and (venv_path / "bin").exists():
                    trace = self._create_trace(venv_path, "Workspace virtual environment")
                    traces.append(trace)
        
        return traces
    
    def _find_config_files(self) -> List[InstallationTrace]:
        """Find specific configuration files."""
        traces = []
        
        config_files = [
            (Path.home() / ".voice-bridge" / "config.yaml", "Main configuration"),
            (Path.home() / ".voice-bridge" / ".env", "Environment configuration"),
            (Path.home() / ".voice-bridge" / "data" / "sessions.db", "Session database"),
            (Path.home() / ".voice-bridge" / "data" / "bugs.db", "Bug tracker database"),
        ]
        
        for path, description in config_files:
            if path.exists():
                trace = self._create_trace(path, description, is_directory=False)
                traces.append(trace)
        
        return traces
    
    def _create_trace(
        self, 
        path: Path, 
        description: str, 
        is_directory: Optional[bool] = None
    ) -> InstallationTrace:
        """Create an InstallationTrace for a path."""
        is_dir = is_directory if is_directory is not None else path.is_dir()
        size = self._calculate_size(path) if is_dir else (path.stat().st_size if path.exists() else 0)
        
        return InstallationTrace(
            path=path,
            description=description,
            size_bytes=size,
            is_directory=is_dir,
            is_running=False,
        )
    
    def _calculate_size(self, path: Path) -> int:
        """Calculate total size of a directory."""
        total = 0
        try:
            for item in path.rglob("*"):
                if item.is_file():
                    total += item.stat().st_size
        except (PermissionError, OSError) as e:
            self.logger.warning("Could not calculate size", path=str(path), error=str(e))
        return total
    
    def _determine_state(
        self, 
        traces: List[InstallationTrace], 
        running_processes: List[str]
    ) -> InstallationState:
        """Determine the installation state from traces."""
        if running_processes:
            return InstallationState.ACTIVE
        
        if not traces:
            return InstallationState.NONE
        
        # Check if we have a complete installation
        has_config = any("config" in t.description.lower() for t in traces)
        has_venv = any("venv" in t.description.lower() or "virtual" in t.description.lower() for t in traces)
        has_data = any("data" in t.description.lower() or "database" in t.description.lower() for t in traces)
        
        if has_config and has_venv and has_data:
            return InstallationState.CLEAN
        
        # Partial installation
        return InstallationState.PARTIAL


def detect_previous_installation(workspace: Optional[Path] = None) -> InstallationReport:
    """Convenience function to detect previous installations.
    
    Args:
        workspace: Optional workspace path to check for venvs
        
    Returns:
        InstallationReport with all found traces
    """
    detector = InstallationDetector(workspace=workspace)
    return detector.detect()


def cleanup_installation(
    report: InstallationReport, 
    force: bool = False,
    stop_processes: bool = True,
    keep_config: bool = False,
    keep_data: bool = False,
    keep_voices: bool = True,  # Preserve voice models by default
) -> bool:
    """Clean up a previous installation.
    
    Args:
        report: InstallationReport from detection
        force: Force cleanup even if processes are running
        stop_processes: Stop running processes before cleanup
        keep_config: Keep configuration files
        keep_data: Keep database files
        keep_voices: Keep voice models directory
        
    Returns:
        True if cleanup was successful
    """
    logger = structlog.get_logger()
    logger.info("Starting installation cleanup", force=force, keep_config=keep_config)
    
    # Check for running processes
    if report.has_running_processes and not force:
        if stop_processes:
            for proc in report.running_processes:
                # Extract PID from formats like "PID 69538: python" or "PID 69538"
                try:
                    # Handle both "PID 69538: python" and "PID 69538"
                    pid_str = proc.split()[1].rstrip(':')
                    pid = int(pid_str)
                    logger.info("Stopping process", pid=pid)
                    os.kill(pid, signal.SIGTERM)
                except (ValueError, ProcessLookupError, PermissionError) as e:
                    logger.warning("Could not stop process", process=proc, error=str(e))
                    if not force:
                        return False
        elif not force:
            logger.error("Cannot cleanup: processes running", processes=report.running_processes)
            return False
    
    # Remove traces
    for trace in report.traces:
        # Skip config if requested
        if keep_config and "config" in trace.description.lower():
            logger.info("Keeping config", path=str(trace.path))
            continue
        
        # Skip data if requested
        if keep_data and ("data" in trace.description.lower() or "database" in trace.description.lower()):
            logger.info("Keeping data", path=str(trace.path))
            continue
        
        # Skip voices directory if requested
        if keep_voices and "voice" in str(trace.path).lower() and trace.path.name == "voices":
            logger.info("Keeping voices directory", path=str(trace.path))
            continue
        
        try:
            if trace.is_directory:
                import shutil
                
                # Check for voices subdirectory before removing
                if keep_voices:
                    voices_path = trace.path / "voices"
                    if voices_path.exists():
                        logger.info("Preserving voices directory", path=str(voices_path))
                        # Move voices out temporarily
                        import tempfile
                        temp_voices = Path(tempfile.mkdtemp()) / "voices_backup"
                        shutil.move(str(voices_path), str(temp_voices))
                        
                        # Remove the directory
                        shutil.rmtree(trace.path)
                        logger.info("Removed directory", path=str(trace.path))
                        
                        # Recreate directory and restore voices
                        trace.path.mkdir(parents=True, exist_ok=True)
                        shutil.move(str(temp_voices), str(voices_path))
                        logger.info("Restored voices directory", path=str(voices_path))
                        continue
                
                shutil.rmtree(trace.path)
                logger.info("Removed directory", path=str(trace.path))
            else:
                trace.path.unlink()
                logger.info("Removed file", path=str(trace.path))
        except (PermissionError, FileNotFoundError, OSError) as e:
            logger.warning("Could not remove", path=str(trace.path), error=str(e))
            if not force:
                return False
    
    logger.info("Cleanup complete")
    return True