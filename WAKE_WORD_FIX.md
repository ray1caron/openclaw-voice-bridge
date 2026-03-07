# Wake Word Detection Issue - Fix Applied

**Date:** 2026-03-06 13:22 PST
**Issue:** Voice bridge not responding to wake word
**Root Cause:** Whisper's `vad_filter=True` was filtering out audio as "no speech"
**Fix:** Set `vad_filter=False` in STT config (we use WebRTC VAD for speech detection)

## Root Cause Analysis

1. **Architecture:** We use WebRTC VAD to detect speech start/end
2. **Issue:** Whisper's internal VAD was also running (`vad_filter=True`)
3. **Conflict:** When Whisper receives low-energy audio (silence/noise), its VAD filters it out
4. **Result:** Empty transcription even when we know speech is present

## Changes Made

### 1. `src/bridge/config.py`
- Changed `vad_filter` default from `True` to `False`
- Added comment explaining we use WebRTC VAD

### 2. `~/.voice-bridge/config.yaml`
- Set `vad_filter: false`

## Why This Fixes It

- We already detect speech with WebRTC VAD (` vad.py`)
- Only sending audio to Whisper when speech is detected
- Whisper doesn't need to filter again - it would only reject valid speech
- Disabling Whisper's VAD allows all buffered speech to be transcribed

## Test Required

Run voice bridge and speak wake word "computer":

```bash
cd /home/hal/.openclaw/workspace/voice-bridge-v4
PYTHONPATH=src python3 -m bridge.main
```

## Other Findings

- Audio buffering logic is correct in `wake_word.py`
- VAD frame size handling is correct in `vad.py` (splits 1280-frame into 480-frame chunks)
- The issue was purely Whisper's VAD rejecting audio we knew contained speech

## References

- Faster-Whisper VAD: Can filter aggressively
- WebRTC VAD: We control aggressiveness (set to MEDIUM)
- Double VAD = speech gets lost