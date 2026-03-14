# Voice Bridge Installer Reference

**Last Updated:** 2026-03-14

The installer verifies that every piece of the Voice Bridge stack works on your
machine before you start using it.  It goes beyond a simple dependency check —
Step 7 runs the **real bridge code** and listens to your microphone, so you know
the bridge will work before you commit to using it in production.

---

## Running the Installer

```bash
cd /path/to/voice-bridge
PYTHONPATH=src python3 -m installer            # interactive (recommended)
PYTHONPATH=src python3 -m installer --auto     # non-interactive
PYTHONPATH=src python3 -m installer --debug    # show full diagnostics
```

---

## Installation Steps

The installer runs up to 9 steps.  Steps 1 and 2 are linked — Step 2 only runs
if Step 1 finds something to clean up.

| # | Step | Mode | What it does |
|---|------|------|--------------|
| 1 | Previous install detection | Auto | Scans for old files and running processes |
| 2 | Cleanup | Auto | Stops old processes, removes stale files |
| 3 | Dependencies | Auto | Verifies all required Python packages |
| 4 | Audio hardware | Interactive | Records mic, plays back, tests STT/TTS |
| 5 | Configuration | Interactive | Loads and shows `config.yaml` summary |
| 6 | OpenClaw connectivity | Auto | HTTP check — is OpenClaw reachable? |
| 7 | Bridge integration test | Interactive | Runs real `VoiceOrchestrator` end-to-end |
| 8 | Known issues | Auto | Scans bug database |
| 9 | Summary | Auto | Next steps and run command |

---

## Step Details

### Step 1 — Previous Install Detection

Scans the system for:

- Running `bridge.main` or related processes
- Existing `~/.voice-bridge/` data files
- Any other installation traces

If running processes are found the installer stops here and asks you to shut
them down first.  If only files are found it proceeds to Step 2.

### Step 2 — Cleanup

Only runs when Step 1 found something.

- Sends SIGTERM to any running bridge processes
- Removes or archives stale config/data/socket files
- Leaves `~/.voice-bridge/config.yaml` untouched (your settings are safe)

### Step 3 — Dependencies

Checks that every required Python package can be imported:

| Package | Purpose |
|---------|---------|
| `pyyaml` | Config file parsing |
| `sounddevice` | Audio I/O (requires PortAudio) |
| `numpy` | Audio buffer math |
| `websockets` | WebSocket server |
| `faster-whisper` | Speech-to-text |
| `piper-tts` | Text-to-speech |
| `openwakeword` | Wake-word detection |
| `structlog` | Structured logging |

Any missing package is reported with the exact `pip install` command needed.

If `sounddevice` imports but PortAudio is not installed you will see a
`OSError: PortAudio library not found` error at Step 4.  Fix it with:

```bash
# Ubuntu/Debian
sudo apt install portaudio19-dev

# Fedora
sudo dnf install portaudio-devel

# macOS
brew install portaudio
```

### Step 4 — Audio Hardware Test

Opens the microphone and speakers to confirm audio I/O works before loading
the full bridge stack.

| Sub-test | Required? | What happens |
|----------|-----------|-------------|
| Microphone record | Yes | 3-second recording |
| Speaker playback | Yes | Plays the recording back |
| STT transcription | Optional | Transcribes the recording with Whisper |
| TTS playback | Optional | Synthesises a test phrase with Piper |
| Wake word test | Optional | Full bridge test — same as Step 7 |

If the wake word acknowledgement test is selected from here it delegates to
the same `BridgeTester` used in Step 7.

### Step 5 — Configuration

Reads `config.yaml` from the first location found:

1. `~/.voice-bridge/config.yaml`
2. `$XDG_CONFIG_HOME/voice-bridge/config.yaml`
3. `$XDG_CONFIG_HOME/voice-bridge-v2/config.yaml`

You can override the path with the `VOICE_BRIDGE_CONFIG` environment variable.

Displays a summary of the active settings:

- Wake word phrase and detection backend (`stt` or `openwakeword`)
- STT model name and language
- TTS voice and speed
- Audio input/output device
- OpenClaw host, port, and auth token status
- WebSocket server port (default 18790)

No changes are made to the config file during this step.

### Step 6 — OpenClaw Connectivity

Makes a single HTTP request to the configured OpenClaw endpoint to confirm it
is reachable.

- Pass: OpenClaw responded — wake word → ACK → TTS flow will work
- Fail (advisory): OpenClaw is not running or not reachable

Step 6 failure does **not** block Step 7.  The bridge integration test still
runs; it will report whether OpenClaw ACK succeeds or times out as a separate
result field.

### Step 7 — Bridge Integration Test

This is the most important step.  It runs the **real `VoiceOrchestrator`** — the
same object that runs when you start the bridge normally — against your actual
hardware and config.

#### Why this matters

Earlier versions of the installer tested individual components in isolation
(raw `sounddevice` input streams, `sd.play()` for output).  Those tests could
pass while the bridge itself failed because they bypassed:

- The audio pipeline state machine
- The FIFO output queue (previously a ring buffer that caused looping noise)
- Wake word echo suppression
- The interaction between VAD, STT, and the orchestrator state machine

Step 7 catches all of those problems.

#### What it does

**Startup test (automatic — no interaction needed)**

1. Loads `config.yaml`
2. Creates a `VoiceOrchestrator` instance
3. Calls `orchestrator.start()` — opens audio devices, loads STT/TTS/wake-word models
4. Waits 1.5 seconds for the audio pipeline to settle
5. Calls `orchestrator.stop()`

Pass means: all four components (audio, STT, TTS, wake-word detector) initialised
without error.

**Wake word test (interactive)**

After the startup test passes, the installer prompts you to say the wake word.

1. Bridge listens on the microphone (up to 20 seconds)
2. When wake word is detected, the orchestrator transitions to `WAKE_WORD_ACK` state
3. An HTTP ACK is sent to OpenClaw
4. If OpenClaw responds, TTS plays the response audio through the speakers
5. The orchestrator returns to `INTERACTIVE` / `LISTENING` state

**Result fields shown:**

```
  Bridge startup   : ✅ OK
    ✅ audio_pipeline: listening
    ✅ stt: loaded
    ✅ tts: loaded
    ✅ wake_word_detector: running
  Wake word        : ✅ 'computer'
  OpenClaw ACK     : ✅ responded
  TTS playback     : ✅ played
  Duration         : 8423ms
```

#### Wake word timeout

If the bridge starts but the wake word is not detected within 20 seconds the
installer shows audio pipeline stats to help diagnose the problem:

```
  Audio stats:
    frames_processed: 0          ← microphone not delivering frames (device error)
    frames_processed: 14200      ← mic working, wake word just not triggered
    speech_segments_detected: 0  ← VAD heard nothing (too quiet, or wrong device)
```

A `frames_processed` count of 0 means the microphone is open but no audio is
arriving — check the device name in `config.yaml` or use `--test-audio` to
identify the correct device index.

#### OpenClaw ACK timeout

If the wake word is detected but OpenClaw does not respond within the configured
timeout (default 5 seconds), the test is marked advisory-pass:

```
  OpenClaw ACK     : ⏰ timed out (advisory)
```

The bridge itself is functional — it detected the wake word and attempted the
ACK.  Start OpenClaw and re-run `python -m installer` (or just start the bridge
directly) to complete the full flow.

#### What is captured on failure

When the bridge fails during the installer you will see:

| Output | Shown when |
|--------|-----------|
| Per-component status | Always on startup failure |
| Full Python traceback | Any exception |
| All bridge log lines | Any failure (last 60 lines) |
| Audio pipeline stats | Wake word timeout |
| New bug-tracker IDs | Any time new bugs are written |

Use `--debug` to see the full log output even when the test passes.

### Step 8 — Known Issues Check

Queries the SQLite bug database at `~/.voice-bridge/bugs.db`.

- Critical and high-severity bugs are shown with title and component
- Medium/low bugs are summarised by count
- You can choose to continue or abort

View full details at any time with:
```bash
PYTHONPATH=src python3 -m installer --show-bugs
```

### Step 9 — Installation Summary

Shows what was found and configured, any warnings from earlier steps, and the
command to start the bridge.

---

## CLI Reference

```
PYTHONPATH=src python3 -m installer [OPTIONS]

Options:
  --interactive       Run with full interactive prompts (default)
  --auto              Run automatically without prompts
  --debug             Show full debug output during bridge test
                      (traceback, all bridge log lines, component status)
                      Implies --verbose
  --verbose, -v       Show detailed output
  --test-audio        Run audio hardware tests only (no full install)
  --show-bugs         Show known bugs from the tracker and exit
  --show-config       Show the active configuration file and exit
  --skip-hardware     Skip hardware validation (Step 4)
  --clean             Clean up a previous installation and exit
  --workspace PATH    Workspace directory (default: current directory)
  --help              Show this message
```

### --debug flag

Add `--debug` any time you want the installer to print the complete bridge
diagnostic output regardless of whether the test passes or fails:

```bash
PYTHONPATH=src python3 -m installer --debug
```

This shows:
- Full Python traceback if an exception occurred
- Every log line emitted by the bridge during the test
- Per-component initialisation status
- Audio pipeline statistics
- Any bug-tracker entries written during the test

This is the recommended flag to use when reporting a bug or asking for help.

---

## Diagnostic Output Reference

When the bridge test fails the installer prints structured diagnostic sections.

### Component status

```
    ✅ audio_pipeline: listening
    ❌ stt: not loaded
    ✅ tts: loaded
    ✅ wake_word_detector: running
```

Each component has one of these states:

| Component | States |
|-----------|--------|
| `audio_pipeline` | `listening`, `speaking`, `idle`, `error`, `probe error: …` |
| `stt` | `loaded`, `not loaded`, `probe error: …` |
| `tts` | `loaded`, `not loaded`, `probe error: …` |
| `wake_word_detector` | `running`, `stopped`, `probe error: …` |

### Audio stats

```
  Audio stats:
    frames_processed: 0
    speech_segments_detected: 0
    queue_overflows: 0
    pipeline_state: listening
```

| Field | Meaning |
|-------|---------|
| `frames_processed` | Total 1024-sample chunks delivered by the microphone callback. 0 = mic not producing audio. |
| `speech_segments_detected` | Times VAD detected speech above threshold. 0 after talking = VAD not triggering (check sensitivity in config). |
| `queue_overflows` | Times the output queue was full when TTS tried to enqueue a frame. Should be 0. |
| `pipeline_state` | State of the audio pipeline at test end. |

### Traceback

```
  --- Traceback ---
  Traceback (most recent call last):
    File "…/bridge/orchestrator.py", line 142, in start
      self.audio_pipeline.start()
  OSError: [Errno -9996] Invalid input device (no default output device)
```

### Bridge log

```
  --- Bridge log (87 lines) ---
  10:23:01 [INFO ] bridge.orchestrator: orchestrator_starting
  10:23:01 [INFO ] bridge.audio_pipeline: audio_pipeline_starting
  10:23:01 [ERROR] bridge.audio_pipeline: audio_device_open_failed device=null
  ...
```

---

## Troubleshooting

### Bridge test fails at startup

| Symptom | Likely cause | Fix |
|---------|-------------|-----|
| `audio_pipeline: probe error` | PortAudio not installed | `sudo apt install portaudio19-dev` |
| `stt: not loaded` | faster-whisper not installed or model missing | `pip install faster-whisper` |
| `tts: not loaded` | piper-tts not installed or voice missing | `pip install piper-tts` |
| `wake_word_detector: stopped` | openwakeword model not found | `pip install openwakeword` |
| `frames_processed: 0` | Audio device not found | Check `audio.input_device` in config |

### Wake word not detected

1. Run `--test-audio` first to verify the microphone records clearly
2. Check `speech_segments_detected` — if 0, VAD is not triggering:
   - Speak louder or move closer to the mic
   - Lower `audio.vad_sensitivity` in `config.yaml`
3. If using the `stt` backend for wake words, check that STT loaded (`stt: loaded`)
4. If using `openwakeword`, verify the model name matches your wake word phrase

### OpenClaw ACK times out

- Confirm OpenClaw is running: `curl http://localhost:18789/v1/models`
- Check `openclaw.host` and `openclaw.port` in `config.yaml`
- Check auth token: `OPENCLAW_GATEWAY_TOKEN` environment variable or `openclaw.auth_token` in config

### Loud noise / looping audio

Fixed in v4.4.0 (audio output path changed from ring buffer to FIFO queue).
If you see this on a clean install, run `--show-bugs` to check for related entries.

---

## Config File Reference

```yaml
# ~/.voice-bridge/config.yaml

wake_word:
  wake_word: "computer"        # phrase to listen for
  backend: "stt"               # "stt" or "openwakeword"

audio:
  sample_rate: 16000
  input_device: null           # null = system default; or device index/name
  output_device: null
  vad_sensitivity: 3           # 1 (permissive) – 3 (strict)

stt:
  model: "base"                # tiny / base / small / medium / large
  language: null               # null = auto-detect

tts:
  voice: "en_US-lessac-medium"
  speed: 1.0

openclaw:
  host: "localhost"
  port: 18789
  auth_token: null             # or set OPENCLAW_GATEWAY_TOKEN env var

bridge:
  websocket_server:
    port: 18790

persistence:
  ttl_minutes: 30
  cleanup_interval: 60
```

### Environment variables

| Variable | Description |
|----------|-------------|
| `OPENCLAW_GATEWAY_TOKEN` | Auth token (highest priority) |
| `OPENCLAW_TOKEN` | Alt auth token variable |
| `VOICE_BRIDGE_CONFIG` | Override config file path |
| `XDG_CONFIG_HOME` | Base dir for XDG config paths (default `~/.config`) |

---

## Data Directories

| Path | Contents |
|------|----------|
| `~/.voice-bridge/config.yaml` | Main configuration |
| `~/.voice-bridge/bugs.db` | SQLite bug database |
| `~/.voice-bridge/data/` | Session and persistence data |
| `~/.voice-bridge/voices/` | Downloaded Piper TTS voice files |

---

_See also: [QUICKSTART.md](QUICKSTART.md) · [ARCHITECTURE.md](ARCHITECTURE.md) · [BUG_TRACKER.md](BUG_TRACKER.md)_
