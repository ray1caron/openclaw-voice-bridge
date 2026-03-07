# Quick Start: Voice Bridge v4.4.0

**Version:** 4.4.0  
**Last Updated:** 2026-03-06 10:26 PST

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

The installer guides you through setup in 8 steps:

| Step | Description | Interactive? |
|------|-------------|--------------|
| 1 | Previous installations check | Auto |
| 2 | Environment preparation | Auto |
| 3 | Dependencies check | Auto |
| 4 | Audio hardware test | **Yes** - Records & plays audio |
| 5 | Configuration | **Yes** - Shows config summary |
| 6 | Known issues check | Auto |
| 7 | **OpenClaw Integration Test** | **Yes** - Tests WebSocket + HTTP |
| 8 | Installation summary | Auto |

### What Happens in Each Step

#### Step 1: Previous Installations
- Scans for existing Voice Bridge installations
- Offers to clean up old configs/data

#### Step 2: Environment Preparation
- Creates directories: `~/.voice-bridge/`, `~/.voice-bridge/data/`
- Sets up Python path

#### Step 3: Dependencies
- Verifies all Python packages installed
- Lists missing packages with install commands

#### Step 4: Audio Hardware Test
- Records 3 seconds from microphone
- Plays back through speakers
- **Optional:** Speech-to-Text transcription test
- **Optional:** Text-to-Speech playback test
- **Optional:** Wake word acknowledgement test

#### Step 5: Configuration
- Loads config from `~/.voice-bridge/config.yaml`
- Shows configuration summary:
  - Wake word
  - STT model
  - TTS voice
  - Audio device
  - OpenClaw URL
  - **WebSocket port** (default: 18790)

#### Step 6: Known Issues Check
- Scans bug database for unresolved issues
- Shows critical/high bugs
- Offers to continue or abort

#### Step 7: OpenClaw Integration Test 🆕
- Checks if OpenClaw is available
- **WebSocket Server Test:**
  - Starts WebSocket server on port 18790
  - Connects test client
  - Tests ping/pong protocol
  - Verifies message serialization
- **HTTP Integration Test:**
  - Simulates wake word "computer"
  - Sends test message to OpenClaw
  - Displays response text
  - Shows pass/fail status

#### Step 8: Installation Summary
- Lists what was configured
- Shows next steps
- Provides run command

---

## CLI Options

```bash
# Interactive mode (default)
PYTHONPATH=src python3 -m installer

# Automatic mode (no prompts)
PYTHONPATH=src python3 -m installer --auto

# Test audio hardware
PYTHONPATH=src python3 -m installer --test-audio

# Show known bugs
PYTHONPATH=src python3 -m installer --show-bugs

# View config
PYTHONPATH=src python3 -m installer --show-config

# Clean previous install
PYTHONPATH=src python3 -m installer --clean
```

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

| File | Path | Description |
|------|------|-------------|
| Main config | `~/.voice-bridge/config.yaml` | All settings |
| Bug database | `~/.voice-bridge/bugs.db` | Bug tracking data |
| Data directory | `~/.voice-bridge/data/` | Session data |
| Voices | `~/.voice-bridge/voices/` | Piper TTS voices |

### Default Configuration

```yaml
# ~/.voice-bridge/config.yaml
wake_word:
  wake_word: "hey jarvis"
  backend: "openwakeword"
  threshold: 0.5

audio:
  sample_rate: 16000
  input_device: null  # Auto-detect
  output_device: null  # Auto-detect

stt:
  model: "tiny"
  language: "en"

tts:
  voice: "en_US-lessac-medium"
  speed: 1.0

openclaw:
  host: "localhost"
  port: 18789
  secure: false
```

### Environment Variables

| Variable | Description |
|----------|-------------|
| `OPENCLAW_GATEWAY_TOKEN` | OpenClaw authentication token |
| `VOICE_BRIDGE_CONFIG` | Custom config file path |

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
│   │   ├── core.py              # Installer logic
│   │   └── interactive.py       # Interactive flow
│   └── bug_tracker_ui.py         # Bug Tracker UI
├── tests/
│   └── test_voice_bridge_integration.py
├── run.sh                        # Start bridge
├── run_bug_tracker_ui.sh          # Start Bug UI
├── ARCHITECTURE.md               # Architecture docs
├── BUG_TRACKER.md                # Bug tracking docs
├── BUGFIX_PLAN.md                # Fix plan
├── BUG_TRACKER_ENHANCEMENT.md     # Enhancement plan
└── QUICKSTART.md                  # This file
```

---

## Getting Help

| Resource | Description |
|----------|-------------|
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

_Last Updated: 2026-03-05_