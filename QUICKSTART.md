# Quick Start: Voice Bridge v4.4.0

**Version:** 4.4.0
**Last Updated:** 2026-03-14

---

## Run the Installer

```bash
cd /home/hal/.openclaw/workspace/voice-bridge-v4
PYTHONPATH=src python3 -m installer
```

Or use the startup script:
```bash
./run.sh
```

---

## Installation Steps

The installer guides you through setup in 9 steps:

| Step | Description | Interactive? |
|------|-------------|--------------|
| 1 | Previous installations check | Auto |
| 2 | Clean up old installation (if needed) | Auto |
| 3 | Dependencies check | Auto |
| 4 | Audio hardware test | **Yes** — Records & plays audio |
| 5 | Configuration | **Yes** — Shows config summary |
| 6 | OpenClaw connectivity check | Auto |
| 7 | **Bridge integration test** | **Yes** — Runs real VoiceOrchestrator |
| 8 | Known issues check | Auto |
| 9 | Installation summary | Auto |

### What Happens in Each Step

#### Step 1: Previous Installations
- Scans for existing Voice Bridge installations and running processes
- Proceeds to Step 2 if anything is found; skips otherwise

#### Step 2: Clean Up (conditional)
- Stops any running bridge processes
- Removes stale config/data files from previous installs

#### Step 3: Dependencies
- Verifies all required Python packages are installed
- Lists missing packages with exact `pip install` commands

#### Step 4: Audio Hardware Test
- Records 3 seconds from the microphone
- Plays the recording back through speakers
- **Optional:** Speech-to-Text transcription test
- **Optional:** Text-to-Speech playback test
- **Optional:** Wake word acknowledgement test (uses real bridge — see Step 7)

#### Step 5: Configuration
- Searches for config in order:
  1. `~/.voice-bridge/config.yaml`
  2. `$XDG_CONFIG_HOME/voice-bridge/config.yaml`
  3. `$XDG_CONFIG_HOME/voice-bridge-v2/config.yaml`
- Shows configuration summary:
  - Wake word and detection backend
  - STT model
  - TTS voice
  - Audio device
  - OpenClaw URL and WebSocket port

#### Step 6: OpenClaw Connectivity Check
- Makes an HTTP request to OpenClaw to verify it is reachable
- Reports the host, port, and response status
- Failure here is advisory — the bridge can still be tested without OpenClaw

#### Step 7: Bridge Integration Test
Runs the **real `VoiceOrchestrator`** — the exact same code path used in production.

- **Startup test (automatic):** Opens audio devices, loads STT/TTS/wake-word models. Fails immediately if any component cannot initialise.
- **Wake word test (interactive):** Prompts you to say the wake word. Listens using the live microphone and wake-word detector.
- **OpenClaw ACK test:** After wake word detection, waits for OpenClaw to send a response.
- **TTS test:** If OpenClaw responds, plays the TTS audio through the speakers.

On failure the installer shows:
- Which component failed (audio / STT / TTS / wake-word detector)
- Full exception traceback
- All bridge log lines emitted during the test
- Audio pipeline stats (frames processed — 0 = microphone not working)
- Any new bug-tracker entries written during the test

Add `--debug` to see full log output even when the test passes.

#### Step 8: Known Issues Check
- Scans the bug database for unresolved issues
- Shows critical and high-severity bugs
- Offers to continue or abort

#### Step 9: Installation Summary
- Lists what was configured
- Shows next steps
- Provides the run command

---

## CLI Options

```bash
# Interactive mode (default)
PYTHONPATH=src python3 -m installer

# Automatic mode (no prompts)
PYTHONPATH=src python3 -m installer --auto

# Full debug output during bridge test (traceback + all bridge logs)
PYTHONPATH=src python3 -m installer --debug

# Test audio hardware only
PYTHONPATH=src python3 -m installer --test-audio

# Show known bugs
PYTHONPATH=src python3 -m installer --show-bugs

# View config
PYTHONPATH=src python3 -m installer --show-config

# Clean previous install
PYTHONPATH=src python3 -m installer --clean
```

See [INSTALLER.md](INSTALLER.md) for a complete reference.

---

## After Installation

### Start the Bridge

```bash
cd /home/hal/.openclaw/workspace/voice-bridge-v4
PYTHONPATH=src python3 -m bridge.main
```

Output:
```
🎙️  Voice Bridge is running!
   Wake word: 'hey jarvis'
   Backend: openwakeword
   Say 'hey jarvis' to start a conversation.
   Press Ctrl+C to stop.
```

### Run Integration Test

```bash
cd /home/hal/.openclaw/workspace/voice-bridge-v4
PYTHONPATH=src python3 tests/test_voice_bridge_integration.py
```

### View Bug Reports

```bash
# Show all bugs
PYTHONPATH=src python3 src/bug_tracker_ui.py

# Show statistics
PYTHONPATH=src python3 src/bug_tracker_ui.py --stats

# Filter by severity
PYTHONPATH=src python3 src/bug_tracker_ui.py --severity critical

# Watch mode (live updates)
PYTHONPATH=src python3 src/bug_tracker_ui.py --watch

# Or use the startup script
./run_bug_tracker_ui.sh
```

---

## Configuration

### Config Location

Config is loaded from the **first file found** in this order:

| Priority | Path | Notes |
|----------|------|-------|
| 1 | `~/.voice-bridge/config.yaml` | Legacy default — existing installs work unchanged |
| 2 | `$XDG_CONFIG_HOME/voice-bridge/config.yaml` | XDG preferred (`~/.config/voice-bridge/` when unset) |
| 3 | `$XDG_CONFIG_HOME/voice-bridge-v2/config.yaml` | Legacy XDG name |

Other files always use `~/.voice-bridge/`:

| File | Path | Description |
|------|------|-------------|
| Bug database | `~/.voice-bridge/bugs.db` | Bug tracking data |
| Data directory | `~/.voice-bridge/data/` | Session data |
| Voices | `~/.voice-bridge/voices/` | Piper TTS voices |

### Default Configuration

```yaml
# ~/.voice-bridge/config.yaml  (or $XDG_CONFIG_HOME/voice-bridge/config.yaml)
wake_word:
  wake_word: "computer"      # phrase to listen for
  backend: "stt"             # "stt" (reliable) or "openwakeword" (fast)

audio:
  sample_rate: 16000
  input_device: null         # null = auto-detect
  output_device: null

stt:
  model: "base"
  language: null             # null = auto-detect

tts:
  voice: "en_US-lessac-medium"
  speed: 1.0

openclaw:
  host: "localhost"
  port: 18789
  auth_token: null           # env vars take priority: OPENCLAW_GATEWAY_TOKEN or OPENCLAW_TOKEN

bridge:
  websocket_server:
    port: 18790              # port clients connect to

persistence:
  ttl_minutes: 30
  cleanup_interval: 60       # must be < ttl_minutes * 60
```

### Environment Variables

| Variable | Description |
|----------|-------------|
| `OPENCLAW_GATEWAY_TOKEN` | OpenClaw authentication token (highest priority) |
| `OPENCLAW_TOKEN` | Alternative token variable (checked after `OPENCLAW_GATEWAY_TOKEN`) |
| `VOICE_BRIDGE_CONFIG` | Custom config file path (overrides search order) |
| `XDG_CONFIG_HOME` | Base directory for user config files (default: `~/.config`) |

> **Token priority:** `OPENCLAW_GATEWAY_TOKEN` → `OPENCLAW_TOKEN` → `auth_token` in config file

---

## Wake Word

Say **"hey jarvis"** to start a conversation.

### Available Wake Words

| Word | Notes |
|------|-------|
| `hey_jarvis` | Default - best accuracy |
| `alexa` | Amazon-style |
| `hey_mycroft` | Mycroft-style |
| `ok_nabu` | Nabu-style |

Change in config:
```yaml
wake_word:
  wake_word: "hey_jarvis"
```

---

## Troubleshooting

### Common Issues

| Issue | Solution |
|-------|----------|
| Missing packages | `pip install pyyaml sounddevice numpy websockets faster-whisper piper-tts openwakeword` |
| No audio devices | Check `cat /proc/asound/cards` |
| Permission denied | `chmod +x run.sh run_bug_tracker_ui.sh` |
| Piper voice missing | Voice downloads automatically on first run |
| STT not working | Install: `pip install faster-whisper` |
| TTS not working | Install: `pip install piper-tts` |
| Wake word not detected | Check microphone levels, try "HEY JARVIS" louder |
| OpenClaw connection failed | Verify OpenClaw is running on port 18789 |
| Rich not installed | Bug UI needs: `pip install rich` |

### PortAudio Installation

```bash
# Ubuntu/Debian
sudo apt install portaudio19-dev

# Fedora
sudo dnf install portaudio-devel

# macOS
brew install portaudio
```

### Check Audio Devices

```bash
# List audio devices
python3 -c "import sounddevice; print(sounddevice.query_devices())"

# Test microphone
python3 -c "import sounddevice; print(sounddevice.query_devices(kind='input'))"

# Test speakers
python3 -c "import sounddevice; print(sounddevice.query_devices(kind='output'))"
```

### Test OpenClaw Connection

```bash
# Check if OpenClaw is running
curl http://localhost:18789/v1/models

# Test with integration test
PYTHONPATH=src python3 tests/test_voice_bridge_integration.py
```

---

## Requirements

### Required

- Python 3.9+
- PortAudio library
- Working microphone
- Working speakers

### Python Packages

```bash
pip install pyyaml sounddevice numpy websockets
pip install faster-whisper piper-tts openwakeword
pip install rich  # For Bug Tracker UI
```

### Optional

- GPU (CUDA) for faster STT/TTS
- Virtual environment for isolation

---

## Project Structure

```
voice-bridge-v4/
├── src/
│   ├── bridge/
│   │   ├── main.py              # Entry point
│   │   ├── orchestrator.py      # State machine
│   │   ├── audio_pipeline.py    # Audio I/O
│   │   ├── wake_word.py         # Detection
│   │   ├── wake_word_oww.py     # OpenWakeWord
│   │   ├── stt.py               # Speech-to-text
│   │   ├── tts.py               # Text-to-speech
│   │   ├── http_client.py       # OpenClaw API
│   │   ├── config.py            # Configuration
│   │   ├── bug_tracker.py       # Bug tracking
│   │   ├── errorcapture.py      # Error capture
│   │   └── ...                  # Other modules
│   ├── installer/
│   │   ├── core.py              # Installer orchestrator (9 steps)
│   │   ├── bridge_test.py       # Real bridge integration test
│   │   └── interactive.py       # Interactive flow
│   └── bug_tracker_ui.py         # Bug Tracker UI
├── tests/
│   └── test_voice_bridge_integration.py
├── run.sh                        # Start bridge
├── run_bug_tracker_ui.sh          # Start Bug UI
├── ARCHITECTURE.md               # Architecture docs
├── INSTALLER.md                  # Installer reference
├── BUG_TRACKER.md                # Bug tracking docs
├── BUGFIX_PLAN.md                # Fix plan
├── BUG_TRACKER_ENHANCEMENT.md    # Enhancement plan
└── QUICKSTART.md                 # This file
```

---

## Getting Help

| Resource | Description |
|----------|-------------|
| `INSTALLER.md` | Full installer reference (steps, flags, diagnostics) |
| `ARCHITECTURE.md` | Detailed system architecture |
| `BUG_TRACKER.md` | Bug tracking documentation |
| `BUGFIX_PLAN.md` | Known issues and fixes |
| `--help` flag | CLI command help |
| Bug Tracker UI | `./run_bug_tracker_ui.sh --stats` |

---

## Quick Reference

```bash
# Start Voice Bridge
PYTHONPATH=src python3 -m bridge.main

# Run installer
PYTHONPATH=src python3 -m installer

# Run integration test  
PYTHONPATH=src python3 tests/test_voice_bridge_integration.py

# View bugs
PYTHONPATH=src python3 src/bug_tracker_ui.py

# View config
cat ~/.voice-bridge/config.yaml

# Check logs
# (Logs to stdout with structlog formatting)
```

---

_Last Updated: 2026-03-14_