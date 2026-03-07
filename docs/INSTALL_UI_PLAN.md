# Installation UI Project Plan

## Overview

A CLI/TUI installer for the Voice-OpenClaw Bridge that provides an interactive installation experience with hardware testing, previous installation detection, and bug tracker integration.

---

## 1. Architecture Overview

```
┌─────────────────────────────────────────────────────────────────┐
│                     Installation UI                              │
│                    (src/installer/)                              │
├─────────────────────────────────────────────────────────────────┤
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────────────┐   │
│  │  Installer   │  │   Hardware   │  │    Bug Display       │   │
│  │    Core      │  │    Tester    │  │    Component         │   │
│  └──────┬───────┘  └──────┬───────┘  └───────────┬──────────┘   │
│         │                 │                       │              │
│         ▼                 ▼                       ▼              │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │              Existing Bridge Components                    │   │
│  │  ┌────────────┐ ┌──────────────┐ ┌─────────────────────┐  │   │
│  │  │ Audio      │ │ Audio        │ │ BugTracker          │  │   │
│  │  │ Discovery  │ │ Pipeline     │ │ (bug_tracker.py)    │  │   │
│  │  └────────────┘ └──────────────┘ └─────────────────────┘  │   │
│  │  ┌────────────┐ ┌──────────────────────────────────────┐  │   │
│  │  │ Config     │ │ Main Entry Point                      │  │   │
│  │  │ (config.py)│ │ (main.py - first-run integration)     │  │   │
│  │  └────────────┘ └──────────────────────────────────────┘  │   │
│  └──────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────┘
```

### Design Principles

1. **Simple CLI-first** - Use plain text with optional colors, no heavy TUI frameworks
2. **Non-destructive** - Always ask before removing previous installations
3. **Progressive disclosure** - Show summary first, details on request
4. **Graceful degradation** - Continue if non-critical steps fail

---

## 2. Component Breakdown

### 2.1 Installer Core (`src/installer/core.py`)

**Purpose:** Orchestrates the installation flow, manages state.

**Responsibilities:**
- Detect previous installations (processes, files, configs)
- Prompt for removal confirmation
- Step through installation phases
- Track installation progress
- Generate final summary report

**Key Classes:**
```python
class InstallerState(Enum):
    NOT_STARTED = "not_started"
    CHECKING_PREVIOUS = "checking_previous"
    PROMPTING_REMOVAL = "prompting_removal"
    CONFIGURING = "configuring"
    TESTING_HARDWARE = "testing_hardware"
    SHOWING_BUGS = "showing_bugs"
    COMPLETE = "complete"
    FAILED = "failed"

class InstallProgress:
    current_step: str
    total_steps: int
    completed_steps: int
    messages: List[str]
    errors: List[str]
    warnings: List[str]

class InstallerCore:
    def detect_previous_installations() -> List[Installation]
    def prompt_removal(installations: List[Installation]) -> bool
    def run_installation(progress_callback: Callable) -> bool
    def get_progress() -> InstallProgress
```

### 2.2 Previous Installation Detector (`src/installer/detector.py`)

**Purpose:** Find and catalog existing installations.

**What it checks:**
```python
class InstallationDetector:
    def find_running_processes() -> List[ProcessInfo]
        # Check for running voice-bridge processes
        # Uses: psutil.process_iter() with name filter
    
    def find_config_files() -> List[ConfigFile]
        # ~/.voice-bridge/config.yaml
        # ~/.voice-bridge/.env
        # ~/.voice-bridge/bugs.db
    
    def find_data_directories() -> List[DataDir]
        # ~/.voice-bridge/
        # ~/.local/share/voice-bridge/ (if used)
    
    def find_cron_jobs() -> List[CronJob]
        # Autostart entries if configured
    
    def find_service_files() -> List[ServiceFile]
        # systemd user services
    
    def scan() -> InstallationReport
        # Returns summary of all found items
```

**Installation Report Structure:**
```python
@dataclass
class InstallationReport:
    processes: List[ProcessInfo]
    config_files: List[ConfigFile]
    data_dirs: List[DataDir]
    cron_jobs: List[CronJob]
    services: List[ServiceFile]
    
    @property
    def has_previous(self) -> bool
    
    @property
    def total_items(self) -> int
    
    def summarize(self) -> str  # Human-readable summary
```

### 2.3 Hardware Tester (`src/installer/hardware_test.py`)

**Purpose:** Interactive hardware testing during installation.

**Flow:**
```
1. List available audio devices (using AudioDiscovery)
2. Let user select input device
3. Record 3-second sample
4. Play back sample for verification
5. Let user confirm or retry
6. Repeat for output device
```

**Key Functions:**
```python
class HardwareTester:
    def __init__(self, config: AppConfig):
        self.discovery = AudioDiscovery()
        self.pipeline = AudioPipeline(config.audio)
    
    def list_devices() -> DeviceList
        # Use AudioDiscovery.discover() and format for display
    
    def test_microphone(device_index: int) -> MicTestResult
        # Record 3 seconds from selected device
        # Return audio quality metrics
    
    def test_speaker(device_index: int, test_audio: bytes) -> SpeakerTestResult
        # Play test tone or recorded audio
        # Return playback status
    
    def run_interactive_test() -> HardwareTestReport
        # Full interactive flow with user prompts
    
    def generate_report() -> str
        # Format results for display
```

**Test Result Structure:**
```python
@dataclass
class MicTestResult:
    success: bool
    device_name: str
    duration_ms: int
    peak_level: float
    noise_floor: float
    snr_db: float
    error: Optional[str]

@dataclass
class SpeakerTestResult:
    success: bool
    device_name: str
    playback_completed: bool
    error: Optional[str]

@dataclass  
class HardwareTestReport:
    input_device: Optional[MicTestResult]
    output_device: Optional[SpeakerTestResult]
    recommendations: List[str]
    warnings: List[str]
    
    @property
    def all_passed(self) -> bool
```

### 2.4 Bug Display Component (`src/installer/bug_display.py`)

**Purpose:** Show unfixed bugs from the integrated bug tracker.

**Integration with BugTracker:**
```python
class BugDisplay:
    def __init__(self, bug_tracker: BugTracker):
        self.tracker = bug_tracker
    
    def get_unfixed_bugs() -> List[BugReport]
        # BugTracker.list_bugs(status=BugStatus.NEW)
        # BugTracker.list_bugs(status=BugStatus.TRIAGED)
        # BugTracker.list_bugs(status=BugStatus.IN_PROGRESS)
    
    def format_bug_summary(bug: BugReport) -> str
        # Compact one-line format
    
    def format_bug_detail(bug: BugReport) -> str
        # Full multi-line format
    
    def display_bugs(verbosity: str = "summary") -> str
        # Format all unfixed bugs for display
    
    def get_bug_stats() -> Dict[str, int]
        # Total, new, in_progress, critical
```

**Display Format:**
```
📋 Known Issues (from bug tracker)
───────────────────────────────────
! [HIGH] audio: Microphone not detected on USB disconnect
  Status: new  ID: 42
  
! [MEDIUM] config: Hot-reload fails with symlinks  
  Status: triaged  ID: 38
  
ℹ️  [LOW] UI: Missing emoji on older terminals
  Status: in_progress  ID: 35

3 known issues found. Run 'bridge bugs show <id>' for details.
```

### 2.5 Configuration Summary (`src/installer/config_summary.py`)

**Purpose:** Show user what's being configured.

**What it displays:**
```python
class ConfigSummary:
    def generate(config: AppConfig, discovery: AudioDiscovery) -> str
```

**Output Format:**
```
⚙️  Configuration Summary
─────────────────────────

Audio:
  ┌────────────────────────────────────────────┐
  │ Input Device:  Blue Yeti [index: 2]       │
  │ Output Device: Built-in Speakers [index: 5]│
  │ Sample Rate:   16000 Hz                    │
  │ Channels:      1 (mono)                     │
  └────────────────────────────────────────────┘
  
OpenClaw Connection:
  ┌────────────────────────────────────────────┐
  │ Host:     localhost                        │
  │ Port:     8080                              │
  │ Secure:   No (HTTP)                        │
  │ Timeout:  30.0 seconds                      │
  └────────────────────────────────────────────┘
  
STT/TTS:
  ┌────────────────────────────────────────────┐
  │ STT Model:   base (Whisper)                │
  │ TTS Voice:   en_US-lessac-medium           │
  └────────────────────────────────────────────┘

Files:
  Config:  ~/.voice-bridge/config.yaml
  Bugs DB: ~/.voice-bridge/bugs.db
```

---

## 3. File Structure

```
voice-bridge-v4/
├── src/
│   ├── bridge/              # Existing bridge code (unchanged)
│   │   ├── audio_discovery.py
│   │   ├── audio_pipeline.py
│   │   ├── bug_tracker.py
│   │   ├── config.py
│   │   └── main.py
│   │
│   └── installer/           # NEW: Installation UI module
│       ├── __init__.py
│       ├── core.py          # Main installer orchestration
│       ├── detector.py     # Previous installation detection
│       ├── hardware_test.py # Interactive hardware testing
│       ├── bug_display.py   # Bug tracker integration
│       ├── config_summary.py# Configuration display
│       └── utils.py         # Shared utilities (formatting, etc.)
│
├── tests/
│   └── installer/           # NEW: Installer tests
│       ├── __init__.py
│       ├── test_detector.py
│       ├── test_hardware_test.py
│       ├── test_core.py
│       └── test_integration.py
│
└── docs/
    └── INSTALL_UI_PLAN.md  # This document
```

---

## 4. Implementation Phases

### Phase 1: Previous Installation Detection (P1)
**Estimated: 2-3 hours**

1. Create `src/installer/` module structure
2. Implement `InstallationDetector` in `detector.py`
3. Add process detection (running voice-bridge instances)
4. Add config file detection
5. Add data directory detection
6. Write unit tests for detector

**Acceptance Criteria:**
- [ ] Detects running `python` processes with "voice-bridge" or "bridge" in command line
- [ ] Finds `~/.voice-bridge/` directory if exists
- [ ] Lists all found config files with sizes
- [ ] Returns structured `InstallationReport`
- [ ] Works without external dependencies beyond stdlib + psutil

### Phase 2: Hardware Testing (P1)
**Estimated: 3-4 hours**

1. Create `HardwareTester` class in `hardware_test.py`
2. Integrate with existing `AudioDiscovery` for device listing
3. Integrate with existing `AudioPipeline` for audio capture/playback
4. Implement 3-second microphone recording test
5. Implement speaker playback test with recorded audio
6. Add quality metrics (SNR, peak level)
7. Write unit tests

**Acceptance Criteria:**
- [ ] Lists all available input/output devices
- [ ] Records 3-second sample from selected mic
- [ ] Plays back recording on selected speakers
- [ ] User can retry test with different device
- [ ] Returns structured test report with pass/fail
- [ ] Handles device disconnection gracefully

### Phase 3: Bug Tracker Integration (P2)
**Estimated: 1-2 hours**

1. Create `BugDisplay` class in `bug_display.py`
2. Query `BugTracker.list_bugs()` for unfixed bugs
3. Format bugs for terminal display
4. Add summary statistics
5. Write unit tests

**Acceptance Criteria:**
- [ ] Displays all bugs with status != 'fixed' and != 'closed'
- [ ] Shows severity, component, and title for each bug
- [ ] Displays total count and breakdown by status
- [ ] Provides hint for viewing bug details
- [ ] Handles empty bug database gracefully

### Phase 4: Installation Flow Integration (P1)
**Estimated: 2-3 hours**

1. Integrate components in `core.py`
2. Add progress reporting with callbacks
3. Integrate into `main.py` entry point
4. Add `--interactive` flag for explicit installer run
5. Add `--skip-hardware-test` flag
6. Add `--show-bugs` flag

**Acceptance Criteria:**
- [ ] Installer runs on first-run or `--interactive` flag
- [ ] Checks for previous installations before proceeding
- [ ] Shows configuration summary
- [ ] Runs hardware tests (skippable)
- [ ] Shows known bugs from tracker
- [ ] Creates/updates config file

### Phase 5: Polish & Edge Cases (P2)
**Estimated: 1-2 hours**

1. Add color support (optional, detected)
2. Add terminal width detection
3. Handle non-interactive environments
4. Add timeout for hardware tests
5. Add cleanup on interruption (Ctrl+C)

**Acceptance Criteria:**
- [ ] Colors work on modern terminals, degrade gracefully
- [ ] Output wraps properly on narrow terminals
- [ ] Works in non-interactive mode with defaults
- [ ] Hardware tests timeout after reasonable period
- [ ] Ctrl+C cleans up audio resources

---

## 5. Integration Points

### 5.1 Entry Point Changes (`main.py`)

```python
# In main.py, add:

from installer.core import InstallerCore, InstallerState
from installer.detector import InstallationDetector

def check_first_run() -> bool:
    # Existing implementation
    
def run_first_time_setup() -> AppConfig:
    """Run first-time setup with interactive installer."""
    installer = InstallerCore()
    
    # Phase 1: Check for previous installations
    previous = installer.detect_previous()
    if previous.has_previous:
        if not installer.prompt_removal(previous):
            print("Installation cancelled. Remove existing files manually.")
            sys.exit(0)
    
    # Phase 2: Hardware testing
    report = installer.run_hardware_tests()
    
    # Phase 3: Show configuration summary
    installer.display_config_summary()
    
    # Phase 4: Show known bugs
    installer.display_known_bugs()
    
    # Phase 5: Confirm and save
    if installer.confirm_installation():
        return installer.save_and_get_config()
    else:
        print("Installation cancelled.")
        sys.exit(0)
```

### 5.2 Existing Component Usage

**AudioDiscovery (audio_discovery.py):**
```python
# Reuse directly
from bridge.audio_discovery import AudioDiscovery, run_discovery, print_discovery_report
```

**AudioPipeline (audio_pipeline.py):**
```python
# Use for hardware testing
from bridge.audio_pipeline import AudioPipeline, AudioDeviceManager
```

**BugTracker (bug_tracker.py):**
```python
# Use for displaying bugs
from bridge.bug_tracker import BugTracker, BugStatus, BugSeverity
```

**Config (config.py):**
```python
# Use for saving/loading configuration
from bridge.config import AppConfig, get_config, DEFAULT_CONFIG_FILE
```

---

## 6. Command Line Interface

### New Flags Added to `main.py`

```
Usage: python -m bridge [OPTIONS]

Options:
  --interactive      Run interactive installation wizard
  --skip-hardware     Skip hardware testing during installation
  --show-bugs         Show known bugs from tracker and exit
  --force-setup       Force re-run of first-time setup

Existing:
  --config PATH       Use specific config file
  --log-level LEVEL   Set logging level
```

### Installer Commands (Future Enhancement)

```
bridge install              # Run installation wizard
bridge install --check      # Verify current installation
bridge install --repair    # Attempt to fix issues
bridge bugs                # Show known bugs
bridge bugs show ID        # Show bug details
bridge hardware test        # Run hardware tests
```

---

## 7. Acceptance Criteria Summary

### Functional Requirements

| ID | Requirement | Priority | Phase |
|----|-------------|----------|-------|
| F1 | Detect running voice-bridge processes | P1 | 1 |
| F2 | Find existing config files | P1 | 1 |
| F3 | Find existing data directories | P1 | 1 |
| F4 | Prompt before removing previous installation | P1 | 1 |
| F5 | List all audio input devices | P1 | 2 |
| F6 | List all audio output devices | P1 | 2 |
| F7 | Record test sample from microphone | P1 | 2 |
| F8 | Play back recorded sample | P1 | 2 |
| F9 | Allow retry of hardware test | P2 | 2 |
| F10 | Display configuration summary | P1 | 4 |
| F11 | Display unfixed bugs from tracker | P2 | 3 |
| F12 | Graceful degradation on failures | P2 | 5 |

### Non-Functional Requirements

| ID | Requirement | Priority |
|----|-------------|----------|
| N1 | No GUI frameworks (pure CLI/TUI) | P1 |
| N2 | Works on Python 3.9+ | P1 |
| N3 | No network requirements for installation | P1 |
| N4 | < 5 second startup time | P2 |
| N5 | Handles Ctrl+C cleanly | P1 |

---

## 8. Testing Strategy

### Unit Tests

```python
# test_detector.py
def test_detect_no_previous_installation():
    """No files exist, should return empty report."""
    
def test_detect_existing_config():
    """Config file exists, should be detected."""
    
def test_detect_running_process():
    """Process running, should be detected."""

# test_hardware_test.py
def test_list_input_devices():
    """All input devices listed."""
    
def test_list_output_devices():
    """All output devices listed."""
    
def test_mic_test_success():
    """Mic test completes successfully."""
    
def test_mic_test_no_device():
    """Handles missing device gracefully."""

# test_core.py
def test_install_flow_complete():
    """Full installation completes."""
    
def test_install_cancelled():
    """User cancels, should clean up."""
```

### Integration Tests

```python
# test_integration.py
def test_first_run_triggers_installer():
    """First run detects no config and starts installer."""
    
def test_existing_config_skips_installer():
    """Existing config skips installer unless --interactive."""
    
def test_hardware_test_saves_config():
    """Successful hardware test saves device selection."""
```

---

## 9. Dependencies

**Existing (No new imports needed):**
- `sounddevice` - Audio I/O
- `numpy` - Audio processing
- `structlog` - Logging
- `pydantic` - Config validation
- `psutil` - Process detection

**New (Add to requirements.txt):**
- None required - uses existing dependencies

---

## 10. Timeline Estimate

| Phase | Description | Effort | Priority |
|-------|-------------|--------|----------|
| 1 | Previous Installation Detection | 2-3h | P1 |
| 2 | Hardware Testing | 3-4h | P1 |
| 3 | Bug Tracker Integration | 1-2h | P2 |
| 4 | Installation Flow Integration | 2-3h | P1 |
| 5 | Polish & Edge Cases | 1-2h | P2 |
| **Total** | | **9-14h** | |

---

## 11. Risks & Mitigations

| Risk | Probability | Impact | Mitigation |
|------|-------------|--------|------------|
| Audio device permissions | Medium | High | Try multiple devices, clear error messages |
| Process detection false positives | Low | Medium | Match process names carefully |
| Config migration complexity | Low | Medium | Keep backup of old config |
| Terminal compatibility | Medium | Low | Detect capabilities, degrade gracefully |

---

## 12. Future Enhancements (Out of Scope)

- GUI-based installer (web or desktop)
- Automatic driver installation
- Network configuration testing
- Voice calibration (volume, sensitivity)
- Multi-language installation wizard
- Remote installation via SSH

---

## Appendix A: Code Examples

### A.1 Installation Detector Outline

```python
"""Installation detector for previous installations."""

import os
import psutil
from pathlib import Path
from dataclasses import dataclass
from typing import List, Optional

@dataclass
class ProcessInfo:
    pid: int
    name: str
    cmdline: List[str]
    
@dataclass  
class ConfigFile:
    path: Path
    size: int
    mtime: float

@dataclass
class InstallationReport:
    processes: List[ProcessInfo]
    config_files: List[ConfigFile]
    data_dirs: List[Path]
    
    @property
    def has_previous(self) -> bool:
        return bool(self.processes or self.config_files or self.data_dirs)

class InstallationDetector:
    CONFIG_DIR = Path.home() / ".voice-bridge"
    PROCESS_NAMES = ["bridge", "voice-bridge"]
    
    def find_running_processes(self) -> List[ProcessInfo]:
        """Find running voice-bridge processes."""
        found = []
        for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
            try:
                cmdline = proc.info.get('cmdline', []) or []
                if any(name in ' '.join(cmdline).lower() for name in self.PROCESS_NAMES):
                    found.append(ProcessInfo(
                        pid=proc.info['pid'],
                        name=proc.info['name'],
                        cmdline=cmdline
                    ))
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        return found
    
    def find_config_files(self) -> List[ConfigFile]:
        """Find existing config files."""
        files = []
        for pattern in ["config.yaml", "config.yml", ".env", "bugs.db"]:
            path = self.CONFIG_DIR / pattern
            if path.exists():
                stat = path.stat()
                files.append(ConfigFile(
                    path=path,
                    size=stat.st_size,
                    mtime=stat.st_mtime
                ))
        return files
    
    def scan(self) -> InstallationReport:
        """Run full installation scan."""
        return InstallationReport(
            processes=self.find_running_processes(),
            config_files=self.find_config_files(),
            data_dirs=[self.CONFIG_DIR] if self.CONFIG_DIR.exists() else []
        )
```

### A.2 Hardware Tester Outline

```python
"""Interactive hardware testing for installation."""

import time
import tempfile
from typing import Optional, Tuple
from dataclasses import dataclass

from bridge.audio_discovery import AudioDiscovery
from bridge.audio_pipeline import AudioPipeline, AudioDeviceType
from bridge.config import AudioConfig

DURATION_SECONDS = 3

@dataclass
class TestResult:
    success: bool
    device_name: str
    error: Optional[str] = None
    peak_level: float = 0.0
    snr_db: float = 0.0

class HardwareTester:
    def __init__(self):
        self.discovery = AudioDiscovery()
        self.discovery.discover()
        
    def list_input_devices(self):
        """List available input devices."""
        return self.discovery.devices
    
    def test_microphone(self, device_index: int) -> TestResult:
        """Test microphone with recording."""
        import sounddevice as sd
        import numpy as np
        
        try:
            # Record sample
            fs = 16000
            recording = sd.rec(
                int(DURATION_SECONDS * fs),
                samplerate=fs,
                channels=1,
                device=device_index
            )
            sd.wait()
            
            # Analyze
            peak = np.max(np.abs(recording))
            rms = np.sqrt(np.mean(recording ** 2))
            
            return TestResult(
                success=True,
                device_name=self._get_device_name(device_index),
                peak_level=float(peak),
                snr_db=20 * np.log10(peak / (rms + 1e-10))
            )
        except Exception as e:
            return TestResult(
                success=False,
                device_name="",
                error=str(e)
            )
    
    def test_speaker(self, device_index: int, audio_data) -> TestResult:
        """Test speaker with playback."""
        import sounddevice as sd
        
        try:
            sd.play(audio_data, samplerate=16000, device=device_index)
            sd.wait()
            return TestResult(
                success=True,
                device_name=self._get_device_name(device_index, output=True)
            )
        except Exception as e:
            return TestResult(
                success=False,
                device_name="",
                error=str(e)
            )
```

---

*Document created: 2026-03-03*
*Last updated: 2026-03-03*
*Status: Planning*