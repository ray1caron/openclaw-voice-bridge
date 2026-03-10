"""Diagnostic collection and fix-guide reporting for the Voice Bridge installer.

Every failure in the installer produces an Issue with:
  - A plain-English title
  - Context lines (what we observed)
  - Ordered fix_steps (what to do about it)

At the end of installation, DiagnosticReport renders a "Problems Found /
How to Fix" section so the user leaves with a concrete action list.
"""

from __future__ import annotations

import os
import platform
import subprocess
import sys
from dataclasses import dataclass, field
from typing import List, Optional


# ──────────────────────────────────────────────────────────────────────────────
# Issue: one diagnosable problem + its fix steps
# ──────────────────────────────────────────────────────────────────────────────

@dataclass
class Issue:
    """A single diagnosable problem found during installation."""
    step: str                         # e.g. "Dependencies", "OpenClaw Connection"
    title: str                        # short headline
    context: List[str] = field(default_factory=list)   # "what we saw"
    fix_steps: List[str] = field(default_factory=list) # ordered "what to do"
    is_blocking: bool = False         # bridge will definitely not work without this fix


# ──────────────────────────────────────────────────────────────────────────────
# DiagnosticReport: accumulates issues, renders the fix guide
# ──────────────────────────────────────────────────────────────────────────────

class DiagnosticReport:
    """Accumulates Issues during installation and renders the fix guide."""

    def __init__(self):
        self._issues: List[Issue] = []

    def add(self, issue: Issue) -> None:
        self._issues.append(issue)

    @property
    def issues(self) -> List[Issue]:
        return list(self._issues)

    @property
    def has_issues(self) -> bool:
        return bool(self._issues)

    @property
    def has_blocking(self) -> bool:
        return any(i.is_blocking for i in self._issues)

    def render(self) -> str:
        """Return the full fix-guide as a printable string."""
        if not self._issues:
            return ""

        W = 62
        lines: List[str] = []

        lines.append("")
        lines.append("=" * W)
        blocking = [i for i in self._issues if i.is_blocking]
        non_blocking = [i for i in self._issues if not i.is_blocking]
        if blocking:
            lines.append("  PROBLEMS FOUND — Voice Bridge will NOT work until fixed")
        else:
            lines.append("  PROBLEMS FOUND — Action recommended before starting")
        lines.append("=" * W)

        all_issues = blocking + non_blocking   # blocking first
        for idx, issue in enumerate(all_issues, start=1):
            tag = "[BLOCKING] " if issue.is_blocking else ""
            lines.append("")
            lines.append(f"  Problem {idx}: {tag}{issue.title}")
            lines.append(f"  Step: {issue.step}")
            lines.append("  " + "-" * (W - 2))

            if issue.context:
                lines.append("  What we found:")
                for ctx in issue.context:
                    lines.append(f"    {ctx}")

            if issue.fix_steps:
                lines.append("  How to fix:")
                for i, step in enumerate(issue.fix_steps, start=1):
                    lines.append(f"    {i}. {step}")

        lines.append("")
        lines.append("=" * W)
        if blocking:
            lines.append(f"  {len(blocking)} blocking / {len(non_blocking)} advisory issue(s) found")
        else:
            lines.append(f"  {len(non_blocking)} advisory issue(s) found")
        lines.append("=" * W)
        lines.append("")

        return "\n".join(lines)


# ──────────────────────────────────────────────────────────────────────────────
# System snapshot (captured once, shown in the report header)
# ──────────────────────────────────────────────────────────────────────────────

def collect_system_info() -> List[str]:
    """Return a list of key/value strings describing the current environment."""
    info = []

    # Python
    info.append(f"Python        : {sys.version.split()[0]}  ({sys.executable})")

    # Virtual environment
    venv = os.environ.get("VIRTUAL_ENV") or os.environ.get("CONDA_DEFAULT_ENV")
    if venv:
        info.append(f"Environment   : {venv}")
    else:
        info.append("Environment   : system Python (no venv detected)")

    # Platform
    info.append(f"OS            : {platform.platform()}")

    # Audio subsystem
    info.append(f"Audio system  : {detect_audio_subsystem()}")

    # PortAudio library
    pa = check_portaudio_library()
    info.append(f"PortAudio lib : {pa}")

    return info


# ──────────────────────────────────────────────────────────────────────────────
# Audio subsystem detection
# ──────────────────────────────────────────────────────────────────────────────

def detect_audio_subsystem() -> str:
    """Identify the running audio subsystem (PipeWire, PulseAudio, JACK, ALSA)."""
    checks = [
        ("pw-cli",       ["pw-cli", "info", "0"],       "PipeWire"),
        ("pactl",        ["pactl", "info"],               "PulseAudio"),
        ("jack_control", ["jack_control", "status"],      "JACK"),
    ]
    for _, cmd, name in checks:
        try:
            result = subprocess.run(cmd, capture_output=True, timeout=3)
            if result.returncode == 0:
                return name
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
            continue

    # ALSA is always "there" on Linux if /proc/asound exists
    if os.path.exists("/proc/asound"):
        return "ALSA (no PulseAudio/PipeWire detected)"

    return "unknown"


def check_portaudio_library() -> str:
    """
    Verify the PortAudio C library is present and usable by sounddevice.
    Returns a short status string.
    """
    try:
        import sounddevice as sd
        # Calling query_devices() actually initialises PortAudio
        sd.query_devices()
        return "OK (sounddevice initialised successfully)"
    except OSError as exc:
        return f"MISSING — {exc}"
    except ImportError:
        return "N/A (sounddevice not installed)"
    except Exception as exc:
        return f"ERROR — {exc}"


def get_installed_version(package: str) -> Optional[str]:
    """Return the installed version string for a package, or None."""
    try:
        import importlib.metadata
        return importlib.metadata.version(package)
    except Exception:
        return None


def get_install_cmd(packages: List[str]) -> str:
    """Return the pip install command appropriate for this environment."""
    in_venv = bool(os.environ.get("VIRTUAL_ENV") or os.environ.get("CONDA_DEFAULT_ENV"))
    flag = "" if in_venv else " --break-system-packages"
    return f"pip install{flag} {' '.join(packages)}"


def portaudio_install_hint() -> str:
    """Return the OS-appropriate command to install PortAudio."""
    system = platform.system().lower()
    if system == "linux":
        # Try to detect distro
        try:
            with open("/etc/os-release") as f:
                release = f.read().lower()
        except OSError:
            release = ""
        if "ubuntu" in release or "debian" in release or "mint" in release:
            return "sudo apt install portaudio19-dev python3-dev"
        if "fedora" in release or "rhel" in release or "centos" in release:
            return "sudo dnf install portaudio-devel"
        if "arch" in release or "manjaro" in release:
            return "sudo pacman -S portaudio"
        return "sudo apt install portaudio19-dev   # (adjust for your distro)"
    if system == "darwin":
        return "brew install portaudio"
    if system == "windows":
        return "pip install pipwin && pipwin install pyaudio"
    return "Install PortAudio development libraries for your OS"
