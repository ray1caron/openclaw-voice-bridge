# Voice-OpenClaw Bridge

A voice I/O layer for OpenClaw. Detects a wake word, transcribes speech,
sends it to OpenClaw via HTTP, speaks the response, and keeps the conversation
going in interactive mode until the user says a cancel phrase or goes silent.

## How it works

```
Microphone
    │
    ▼
Audio Pipeline (WebRTC VAD)
    │
    ├─── Wake Word Detector (STT or OpenWakeWord)
    │         │
    │         ▼  wake word detected
    │    [WAKE_WORD_ACK] ──HTTP──► OpenClaw
    │         │                        │ ack phrase
    │         ▼                        │
    │    [INTERACTIVE] ◄───────────────┘
    │         │
    │         │  user speaks
    │         ▼
    │    STT (Faster-Whisper)
    │         │ text
    │         ▼
    │    OpenClaw  (HTTP /v1/chat/completions)
    │         │ response
    │         ▼
    │    TTS (Piper) → Speaker
    │         │
    │         └─► back to [INTERACTIVE]
    │
    └─── (cancel phrase / idle timeout → back to wake word detection)
```

## State machine

| State | Description |
|-------|-------------|
| `listening_for_wake_word` | Continuously scanning audio for the wake word |
| `wake_word_ack` | Wake word detected; waiting for OpenClaw's acknowledgement |
| `interactive` | Active conversation — user speaks, OpenClaw responds, repeat |
| `processing` | Speech transcribed; waiting for OpenClaw HTTP response |
| `speaking` | Playing OpenClaw's response via TTS |
| `error` | Unrecoverable error; check logs |

## Quick start

```bash
# Install dependencies
pip install -e .

# Copy and edit config
cp config.yaml ~/.voice-bridge/config.yaml
$EDITOR ~/.voice-bridge/config.yaml

# Run
PYTHONPATH=src python3 -m bridge.main
```

Or use the interactive installer:

```bash
PYTHONPATH=src python3 -m installer
```

## Configuration

`~/.voice-bridge/config.yaml` — copy from `config.yaml` in this repo.

Key settings:

```yaml
openclaw:
  host: "localhost"
  port: 18789
  api_mode: "http"          # http (default) or websocket

wake_word:
  wake_word: "computer"
  backend: "stt"            # stt (reliable) or openwakeword (fast)

bridge:
  acknowledgement:
    enabled: true
    timeout_ms: 5000        # wait this long for OpenClaw's ack reply

  interactive:
    enabled: true
    idle_timeout_seconds: 30.0   # exit after 30 s of silence
    cancel_phrases:
      - "stop"
      - "cancel"
      - "goodbye"
```

## Interactive mode

After the wake word is acknowledged the bridge enters **interactive mode**:
the user can keep speaking without repeating the wake word. OpenClaw responds
to each turn. The session ends when:

- The user says a **cancel phrase** (configurable, default: stop / cancel /
  nevermind / never mind / exit / goodbye / bye)
- The user is **silent for `idle_timeout_seconds`** (default 30 s)

## Debugging

Every state transition, interactive mode entry/exit, HTTP timeout, and cancel
phrase is recorded to `~/.voice-bridge/bugs.db` (SQLite, `events` table).

```python
from bridge.bug_tracker import BugTracker
tracker = BugTracker.get_instance()

# Last 50 state transitions
for e in tracker.get_state_history(limit=50):
    print(e["timestamp"], e["from_state"], "→", e["to_state"],
          f"({e['duration_ms']:.0f} ms)")

# All interactive mode events
for e in tracker.get_recent_events(event_type="interactive_exit"):
    print(e["timestamp"], e["trigger"])
```

Exceptions are stored in the `bugs` table and printed to stderr at HIGH/CRITICAL
severity.

## Components

| File | Purpose |
|------|---------|
| `src/bridge/orchestrator.py` | State machine; coordinates all components |
| `src/bridge/audio_pipeline.py` | Microphone capture, VAD, TTS playback |
| `src/bridge/wake_word.py` | Wake word detection (STT or OpenWakeWord backend) |
| `src/bridge/stt.py` | Faster-Whisper speech-to-text |
| `src/bridge/tts.py` | Piper text-to-speech |
| `src/bridge/http_client.py` | OpenClaw HTTP API client |
| `src/bridge/websocket_client.py` | OpenClaw WebSocket client (legacy) |
| `src/bridge/websocket_server.py` | Inbound WebSocket server (port 18790) |
| `src/bridge/config.py` | Pydantic configuration models |
| `src/bridge/bug_tracker.py` | Error capture + diagnostic event recording |
| `src/bridge/database.py` | Thread-safe SQLite connection manager |

## Requirements

- Python 3.11+
- `faster-whisper` — STT
- `piper-tts` — TTS
- `sounddevice` — audio I/O
- `webrtcvad` — voice activity detection
- `aiohttp` — HTTP client
- `websockets` — WebSocket support
- `pydantic`, `pydantic-settings` — configuration
- `structlog` — structured logging

Optional:
- `openwakeword` — fast hardware-accelerated wake word detection
- `psutil` — system info in bug reports
