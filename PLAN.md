# Implementation Plan: Hybrid Wake Word Detection (Option C)

## Overview

Replace the current blocking STT-based wake word detection with a two-stage hybrid approach:
1. **OpenWakeWord** for fast (~5ms) wake word detection ("hey jarvis")
2. **Faster-Whisper** for command understanding (after wake word triggered)

This follows the production-tested pattern used by Home Assistant's Wyoming Satellite.

---

## Architecture Changes

### Current (Broken) Flow
```
Audio Callback (PortAudio thread)
  └─▶ VAD process
       └─▶ Wake word buffer fills
            └─▶ stt_engine.transcribe()  ← BLOCKS 200-2000ms
                                            ← Input overflow!
                                            ← Dropped frames!
```

### New (Fixed) Flow
```
Audio Callback (PortAudio thread - MUST return in <10ms)
  └─▶ Copy frame to queue
  └─▶ Return immediately

Worker Thread (can block)
  └─▶ Pull frames from queue
       └─▶ OpenWakeWord.process(frame)  ← ~5ms
            └─▶ If wake word detected:
                 └─▶ Switch to LISTENING state
                 └─▶ Buffer command audio
                 └─▶ On speech end:
                      └─▶ Faster-Whisper.transcribe()  ← Now OK to block!
                      └─▶ Send to OpenClaw
```

---

## Implementation Tasks

### Task 1: Add OpenWakeWord Dependency

**Files:** `pyproject.toml` or `requirements.txt`, `src/bridge/config.py`

**Changes:**
- Add `openwakeword` package dependency
- Add wake word configuration:
  - `wake_word_backend: "openwakeword"` (vs "stt")
  - `openwakeword_model: "hey_jarvis"` (or custom path)
  - `openwakeword_threshold: 0.5` (detection threshold)
  - `refractory_seconds: 2.0` (cooldown after detection)

**Verification:**
```bash
pip install openwakeword
python3 -c "import openwakeword; print('OK')"
```

---

### Task 2: Create OpenWakeWord Detector Module

**File:** `src/bridge/wake_word_oww.py` (new file)

**Implementation:**
```python
"""OpenWakeWord-based wake word detector."""
import numpy as np
from openwakeword import Model

class OpenWakeWordDetector:
    def __init__(self, model_name: str = "hey_jarvis", threshold: float = 0.5):
        self.model = Model()
        self.model_name = model_name
        self.threshold = threshold
        self._last_detection_time = 0
        self._refractory_seconds = 2.0
    
    def process_frame(self, audio_frame: np.ndarray) -> bool:
        """Process a single audio frame. Returns True if wake word detected.
        
        This runs in <5ms per frame - safe for real-time processing.
        """
        # Check refractory period
        if time.time() - self._last_detection_time < self._refractory_seconds:
            return False
        
        # OpenWakeWord processing (~5ms)
        prediction = self.model(audio_frame)
        score = prediction.get(self.model_name, 0)
        
        if score >= self.threshold:
            self._last_detection_time = time.time()
            return True
        return False
    
    def is_available(self) -> bool:
        return self.model is not None
```

**Key points:**
- `process_frame()` runs in ~5ms - safe to call frequently
- Model runs ON the frame, not on buffered audio
- Refractory period prevents repeated detections

---

### Task 3: Refactor Audio Pipeline to Queue-Based Pattern

**File:** `src/bridge/audio_pipeline.py`

**Changes:**
1. Add thread-safe queue for audio frames
2. Audio callback only copies to queue (returns in <1ms)
3. Worker thread pulls from queue and processes

```python
import queue
import threading

class AudioPipeline:
    def __init__(self, ...):
        # ... existing init ...
        self._audio_queue = queue.Queue(maxsize=100)  # ~6 seconds at 16kHz
        self._worker_thread = None
        self._stop_event = threading.Event()
        self._frame_callbacks = []  # Called in worker thread
    
    def _audio_input_callback(self, indata, frames, time_info, status):
        """Audio callback - runs in PortAudio thread. MUST be fast."""
        if status:
            logger.warning("audio_input_status", status=str(status))
        
        # FAST: Just copy to queue (returns in microseconds)
        try:
            self._audio_queue.put_nowait(bytes(indata))
        except queue.Full:
            logger.warning("audio_queue_full")  # Queue overflow - worker too slow
        # Return immediately!
    
    def _worker_loop(self):
        """Worker thread - processes audio frames. Can block here."""
        while not self._stop_event.is_set():
            try:
                audio_data = self._audio_queue.get(timeout=0.1)
            except queue.Empty:
                continue
            
            # All processing happens HERE (safe to block)
            for callback in self._frame_callbacks:
                try:
                    callback(audio_data)
                except Exception as e:
                    logger.error("callback_error", error=str(e))
    
    def start_capture(self):
        """Start audio capture."""
        self._stop_event.clear()
        self._worker_thread = threading.Thread(target=self._worker_loop, daemon=True)
        self._worker_thread.start()
        
        self._stream = sd.RawInputStream(
            samplerate=self.audio_config.sample_rate,
            channels=1,
            dtype='int16',
            callback=self._audio_input_callback,
            # ... rest of config ...
        )
        self._stream.start()
```

**Key points:**
- Callback is ~100x faster (microseconds vs milliseconds)
- Worker thread can safely call blocking operations
- Queue provides buffering for brief processing spikes

---

### Task 4: Update Wake Word Detector to Use Queue-Based Pattern

**File:** `src/bridge/wake_word.py`

**Changes:**
1. Remove STT-based transcribe() from callback path
2. Integrate OpenWakeWord detector
3. Keep STT for command understanding (after wake word detected)

```python
class WakeWordDetector:
    def __init__(self, config, stt_engine=None, openwakeword_model="hey_jarvis"):
        self.config = config
        self.stt_engine = stt_engine  # For command understanding, NOT wake word
        
        # OpenWakeWord for fast wake word detection
        if config.wake_word_backend == "openwakeword":
            from .wake_word_oww import OpenWakeWordDetector
            self._detector = OpenWakeWordDetector(
                model_name=openwakeword_model,
                threshold=config.openwakeword_threshold
            )
        else:
            raise ValueError(f"Unknown wake word backend: {config.wake_word_backend}")
        
        self._on_wake_word_callback = None
    
    def process_frame(self, audio_data: bytes) -> bool:
        """Process audio frame. Called from worker thread.
        
        Returns True if wake word detected.
        This uses OpenWakeWord (~5ms), NOT STT.
        """
        # Convert bytes to numpy array
        audio_np = np.frombuffer(audio_data, dtype=np.int16)
        
        # Fast wake word detection
        if self._detector.process_frame(audio_np):
            logger.info("wake_word_detected", word=self.wake_word)
            if self._on_wake_word_callback:
                self._on_wake_word_callback(self.wake_word)
            return True
        return False
```

---

### Task 5: Update Orchestrator State Machine

**File:** `src/bridge/orchestrator.py`

**Changes:**
1. States: IDLE → WAKE_WORD_LISTENING → COMMAND_LISTENING → PROCESSING
2. Use OpenWakeWord for wake word detection
3. Use STT only for command transcription

```python
class VoiceOrchestrator:
    def __init__(self, ...):
        # ... existing init ...
        self._state = OrchestratorState.IDLE
        self._command_buffer = []  # Audio buffer for command
    
    def _on_audio_frame(self, audio_data: bytes):
        """Called by audio pipeline worker thread."""
        if self.state == OrchestratorState.IDLE:
            # Check for wake word (OpenWakeWord, ~5ms)
            if self.wake_word_detector.process_frame(audio_data):
                self._transition_to(OrchestratorState.COMMAND_LISTENING)
                self._command_buffer = []
        
        elif self.state == OrchestratorState.COMMAND_LISTENING:
            # Buffer command audio
            self._command_buffer.append(audio_data)
            
            # Check for speech end (VAD)
            if self.vad.is_silence(audio_data):
                self._silence_frames += 1
                if self._silence_frames > SILENCE_THRESHOLD:
                    # Command complete - transcribe (can block here!)
                    self._transition_to(OrchestratorState.PROCESSING)
                    self._transcribe_and_respond()
            else:
                self._silence_frames = 0
    
    def _transcribe_and_respond(self):
        """Transcribe command and get response. Runs in worker thread."""
        # Concatenate buffered audio
        command_audio = b''.join(self._command_buffer)
        
        # STT transcription (can block - we're in worker thread)
        transcript = self.stt_engine.transcribe(command_audio)
        
        # Send to OpenClaw
        response = self.http_client.send_transcript(transcript)
        
        # TTS response
        self._play_response(response)
        
        # Return to idle
        self._transition_to(OrchestratorState.IDLE)
```

---

### Task 6: Update Configuration

**File:** `src/bridge/config.py`

**Add new config options:**
```python
class WakeWordConfig(BaseModel):
    wake_word: str = "hey jarvis"
    backend: str = "openwakeword"  # "openwakeword" or "stt"
    openwakeword_model: str = "hey_jarvis"
    openwakeword_threshold: float = 0.5
    refractory_seconds: float = 2.0
```

---

### Task 7: Add Installation Script for OpenWakeWord

**File:** `install.sh` (update)

**Add:**
```bash
# Install OpenWakeWord
echo "Installing OpenWakeWord..."
pip install openwakeword

# Download pre-trained models (optional, downloads on first use)
python3 -c "from openwakeword import Model; Model()"
```

---

## File Changes Summary

| File | Action | Description |
|------|--------|-------------|
| `pyproject.toml` | Modify | Add `openwakeword` dependency |
| `src/bridge/config.py` | Modify | Add wake word config options |
| `src/bridge/wake_word_oww.py` | **Create** | OpenWakeWord detector class |
| `src/bridge/audio_pipeline.py` | Modify | Queue-based audio processing |
| `src/bridge/wake_word.py` | Modify | Use OpenWakeWord + queue pattern |
| `src/bridge/orchestrator.py` | Modify | Update state machine |
| `install.sh` | Modify | Add OpenWakeWord installation |

---

## Testing Plan

1. **Unit Test: OpenWakeWord Detector**
   - Test frame processing speed (<10ms)
   - Test detection threshold
   - Test refractory period

2. **Unit Test: Audio Queue**
   - Test queue fill/drain
   - Test overflow handling
   - Test thread safety

3. **Integration Test: Full Pipeline**
   - Play "hey jarvis" audio → should trigger wake word
   - Speak command → should transcribe
   - Verify response plays

4. **Performance Test: Latency**
   - Measure audio callback duration (should be <1ms)
   - Measure wake word detection latency
   - Measure end-to-end response time

---

## Rollback Plan

If issues arise:
1. Revert to `stt` backend in config: `wake_word_backend: "stt"`
2. The queue-based architecture still helps (prevents overflow)
3. Can disable wake word detection entirely and use push-to-talk

---

## Success Criteria

- [ ] Audio callback returns in <1ms (no overflow warnings)
- [ ] Wake word "hey jarvis" detected with >90% accuracy
- [ ] End-to-end latency <2 seconds from wake word to response
- [ ] No dropped frames during wake word detection
- [ ] System stable for >10 minutes of continuous operation