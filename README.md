# Voice-OpenClaw Bridge v4.4.0

A bidirectional voice interface where OpenClaw is the brain, voice is the I/O layer.

**Last Updated:** 2026-03-06 10:26 PST

## Features

- **Wake Word Detection** - STT-based detection (default: `"computer"`)
- **Speech-to-Text** - Faster-Whisper with CUDA acceleration
- **Text-to-Speech** - Piper TTS with natural voices
- **Audio Pipeline** - Full duplex audio with VAD and barge-in
- **WebSocket Server** - Real-time bidirectional communication (port 18790)
- **OpenClaw Channel Extension** - TypeScript extension for OpenClaw Gateway
- **Protocol Support** - 14 message types for rich interaction
- **Session Persistence** - SQLite-based conversation history
- **Bug Tracking** - Integrated error capture and reporting

## Architecture

```
Microphone → Audio Pipeline → Wake Word Detector
                                    │
                                    ▼
                            STT Engine (Whisper)
                                    │
                                    ▼
                            WebSocket Server (18790)
                                    │
                                    │ WebSocket
                                    ▼
                            OpenClaw Gateway
                                    │
                                    │ Response
                                    ▼
                            TTS Engine (Piper)
                                    │
                                    ▼
                            Speaker Output
```

## Quick Start

```bash
# Install
./install.sh

# Run
PYTHONPATH=src python3 -m bridge.main
```

## Configuration

Config file: `~/.voice-bridge/config.yaml`

```yaml
bridge:
  wake_word: "hey hal"
  
audio:
  input_device: 10    # Device index or name
  output_device: 10
  sample_rate: 44100
  
stt:
  model: base
  device: auto
  
tts:
  voice: en_US-lessac-medium
  
openclaw:
  host: localhost
  port: 8080
```

## Components

| Component | File | Purpose |
|-----------|------|---------|
| Wake Word | `wake_word.py` | Phrase detection with WebRTC VAD |
| STT | `stt.py` | Faster-Whisper speech recognition |
| TTS | `tts.py` | Piper voice synthesis |
| Orchestrator | `orchestrator.py` | State machine for voice loop |
| Audio Pipeline | `audio_pipeline.py` | Device management, VAD, barge-in |
| WebSocket | `websocket_client.py` | OpenClaw communication |

## Installation UI

Run the interactive installer:

```bash
./install.sh
```

Features:
- Detects previous installations
- Tests microphone and speakers (with playback)
- Shows configuration summary
- Displays known bugs
- Validates dependencies

## Dependencies

```bash
pip install faster-whisper piper-tts sounddevice numpy webrtcvad websockets pydantic pyyaml
```

## Project Structure

```
voice-bridge-v4/
├── src/
│   ├── bridge/
│   │   ├── main.py              # Entry point
│   │   ├── config.py            # Configuration
│   │   ├── wake_word.py         # Wake word detection
│   │   ├── stt.py               # Speech-to-text
│   │   ├── tts.py               # Text-to-speech
│   │   ├── orchestrator.py      # Voice loop
│   │   ├── audio_pipeline.py    # Audio I/O
│   │   ├── websocket_client.py  # OpenClaw connection
│   │   └── ...
│   └── installer/
│       ├── interactive.py       # Installation wizard
│       ├── detector.py          # Installation detection
│       ├── hardware_test.py     # Audio testing
│       └── ...
├── tests/
├── install.sh
└── requirements.txt
```

## Status

- ✅ Installation UI - Complete (30 tests passing)
- ✅ Wake Word - Implemented
- ✅ STT (Whisper) - Implemented
- ✅ TTS (Piper) - Implemented
- ✅ Orchestrator - Implemented
- ✅ Audio Pipeline - Working
- ✅ WebSocket Client - Configured

## License

MIT