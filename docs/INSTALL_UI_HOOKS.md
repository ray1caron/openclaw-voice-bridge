# Voice Bridge Installation UI - Available Hooks

This document describes the integration points (hooks) available for building an Installation UI for the Voice-OpenClaw Bridge.

**Document Version:** 2026-03-03  
**Codebase:** voice-bridge-v4

---

## Table of Contents

1. [Previous Installation Detection Hooks](#1-previous-installation-detection-hooks)
2. [Audio Testing Hooks](#2-audio-testing-hooks)
3. [Bug Tracker Hooks](#3-bug-tracker-hooks)
4. [Configuration Hooks](#4-configuration-hooks)
5. [Installation Flow Recommendations](#5-installation-flow-recommendations)
6. [Gaps Requiring New Code](#6-gaps-requiring-new-code)

---

## 1. Previous Installation Detection Hooks

The bridge stores data in multiple locations. Use these hooks to detect and manage previous installations.

### 1.1 Detect Running Bridge Processes

**Location:** `stop_bridge.sh`, `start_bridge.sh`

**Current Code Pattern:**
```bash
# From stop_bridge.sh
pkill -f "bridge.main" 2>/dev/null
```

**Python Hook (Recommended for UI):**

```python
import subprocess
import signal
from pathlib import Path

def detect_running_bridge() -> dict:
    """
    Detect if bridge processes are running.
    
    Returns:
        dict with 'running', 'pids', 'can_stop' keys
    """
    result = {
        "running": False,
        "pids": [],
        "can_stop": False,
        "command": None
    }
    
    try:
        # Check for running bridge.main processes
        proc = subprocess.run(
            ["pgrep", "-f", "bridge.main"],
            capture_output=True, text=True
        )
        
        if proc.returncode == 0:
            pids = [int(p) for p in proc.stdout.strip().split('\n') if p]
            result["running"] = True
            result["pids"] = pids
            result["can_stop"] = True
            
            # Get command line for running process
            if pids:
                cmd_file = Path(f"/proc/{pids[0]}/cmdline")
                if cmd_file.exists():
                    result["command"] = cmd_file.read_text().replace('\x00', ' ')
    except Exception as e:
        result["error"] = str(e)
    
    return result

def stop_running_bridge(force: bool = False) -> bool:
    """
    Stop running bridge processes.
    
    Args:
        force: Use SIGKILL instead of SIGTERM
        
    Returns:
        True if stopped successfully
    """
    try:
        sig = signal.SIGKILL if force else signal.SIGTERM
        subprocess.run(["pkill", "-f" if not force else "-9", "-f", "bridge.main"])
        
        # Verify stopped
        import time
        for _ in range(10):
            time.sleep(0.5)
            if not detect_running_bridge()["running"]:
                return True
        
        return False
    except Exception:
        return False
```

### 1.2 Detect Existing Config Files

**Location:** `src/bridge/config.py` (lines 20-23)

**Paths Defined:**
```python
DEFAULT_CONFIG_DIR = Path.home() / ".voice-bridge"
DEFAULT_CONFIG_FILE = DEFAULT_CONFIG_DIR / "config.yaml"
DEFAULT_ENV_FILE = DEFAULT_CONFIG_DIR / ".env"
```

**Python Hook:**

```python
from pathlib import Path
from dataclasses import dataclass
from typing import Optional, List

@dataclass
class InstallationState:
    """Represents detected installation state."""
    config_dir_exists: bool
    config_file_exists: bool
    env_file_exists: bool
    venv_dir_exists: bool
    sessions_db_exists: bool
    bugs_db_exists: bool
    v2_config_exists: bool  # Legacy v2 config
    v3_config_exists: bool  # Legacy v3 config

def detect_previous_installation() -> InstallationState:
    """
    Detect all traces of previous installations.
    
    Checks for:
    - ~/.voice-bridge/ (current v4)
    - ~/.voice-bridge-v2/ (legacy)
    - ~/.config/voice-bridge-v2/ (legacy alternate)
    """
    config_dir = Path.home() / ".voice-bridge"
    v2_dir = Path.home() / ".voice-bridge-v2"
    v2_alt_dir = Path.home() / ".config" / "voice-bridge-v2"
    
    state = InstallationState(
        config_dir_exists=config_dir.exists(),
        config_file_exists=(config_dir / "config.yaml").exists(),
        env_file_exists=(config_dir / ".env").exists(),
        venv_dir_exists=(config_dir / "venv").exists(),
        sessions_db_exists=(config_dir / "data" / "sessions.db").exists(),
        bugs_db_exists=(config_dir / "bugs.db").exists(),
        v2_config_exists=v2_dir.exists() or v2_alt_dir.exists(),
        v3_config_exists=False  # v3 was same location as v4
    )
    
    return state

def get_config_details() -> dict:
    """
    Get detailed info about existing config.
    
    Returns:
        dict with config file content and metadata
    """
    config_path = Path.home() / ".voice-bridge" / "config.yaml"
    
    if not config_path.exists():
        return {"exists": False}
    
    import yaml
    from datetime import datetime
    
    stat = config_path.stat()
    
    with open(config_path) as f:
        content = yaml.safe_load(f)
    
    return {
        "exists": True,
        "path": str(config_path),
        "size_bytes": stat.st_size,
        "modified": datetime.fromtimestamp(stat.st_mtime).isoformat(),
        "content": content,
        "audio_device": content.get("audio", {}).get("input_device"),
        "output_device": content.get("audio", {}).get("output_device"),
    }
```

### 1.3 Detect Virtual Environments

**Location:** `setup.sh` (lines 17-20)

**Python Hook:**

```python
from pathlib import Path
import os

def detect_venvs() -> List[dict]:
    """
    Detect existing Python virtual environments.
    
    Returns:
        List of detected venv info dicts
    """
    venvs = []
    venv_locations = [
        Path.home() / ".voice-bridge" / "venv",
        Path.home() / ".voice-bridge-v2" / "venv",
        Path.cwd() / "venv",  # Project-local venv
    ]
    
    for venv_path in venv_locations:
        if venv_path.exists() and (venv_path / "bin" / "activate").exists():
            # Check Python version
            python_path = venv_path / "bin" / "python3"
            if python_path.exists():
                import subprocess
                try:
                    version = subprocess.check_output(
                        [str(python_path), "--version"],
                        text=True
                    ).strip()
                except:
                    version = "unknown"
            else:
                version = "unknown"
            
            venvs.append({
                "path": str(venv_path),
                "exists": True,
                "python_version": version,
                "size_mb": sum(
                    f.stat().st_size 
                    for f in venv_path.rglob("*") 
                    if f.is_file()
                ) // (1024 * 1024)
            })
    
    return venvs

def is_venv_active() -> bool:
    """Check if a venv is currently active."""
    return os.environ.get("VIRTUAL_ENV") is not None
```

### 1.4 Detect Database Files

**Location:** `src/bridge/conversation_store.py` (line 30), `src/bridge/bug_tracker.py` (line ~75)

**Paths Defined:**
- Sessions: `~/.voice-bridge/data/sessions.db`
- Bugs: `~/.voice-bridge/bugs.db`

**Python Hook:**

```python
from pathlib import Path
import sqlite3
from dataclasses import dataclass

@dataclass
class DatabaseInfo:
    path: Path
    exists: bool
    size_kb: int
    tables: List[str]
    row_counts: dict

def detect_databases() -> dict:
    """
    Detect and analyze database files.
    
    Returns:
        dict with info for each database
    """
    db_paths = {
        "sessions": Path.home() / ".voice-bridge" / "data" / "sessions.db",
        "bugs": Path.home() / ".voice-bridge" / "bugs.db",
    }
    
    databases = {}
    
    for name, path in db_paths.items():
        if not path.exists():
            databases[name] = DatabaseInfo(
                path=path, exists=False, size_kb=0,
                tables=[], row_counts={}
            )
            continue
        
        try:
            # Get table info
            conn = sqlite3.connect(str(path))
            cursor = conn.cursor()
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
            tables = [row[0] for row in cursor.fetchall()]
            
            # Get row counts
            row_counts = {}
            for table in tables:
                try:
                    cursor.execute(f"SELECT COUNT(*) FROM {table}")
                    row_counts[table] = cursor.fetchone()[0]
                except:
                    pass
            
            conn.close()
            
            databases[name] = DatabaseInfo(
                path=path,
                exists=True,
                size_kb=path.stat().st_size // 1024,
                tables=tables,
                row_counts=row_counts
            )
        except Exception as e:
            databases[name] = DatabaseInfo(
                path=path, exists=True, size_kb=0,
                tables=[], row_counts={}, error=str(e)
            )
    
    return databases
```

---

## 2. Audio Testing Hooks

### 2.1 Device Detection

**Location:** `src/bridge/audio_discovery.py`

**Key Classes/Functions:**
- `AudioDevice` dataclass - Device info
- `AudioDiscovery` class - Device enumeration
- `run_discovery()` - Convenience function
- `print_discovery_report()` - Formatted output

**Python Hook:**

```python
from bridge.audio_discovery import AudioDiscovery, run_discovery
from bridge.audio_pipeline import AudioDeviceManager, AudioDeviceType

def get_audio_devices() -> dict:
    """
    Get list of all audio devices with recommendations.
    
    Returns:
        dict with input_devices, output_devices, recommendations
    """
    discovery = AudioDiscovery()
    discovery.discover()
    
    return discovery.generate_report()

def list_input_devices() -> List[dict]:
    """List only input (microphone) devices."""
    manager = AudioDeviceManager()
    return [
        {
            "index": d.index,
            "name": d.name,
            "channels": d.channels,
            "sample_rate": d.sample_rate,
            "is_default": d.is_default
        }
        for d in manager.list_devices(AudioDeviceType.INPUT)
    ]

def list_output_devices() -> List[dict]:
    """List only output (speaker) devices."""
    manager = AudioDeviceManager()
    return [
        {
            "index": d.index,
            "name": d.name,
            "channels": d.channels,
            "sample_rate": d.sample_rate,
            "is_default": d.is_default
        }
        for d in manager.list_devices(AudioDeviceType.OUTPUT)
    ]

def get_recommended_devices() -> dict:
    """
    Get recommended input/output devices.
    
    Returns:
        dict with recommended input and output device info
    """
    discovery = run_discovery()
    input_dev = discovery.recommend_input()
    output_dev = discovery.recommend_output()
    
    return {
        "input": {
            "index": input_dev.index if input_dev else None,
            "name": input_dev.name if input_dev else None,
        },
        "output": {
            "index": output_dev.index if output_dev else None,
            "name": output_dev.name if output_dev else None,
        }
    }
```

### 2.2 Microphone Test (Record & Playback)

**Location:** `test_audio.sh` (lines 54-69)

**Current Shell Implementation:**
```bash
python3 -c "
import sounddevice as sd
import numpy as np

print('Recording 2 seconds from default microphone...')
duration = 2
sample_rate = 16000
recording = sd.rec(int(duration * sample_rate), samplerate=sample_rate, channels=1)
sd.wait()

max_amplitude = np.max(np.abs(recording))
if max_amplitude > 0.01:
    print(f'✓ Microphone working (max amplitude: {max_amplitude:.3f})')
else:
    print('⚠ Microphone may be muted or not working')
"
```

**Python Hook (Recommended for UI):**

```python
import numpy as np
import sounddevice as sd
from dataclasses import dataclass
from typing import Optional
import tempfile
import wave

@dataclass
class MicTestResult:
    success: bool
    max_amplitude: float
    message: str
    recording_path: Optional[str] = None
    error: Optional[str] = None

def test_microphone(
    device_index: Optional[int] = None,
    duration: float = 2.0,
    sample_rate: int = 16000
) ->MicTestResult:
    """
    Test microphone by recording audio.
    
    Args:
        device_index: Device index (None = default)
        duration: Recording duration in seconds
        sample_rate: Audio sample rate
        
    Returns:
        MicTestResult with success status and metrics
    """
    try:
        # Record audio
        recording = sd.rec(
            int(duration * sample_rate),
            samplerate=sample_rate,
            channels=1,
            device=device_index,
            dtype=np.int16
        )
        sd.wait()
        
        # Analyze recording
        max_amplitude = float(np.max(np.abs(recording)))
        mean_amplitude = float(np.mean(np.abs(recording)))
        
        # Save to temp file for playback
        temp_file = tempfile.NamedTemporaryFile(
            suffix=".wav", delete=False
        )
        temp_path = temp_file.name
        temp_file.close()
        
        with wave.open(temp_path, 'wb') as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)  # 16-bit
            wf.setframerate(sample_rate)
            wf.writeframes(recording.tobytes())
        
        # Determine success
        if max_amplitude < 100:  # Very quiet
            returnMicTestResult(
                success=False,
                max_amplitude=max_amplitude,
                message="Microphone appears to be muted or not working",
                recording_path=temp_path
            )
        elif max_amplitude < 500:
            returnMicTestResult(
                success=True,
                max_amplitude=max_amplitude,
                message=f"Microphone working but quiet (amplitude: {max_amplitude:.0f})",
                recording_path=temp_path
            )
        else:
            return MicTestResult(
                success=True,
                max_amplitude=max_amplitude,
                message=f"Microphone working well (amplitude: {max_amplitude:.0f})",
                recording_path=temp_path
            )
            
    except Exception as e:
        returnMicTestResult(
            success=False,
            max_amplitude=0,
            message=f"Microphone test failed: {str(e)}",
            error=str(e)
        )

def playback_recording(
    recording_path: str,
    device_index: Optional[int] = None
) -> bool:
    """
    Play back a recorded audio file.
    
    Args:
        recording_path: Path to WAV file
        device_index: Output device index (None = default)
        
    Returns:
        True if playback succeeded
    """
    try:
        import soundfile as sf
        data, sr = sf.read(recording_path)
        sd.play(data, sr, device=device_index)
        sd.wait()
        return True
    except Exception as e:
        return False
```

### 2.3 Speaker Test (Play Tone)

**Location:** `test_audio.sh` (lines 72-84)

**Current Shell Implementation:**
```bash
python3 -c "
import sounddevice as sd
import numpy as np

print('Playing test tone (440Hz for 0.5 seconds)...')
sample_rate = 16000
duration = 0.5
t = np.linspace(0, duration, int(sample_rate * duration))
tone = np.sin(2 * np.pi * 440 * t) * 0.3
sd.play(tone, sample_rate)
sd.wait()
print('✓ Speakers working')
"
```

**Python Hook (Recommended for UI):**

```python
import numpy as np
import sounddevice as sd
from dataclasses import dataclass
from typing import Optional

@dataclass
class SpeakerTestResult:
    success: bool
    message: str
    error: Optional[str] = None

def test_speakers(
    device_index: Optional[int] = None,
    frequency: int = 440,
    duration: float = 0.5,
    sample_rate: int = 16000,
    volume: float = 0.3
) ->SpeakerTestResult:
    """
    Test speakers by playing a tone.
    
    Args:
        device_index: Output device index (None = default)
        frequency: Tone frequency in Hz
        duration: Duration in seconds
        sample_rate: Audio sample rate
        volume: Volume level (0.0 to 1.0)
        
    Returns:
        SpeakerTestResult with success status
    """
    try:
        # Generate tone
        t = np.linspace(0, duration, int(sample_rate * duration))
        tone = np.sin(2 * np.pi * frequency * t) * volume
        
        # Play tone
        sd.play(tone, sample_rate, device=device_index)
        sd.wait()
        
        return SpeakerTestResult(
            success=True,
            message=f"Test tone played successfully ({frequency}Hz, {duration}s)"
        )
        
    except Exception as e:
        return SpeakerTestResult(
            success=False,
            message=f"Speaker test failed: {str(e)}",
            error=str(e)
        )

def play_test_tone_sequence(
    device_index: Optional[int] = None,
    frequencies: List[int] = [440, 880]  # A4 and A5
) -> List[SpeakerTestResult]:
    """
    Play a sequence of test tones.
    
    Useful for verifying speaker range.
    """
    results = []
    for freq in frequencies:
        result = test_speakers(device_index, frequency=freq)
        results.append(result)
    return results
```

### 2.4 Full Audio Pipeline Validation

**Location:** `src/bridge/audio_pipeline.py`

**Python Hook:**

```python
from bridge.audio_pipeline import AudioPipeline, PipelineState
from bridge.config import AudioConfig
from dataclasses import dataclass

@dataclass
class AudioValidationResult:
    input_available: bool
    output_available: bool
    input_device_name: str
    output_device_name: str
    can_capture: bool
    can_playback: bool
    vad_working: bool
    errors: List[str]

def validate_audio_pipeline(
    input_device: Optional[str] = None,
    output_device: Optional[str] = None
) -> AudioValidationResult:
    """
    Full validation of audio pipeline.
    
    Tests device detection, capture setup, and playback setup.
    
    Returns:
        AudioValidationResult with detailed status
    """
    result = AudioValidationResult(
        input_available=False,
        output_available=False,
        input_device_name="",
        output_device_name="",
        can_capture=False,
        can_playback=False,
        vad_working=False,
        errors=[]
    )
    
    try:
        # Create pipeline
        config = AudioConfig(
            input_device=input_device or "default",
            output_device=output_device or "default"
        )
        pipeline = AudioPipeline(audio_config=config)
        
        # Check devices
        if not pipeline.initialize_devices(input_device, output_device):
            result.errors.append("Failed to initialize audio devices")
            return result
        
        # Get device info
        if pipeline._input_device:
            result.input_available = True
            result.input_device_name = pipeline._input_device.name
        
        if pipeline._output_device:
            result.output_available = True
            result.output_device_name = pipeline._output_device.name
        
        # Test capture start/stop
        try:
            if pipeline.start_capture():
                result.can_capture = True
                pipeline.stop_capture()
        except Exception as e:
            result.errors.append(f"Capture test failed: {e}")
        
        # Test playback start/stop
        try:
            if pipeline.start_playback():
                result.can_playback = True
                pipeline.stop_playback()
        except Exception as e:
            result.errors.append(f"Playback test failed: {e}")
        
        # Test VAD
        try:
            result.vad_working = pipeline.vad.is_available
        except Exception as e:
            result.errors.append(f"VAD test failed: {e}")
            
    except Exception as e:
        result.errors.append(f"Pipeline validation failed: {e}")
    
    return result
```

---

## 3. Bug Tracker Hooks

**Location:** `src/bridge/bug_tracker.py`, `src/bridge/bug_cli.py`

### 3.1 Query Unfixed Bugs

**Python Hook:**

```python
from bridge.bug_tracker import BugTracker, BugStatus, BugSeverity
from dataclasses import dataclass
from typing import List, Optional

@dataclass
class BugSummary:
    total: int
    new: int
    in_progress: int
    critical: int
    high: int
    bugs: List[dict]

def get_bug_summary(
    include_fixed: bool = False,
    component: Optional[str] = None
) -> BugSummary:
    """
    Get summary of bugs for UI display.
    
    Args:
        include_fixed: Include fixed/closed bugs
        component: Filter by component (audio, stt, tts, etc.)
        
    Returns:
        BugSummary with counts and bug list
    """
    tracker = BugTracker.get_instance()
    
    # Get stats
    stats = tracker.get_stats()
    
    # Get bug list (default: only unfixed)
    status_filter = None if include_fixed else BugStatus.NEW
    bugs = tracker.list_bugs(
        status=status_filter,
        component=component,
        limit=100
    )
    
    return BugSummary(
        total=stats["total"],
        new=stats["new"],
        in_progress=sum(1 for b in bugs if b.status == "in_progress"),
        critical=stats["critical"],
        high=sum(1 for b in bugs if b.severity == "high"),
        bugs=[
            {
                "id": b.id,
                "severity": b.severity,
                "component": b.component,
                "title": b.title,
                "status": b.status,
                "timestamp": b.timestamp
            }
            for b in bugs
        ]
    )

def get_bug_details(bug_id: int) -> Optional[dict]:
    """
    Get detailed information about a specific bug.
    
    Args:
        bug_id: Bug ID number
        
    Returns:
        Bug details dict or None if not found
    """
    tracker = BugTracker.get_instance()
    bug = tracker.get_bug(bug_id)
    
    if not bug:
        return None
    
    return bug.to_dict()

def get_unfixed_count() -> dict:
    """
    Get count of unfixed bugs by severity.
    
    Returns:
        dict with counts by severity
    """
    tracker = BugTracker.get_instance()
    bugs = tracker.list_bugs(limit=1000)
    
    unfixed = [b for b in bugs if b.status not in ("fixed", "closed")]
    
    return {
        "total_unfixed": len(unfixed),
        "critical": sum(1 for b in unfixed if b.severity == "critical"),
        "high": sum(1 for b in unfixed if b.severity == "high"),
        "medium": sum(1 for b in unfixed if b.severity == "medium"),
        "low": sum(1 for b in unfixed if b.severity == "low"),
    }
```

### 3.2 Display Bug Summary

**Python Hook:**

```python
def format_bug_report_for_display(summary: BugSummary) -> str:
    """
    Format bug summary for terminal/text display.
    
    Args:
        summary: BugSummary from get_bug_summary()
        
    Returns:
        Formatted string for display
    """
    lines = [
        "=" * 60,
        "📊 Bug Report Summary",
        "=" * 60,
        f"Total: {summary.total} | New: {summary.new} | In Progress: {summary.in_progress}",
        f"Critical: {summary.critical} | High: {summary.high}",
        "=" * 60,
    ]
    
    if not summary.bugs:
        lines.append("✓ No unfixed bugs!")
    else:
        lines.append("\nRecent Unfixed Bugs:")
        for bug in summary.bugs[:10]:  # Show first 10
            severity_icon = {
                "critical": "🔴",
                "high": "🟠",
                "medium": "🟡",
                "low": "🟢",
            }.get(bug["severity"], "⚪")
            lines.append(
                f"  {severity_icon} #{bug['id']} [{bug['component']}] {bug['title'][:40]}"
            )
    
    return "\n".join(lines)

def format_bug_for_terminal(bug_id: int) -> str:
    """
    Format single bug details for terminal display.
    
    Args:
        bug_id: Bug ID
        
    Returns:
        Formatted string or error message
    """
    details = get_bug_details(bug_id)
    
    if not details:
        return f"Bug #{bug_id} not found"
    
    severity_colors = {
        "critical": "RED",
        "high": "ORANGE",
        "medium": "YELLOW",
        "low": "GREEN",
    }
    
    return f"""
{'=' * 60}
Bug #{details['id']}: {details['title']}
{'=' * 60}
Severity: {details['severity'].upper()}
Component: {details['component']}
Status: {details['status']}
Created: {details['created_at']}

Description:
{details['description']}

{'─' * 40}
System State:
{json.dumps(details['system_state'], indent=2)[:500]}...

{'─' * 40}
Stack Trace:
{details['stack_trace'] or 'No stack trace captured'}
{'=' * 60}
"""
```

---

## 4. Configuration Hooks

**Location:** `src/bridge/config.py`

### 4.1 Show Current Config

**Python Hook:**

```python
from bridge.config import AppConfig, get_config, DEFAULT_CONFIG_FILE
from pathlib import Path
import yaml
from dataclasses import dataclass
from typing import Optional, List

@dataclass
class ConfigDisplay:
    exists: bool
    path: str
    content: dict
    sections: List[str]
    editable_fields: List[dict]

def get_config_for_display(config_path: Optional[Path] = None) -> ConfigDisplay:
    """
    Get current configuration for UI display.
    
    Args:
        config_path: Custom config path (defaults to ~/.voice-bridge/config.yaml)
        
    Returns:
        ConfigDisplay with config details
    """
    config_path = config_path or DEFAULT_CONFIG_FILE
    
    if not config_path.exists():
        return ConfigDisplay(
            exists=False,
            path=str(config_path),
            content={},
            sections=[],
            editable_fields=[]
        )
    
    # Load raw YAML
    with open(config_path) as f:
        content = yaml.safe_load(f)
    
    # Get structured config
    config = AppConfig.load(config_path)
    
    # Define editable fields for UI
    editable = [
        # Audio
        {"section": "audio", "field": "input_device", "type": "device_input", 
         "value": config.audio.input_device, "description": "Microphone device"},
        {"section": "audio", "field": "output_device", "type": "device_output",
         "value": config.audio.output_device, "description": "Speaker device"},
        {"section": "audio", "field": "sample_rate", "type": "int",
         "value": config.audio.sample_rate, "description": "Audio sample rate"},
        
        # STT
        {"section": "stt", "field": "model", "type": "choice",
         "value": config.stt.model, "description": "Whisper model size",
         "choices": ["tiny", "base", "small", "medium", "large", "large-v2", "large-v3"]},
        {"section": "stt", "field": "language", "type": "string",
         "value": config.stt.language, "description": "Language code (null = auto)"},
        
        # TTS
        {"section": "tts", "field": "voice", "type": "string",
         "value": config.tts.voice, "description": "Piper voice model"},
        
        # Bridge
        {"section": "bridge", "field": "wake_word", "type": "string",
         "value": config.bridge.wake_word, "description": "Wake phrase"},
        {"section": "bridge", "field": "log_level", "type": "choice",
         "value": config.bridge.log_level, "description": "Logging level",
         "choices": ["DEBUG", "INFO", "WARNING", "ERROR"]},
        
        # OpenClaw
        {"section": "openclaw", "field": "host", "type": "string",
         "value": config.openclaw.host, "description": "OpenClaw server host"},
        {"section": "openclaw", "field": "port", "type": "int",
         "value": config.openclaw.port, "description": "OpenClaw server port"},
    ]
    
    return ConfigDisplay(
        exists=True,
        path=str(config_path),
        content=content,
        sections=list(content.keys()),
        editable_fields=editable
    )

def validate_config_value(section: str, field: str, value: any) -> tuple[bool, str]:
    """
    Validate a config value before saving.
    
    Returns:
        (is_valid, error_message) tuple
    """
    validators = {
        ("audio", "sample_rate"): lambda v: (8000 <= v <= 192000, "Must be 8000-192000"),
        ("audio", "channels"): lambda v: v in (1, 2), "Must be 1 or 2",
        ("stt", "model"): lambda v: v in ["tiny", "base", "small", "medium", "large", "large-v2", "large-v3"],
        ("bridge", "log_level"): lambda v: v in ["DEBUG", "INFO", "WARNING", "ERROR"],
        ("openclaw", "port"): lambda v: (1 <= v <= 65535, "Must be 1-65535"),
    }
    
    key = (section, field)
    if key in validators:
        validator = validators[key]
        if callable(validator):
            result = validator(value)
            if isinstance(result, tuple):
                return result
            return (result, "")
        return (validator(value), "")
    
    return (True, "")  # No validator, assume valid
```

### 4.2 Modify Settings

**Python Hook:**

```python
import shutil
from datetime import datetime

def backup_config(config_path: Optional[Path] = None) -> Path:
    """
    Create backup of current config.
    
    Returns:
        Path to backup file
    """
    config_path = config_path or DEFAULT_CONFIG_FILE
    
    if not config_path.exists():
        raise FileNotFoundError("No config file to backup")
    
    backup_path = config_path.with_suffix(f".yaml.bak.{datetime.now():%Y%m%d_%H%M%S}")
    shutil.copy(config_path, backup_path)
    
    return backup_path

def update_config_field(
    section: str,
    field: str,
    value: any,
    config_path: Optional[Path] = None,
    create_backup: bool = True
) -> bool:
    """
    Update a single config field.
    
    Args:
        section: Config section (audio, stt, tts, bridge, etc.)
        field: Field name within section
        value: New value
        config_path: Custom config path
        create_backup: Create backup before modifying
        
    Returns:
        True if update succeeded
    """
    config_path = config_path or DEFAULT_CONFIG_FILE
    
    try:
        # Validate
        is_valid, error = validate_config_value(section, field, value)
        if not is_valid:
            raise ValueError(f"Invalid value: {error}")
        
        # Backup
        if create_backup and config_path.exists():
            backup_config(config_path)
        
        # Load, modify, save
        config = AppConfig.load(config_path)
        
        # Update nested value
        section_obj = getattr(config, section)
        setattr(section_obj, field, value)
        
        config.save()
        
        return True
        
    except Exception as e:
        return False

def reset_config_to_defaults(config_path: Optional[Path] = None) -> Path:
    """
    Reset config to defaults, creating backup first.
    
    Returns:
        Path to backup of old config
    """
    config_path = config_path or DEFAULT_CONFIG_FILE
    
    # Backup existing
    if config_path.exists():
        backup_path = backup_config(config_path)
    else:
        backup_path = None
    
    # Create default config
    config = AppConfig()
    config.save(config_path)
    
    return backup_path

def set_audio_devices(
    input_device: str,
    output_device: str,
    config_path: Optional[Path] = None
) ->bool:
    """
    Convenience function to set audio devices.
    
    Args:
        input_device: Input device name or index
        output_device: Output device name or index
        
    Returns:
        True if successful
    """
    config_path = config_path or DEFAULT_CONFIG_FILE
    config = AppConfig.load(config_path)
    
    config.audio.input_device = input_device
    config.audio.output_device = output_device
    
    config.save()
    return True
```

### 4.3 Config Validation

**Python Hook:**

```python
from bridge.config import AppConfig
from pydantic import ValidationError

@dataclass
class ConfigValidationResult:
    valid: bool
    errors: List[str]
    warnings: List[str]

def validate_config_file(config_path: Optional[Path] = None) -> ConfigValidationResult:
    """
    Validate config file for errors.
    
    Returns:
        ConfigValidationResult with any errors/warnings
    """
    config_path = config_path or DEFAULT_CONFIG_FILE
    result = ConfigValidationResult(valid=True, errors=[], warnings=[])
    
    if not config_path.exists():
        result.valid = False
        result.errors.append("Config file does not exist")
        return result
    
    try:
        # This will validate during loading
        config = AppConfig.load(config_path)
        
        # Additional runtime checks
        import sounddevice as sd
        
        # Check input device
        devices = sd.query_devices()
        input_names = [d['name'] for d in devices if d['max_input_channels'] > 0]
        if config.audio.input_device not in input_names:
            if config.audio.input_device != "default":
                result.warnings.append(
                    f"Input device '{config.audio.input_device}' not found. "
                    f"Available: {', '.join(input_names[:5])}..."
                )
        
        # Check output device
        output_names = [d['name'] for d in devices if d['max_output_channels'] > 0]
        if config.audio.output_device not in output_names:
            if config.audio.output_device != "default":
                result.warnings.append(
                    f"Output device '{config.audio.output_device}' not found."
                )
                
    except ValidationError as e:
        result.valid = False
        for error in e.errors():
            field = ".".join(str(loc) for loc in error['loc'])
            result.errors.append(f"{field}: {error['msg']}")
    except Exception as e:
        result.valid = False
        result.errors.append(str(e))
    
    return result
```

---

## 5. Installation Flow Recommendations

### Recommended Installation UI Flow

```
1. PRE-CHECK PHASE
   ├── Detect running bridge processes → offer to stop
   ├── Detect previous installations
   │   ├── Show config details if exists
   │   ├── Offer to backup/reset
   │   └── Check for venv, databases
   └── Check for unfixed bugs → show summary

2. AUDIO SETUP PHASE
   ├── List available devices
   ├── Get recommendations
   ├── Test microphone
   │   ├── Record 2-second sample
   │   ├── Show waveform/amplitude
   │   └── Offer playback
   ├── Test speakers
   │   └── Play test tone
   └── Save device selection

3. CONFIGURATION PHASE
   ├── Show current config (or defaults)
   ├── Allow editing key fields:
   │   ├── Audio devices
   │   ├── Wake word
   │   ├── STT model
   │   └── OpenClaw connection
   └── Validate config

4. INSTALLATION PHASE
   ├── Create venv (or reuse)
   ├── Install dependencies
   ├── Run audio validation
   └── Save config

5. VERIFICATION PHASE
   ├── Run test_audio.sh equivalent
   ├── Show success/failure summary
   └── Offer to start bridge
```

### Example Installation UI Integration

```python
class InstallationUI:
    """
    High-level installation UI controller.
    
    Coordinates all hooks for a complete installation flow.
    """
    
    def __init__(self):
        self.state = {
            "phase": "pre_check",
            "previous_install": None,
            "audio_valid": False,
            "config_valid": False,
            "install_complete": False
        }
    
    def run_pre_checks(self) -> dict:
        """Run all pre-installation checks."""
        return {
            "running_process": detect_running_bridge(),
            "previous_install": detect_previous_installation(),
            "venvs": detect_venvs(),
            "databases": detect_databases(),
            "bugs": get_unfixed_count()
        }
    
    def run_audio_tests(self) -> dict:
        """Run all audio device tests."""
        return {
            "devices": get_audio_devices(),
            "mic_test": test_microphone(),
            "speaker_test": test_speakers(),
            "pipeline_validation": validate_audio_pipeline()
        }
    
    def get_current_config(self) -> ConfigDisplay:
        """Get config for display/editing."""
        return get_config_for_display()
    
    def apply_audio_config(
        self,
        input_device: str,
        output_device: str
    ) -> bool:
        """Apply audio device configuration."""
        return set_audio_devices(input_device, output_device)
    
    def validate_setup(self) ->dict:
        """Validate complete setup."""
        return {
            "config": validate_config_file(),
            "audio": validate_audio_pipeline(),
            "bugs": get_unfixed_count()
        }
```

---

## 6. Gaps Requiring New Code

### 6.1 Need: Clean Uninstall/Reset Function

**Current Gap:** No way to cleanly remove all traces.

**Recommended Implementation:**

```python
def clean_installation(keep_config: bool = False) -> dict:
    """
    Remove all installation traces.
    
    Args:
        keep_config: Keep config.yaml and .env files
        
    Returns:
        dict of removed items
    """
    import shutil
    
    removed = {
        "venv": False,
        "databases": [],
        "logs": [],
        "config": [] if keep_config else ["config.yaml", ".env"]
    }
    
    config_dir = Path.home() / ".voice-bridge"
    
    # Stop any running processes
    stop_running_bridge(force=True)
    
    # Remove venv
    venv_path = config_dir / "venv"
    if venv_path.exists():
        shutil.rmtree(venv_path)
        removed["venv"] = True
    
    # Remove databases
    for db in ["data/sessions.db", "bugs.db"]:
        db_path = config_dir / db
        if db_path.exists():
            db_path.unlink()
            removed["databases"].append(db)
    
    # Remove logs
    log_dir = Path.home() / ".local" / "state" / "voice-bridge" / "logs"
    if log_dir.exists():
        shutil.rmtree(log_dir)
        removed["logs"].append(str(log_dir))
    
    # Remove config if requested
    if not keep_config:
        for cfg_file in ["config.yaml", ".env"]:
            cfg_path = config_dir / cfg_file
            if cfg_path.exists():
                cfg_path.unlink()
                removed["config"].append(cfg_file)
    
    return removed
```

### 6.2 Need: Dependency Check Function

**Current Gap:** No Python to check if dependencies are installed.

**Recommended Implementation:**

```python
def check_dependencies() -> dict:
    """
    Check if required dependencies are installed.
    
    Returns:
        dict with status of each dependency
    """
    dependencies = {
        "sounddevice": "Audio I/O",
        "soundfile": "Audio file handling",
        "numpy": "Numerical computing",
        "webrtcvad": "Voice activity detection",
        "pydantic": "Config validation",
        "yaml": "Config loading",
        "structlog": "Logging",
    }
    
    results = {}
    
    for module, description in dependencies.items():
        try:
            __import__(module)
            results[module] = {
                "installed": True,
                "description": description
            }
        except ImportError:
            results[module] = {
                "installed": False,
                "description": description
            }
    
    return results

def install_dependencies(venv_path: Optional[Path] = None) -> bool:
    """
    Install missing dependencies.
    
    Args:
        venv_path: Path to venv (uses active venv if None)
        
    Returns:
        True if successful
    """
    import subprocess
    import sys
    
    pip = sys.executable.replace("python", "pip")
    if venv_path:
        pip = str(venv_path / "bin" / "pip")
    
    result = subprocess.run(
        [pip, "install", "-r", "requirements.txt"],
        capture_output=True
    )
    
    return result.returncode == 0
```

### 6.3 Need: First-Run Audio Wizard

**Current Gap:** First run just runs auto-discovery without user interaction.

**Recommended Implementation:**

```python
def run_audio_wizard() -> dict:
    """
    Interactive audio setup wizard.
    
    Returns:
        dict with wizard results
    """
    results = {
        "input_device": None,
        "output_device": None,
        "mic_test_passed": False,
        "speaker_test_passed": False
    }
    
    # 1. List devices
    devices = get_audio_devices()
    
    # 2. Get recommendations
    recommended = get_recommended_devices()
    
    # 3. Let user select (would be interactive in real UI)
    # ... user selects input/output ...
    
    # 4. Test microphone
    mic_result = test_microphone()
    results["mic_test_passed"] = mic_result.success
    
    if mic_result.recording_path:
        # Play back for user verification
        playback_recording(mic_result.recording_path)
    
    # 5. Test speakers
    speaker_result = test_speakers()
    results["speaker_test_passed"] = speaker_result.success
    
    # 6. Validate full pipeline
    validation = validate_audio_pipeline(
        results["input_device"],
        results["output_device"]
    )
    
    results["validation"] = validation
    
    return results
```

### 6.4 Need: Installation Summary/Report

**Current Gap:** No way to generate a complete status report.

**Recommended Implementation:**

```python
def generate_installation_report() -> str:
    """
    Generate a complete installation status report.
    
    Returns:
        Formatted report string
    """
    report = []
    report.append("=" * 60)
    report.append("Voice Bridge Installation Report")
    report.append("=" * 60)
    
    # 1. Installation state
    install_state = detect_previous_installation()
    report.append("\n📁 Installation:")
    report.append(f"   Config Dir: {'✓' if install_state.config_dir_exists else '✗'}")
    report.append(f"   Config File: {'✓' if install_state.config_file_exists else '✗'}")
    report.append(f"   Virtual Env: {'✓' if install_state.venv_dir_exists else '✗'}")
    
    # 2. Running processes
    running = detect_running_bridge()
    report.append("\n🔄 Process Status:")
    if running["running"]:
        report.append(f"   Running: YES (PIDs: {running['pids']})")
    else:
        report.append("   Running: NO")
    
    # 3. Audio devices
    devices = get_audio_devices()
    report.append(f"\n🎤 Audio Devices:")
    report.append(f"   Input: {len(devices['input_devices'])} found")
    report.append(f"   Output: {len(devices['output_devices'])} found")
    report.append(f"   Recommended Input: {devices['recommended_input']['name']}")
    report.append(f"   Recommended Output: {devices['recommended_output']['name']}")
    
    # 4. Config
    config = get_config_for_display()
    if config.exists:
        report.append("\n⚙️  Configuration:")
        report.append(f"   Wake Word: {config.content.get('bridge', {}).get('wake_word')}")
        report.append(f"   STT Model: {config.content.get('stt', {}).get('model')}")
        report.append(f"   OpenClaw Host: {config.content.get('openclaw', {}).get('host')}")
    
    # 5. Bugs
    bugs = get_unfixed_count()
    report.append("\n🐛 Bug Tracker:")
    report.append(f"   Unfixed: {bugs['total_unfixed']}")
    if bugs['critical'] > 0:
        report.append(f"   ⚠️  CRITICAL: {bugs['critical']}")
    if bugs['high'] > 0:
        report.append(f"   ⚠️  HIGH: {bugs['high']}")
    
    # 6. Dependencies
    deps = check_dependencies()
    report.append("\n📦 Dependencies:")
    missing = [name for name, info in deps.items() if not info['installed']]
    if missing:
        report.append(f"   ✗ Missing: {', '.join(missing)}")
    else:
        report.append("   ✓ All installed")
    
    report.append("=" * 60)
    return "\n".join(report)
```

---

## Summary

The Voice Bridge codebase already has excellent hooks for building an Installation UI:

| Feature | Location | Status |
|---------|----------|--------|
| Device Detection | `audio_discovery.py` | ✅ Complete |
| Config Management | `config.py` | ✅ Complete |
| Bug Tracking | `bug_tracker.py` | ✅ Complete |
| Audio Pipeline | `audio_pipeline.py` | ✅ Complete |
| Process Management | `stop_bridge.sh` | ⚠️ Shell script, needs Python port |
| Installation State | N/A | ❌ Needs implementation |
| Clean Uninstall | N/A | ❌ Needs implementation |
| Dependency Check | N/A | ❌ Needs implementation |

**Key Files to Import:**
```python
from bridge.config import AppConfig, get_config
from bridge.audio_discovery import AudioDiscovery, run_discovery
from bridge.audio_pipeline import AudioPipeline, AudioDeviceManager
from bridge.bug_tracker import BugTracker, BugStatus, BugSeverity
from bridge.conversation_store import ConversationStore
```

**Key Paths:**
- Config: `~/.voice-bridge/config.yaml`
- Env: `~/.voice-bridge/.env`
- Venv: `~/.voice-bridge/venv/`
- Sessions DB: `~/.voice-bridge/data/sessions.db`
- Bugs DB: `~/.voice-bridge/bugs.db`