#!/usr/bin/env python3
"""
Quick test to verify VAD filter is disabled.
Run this BEFORE starting the voice bridge.
"""
import sys
sys.path.insert(0, 'src')

from bridge.config import get_config, STTConfig
from bridge.stt import STTEngine

print("=" * 60)
print("VAD FILTER CHECK")
print("=" * 60)

# Check config file
config = get_config()
print(f"\nConfig file stt.vad_filter: {config.stt.vad_filter}")

# Check default
default_config = STTConfig()
print(f"STTConfig default vad_filter: {default_config.vad_filter}")

# Create STT engine and check
stt = STTEngine(config.stt)
print(f"STT engine config.vad_filter: {stt.config.vad_filter}")

if stt.config.vad_filter:
    print("\n❌ ERROR: vad_filter is TRUE - Whisper will filter audio!")
    print("   This causes empty transcriptions!")
    print("\n   FIX: Edit ~/.voice-bridge/config.yaml and set:")
    print("   stt:")
    print("     vad_filter: false")
else:
    print("\n✅ vad_filter is FALSE - Whisper will NOT filter audio")

print("\n" + "=" * 60)