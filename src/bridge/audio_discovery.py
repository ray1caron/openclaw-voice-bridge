"""Audio device discovery and configuration.

Automatically discovers audio input/output devices and provides
recommendations based on device names and capabilities.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import List, Optional

import sounddevice as sd
import structlog

logger = structlog.get_logger()


@dataclass
class AudioDevice:
    """Represents an audio device."""
    index: int
    name: str
    channels: int
    sample_rate: float
    is_input: bool
    is_output: bool
    is_default: bool = False
    
    def __str__(self) -> str:
        direction = []
        if self.is_input:
            direction.append("input")
        if self.is_output:
            direction.append("output")
        dir_str = "/".join(direction) if direction else "unknown"
        default_mark = " [DEFAULT]" if self.is_default else ""
        return f"[{self.index}] {self.name} ({dir_str}){default_mark}"


class AudioDiscovery:
    """Discovers and recommends audio devices."""
    
    # Common microphone name patterns (case-insensitive)
    MIC_PATTERNS = [
        "microphone", "mic", "input", "audio input",
        "blue yeti", "snowball", "rode", "shure",
        "usb audio", "analog input", "line in",
    ]
    
    # Common speaker/headphone patterns
    SPEAKER_PATTERNS = [
        "speaker", "output", "audio output", "headphone",
        "headset", "earphone", "playback", "line out",
        "hdmi", "usb audio", "analog output",
    ]
    
    def __init__(self):
        self.devices: List[AudioDevice] = []
        self.default_input: Optional[AudioDevice] = None
        self.default_output: Optional[AudioDevice] = None
        
    def discover(self) -> AudioDiscovery:
        """Discover all audio devices."""
        logger.info("Discovering audio devices...")
        self.devices = []
        
        try:
            device_list = sd.query_devices()
            default_input_idx = sd.default.device[0]
            default_output_idx = sd.default.device[1]
            
            for i, dev_info in enumerate(device_list):
                is_input = dev_info.get("max_input_channels", 0) > 0
                is_output = dev_info.get("max_output_channels", 0) > 0
                
                if not is_input and not is_output:
                    continue
                
                device = AudioDevice(
                    index=i,
                    name=dev_info.get("name", f"Device {i}"),
                    channels=max(
                        dev_info.get("max_input_channels", 0),
                        dev_info.get("max_output_channels", 0)
                    ),
                    sample_rate=dev_info.get("default_samplerate", 44100),
                    is_input=is_input,
                    is_output=is_output,
                    is_default=(i == default_input_idx or i == default_output_idx)
                )
                
                self.devices.append(device)
                
                if i == default_input_idx:
                    self.default_input = device
                if i == default_output_idx:
                    self.default_output = device
            
            logger.info(
                f"Discovered {len(self.devices)} audio devices",
                input_devices=sum(1 for d in self.devices if d.is_input),
                output_devices=sum(1 for d in self.devices if d.is_output),
            )
            
        except Exception as e:
            logger.error("Failed to discover audio devices", error=str(e))
            raise
        
        return self
    
    def recommend_input(self) -> Optional[AudioDevice]:
        """Recommend best input device based on heuristics."""
        if not self.devices:
            return None
        
        # First, check for known good microphone patterns
        for pattern in self.MIC_PATTERNS:
            for device in self.devices:
                if device.is_input and pattern.lower() in device.name.lower():
                    logger.info(
                        "Recommended input device (pattern match)",
                        device=device.name,
                        pattern=pattern,
                    )
                    return device
        
        # Fall back to default input
        if self.default_input:
            logger.info(
                "Recommended input device (system default)",
                device=self.default_input.name,
            )
            return self.default_input
        
        # Last resort: first input device
        for device in self.devices:
            if device.is_input:
                logger.info(
                    "Recommended input device (first available)",
                    device=device.name,
                )
                return device
        
        return None
    
    def recommend_output(self) -> Optional[AudioDevice]:
        """Recommend best output device based on heuristics."""
        if not self.devices:
            return None
        
        # Check for known good output patterns
        for pattern in self.SPEAKER_PATTERNS:
            for device in self.devices:
                if device.is_output and pattern.lower() in device.name.lower():
                    logger.info(
                        "Recommended output device (pattern match)",
                        device=device.name,
                        pattern=pattern,
                    )
                    return device
        
        # Fall back to default output
        if self.default_output:
            logger.info(
                "Recommended output device (system default)",
                device=self.default_output.name,
            )
            return self.default_output
        
        # Last resort: first output device
        for device in self.devices:
            if device.is_output:
                logger.info(
                    "Recommended output device (first available)",
                    device=device.name,
                )
                return device
        
        return None
    
    def generate_report(self) -> dict:
        """Generate a discovery report for the user."""
        input_dev = self.recommend_input()
        output_dev = self.recommend_output()
        
        return {
            "total_devices": len(self.devices),
            "input_devices": [
                {"index": d.index, "name": d.name, "is_default": d.is_default}
                for d in self.devices if d.is_input
            ],
            "output_devices": [
                {"index": d.index, "name": d.name, "is_default": d.is_default}
                for d in self.devices if d.is_output
            ],
            "recommended_input": {
                "index": input_dev.index if input_dev else None,
                "name": input_dev.name if input_dev else None,
            },
            "recommended_output": {
                "index": output_dev.index if output_dev else None,
                "name": output_dev.name if output_dev else None,
            },
        }


def run_discovery() -> AudioDiscovery:
    """Run audio discovery and return results.
    
    This is called automatically on first start to detect audio devices.
    """
    discovery = AudioDiscovery()
    discovery.discover()
    return discovery


def print_discovery_report(discovery: AudioDiscovery) -> None:
    """Print a user-friendly discovery report."""
    report = discovery.generate_report()
    
    print("\n" + "=" * 60)
    print("🎤 Audio Device Discovery Report")
    print("=" * 60)
    
    print(f"\n📊 Total devices found: {report['total_devices']}")
    
    print("\n🎙️  Input Devices:")
    for dev in report['input_devices']:
        default_mark = " ⭐" if dev['is_default'] else ""
        print(f"   [{dev['index']}] {dev['name']}{default_mark}")
    
    print("\n🔊 Output Devices:")
    for dev in report['output_devices']:
        default_mark = " ⭐" if dev['is_default'] else ""
        print(f"   [{dev['index']}] {dev['name']}{default_mark}")
    
    print("\n✅ Recommended Configuration:")
    if report['recommended_input']['name']:
        print(f"   Input:  [{report['recommended_input']['index']}] {report['recommended_input']['name']}")
    else:
        print("   Input:  ⚠️  No suitable input device found")
    
    if report['recommended_output']['name']:
        print(f"   Output: [{report['recommended_output']['index']}] {report['recommended_output']['name']}")
    else:
        print("   Output: ⚠️  No suitable output device found")
    
    print("=" * 60 + "\n")


# Global config instance (lazy-loaded)
_config: AppConfig | None = None


def get_config() -> AppConfig:
    """Get the global configuration instance.
    
    Loads config on first call. Subsequent calls return cached instance.
    """
    global _config
    if _config is None:
        _config = AppConfig.load()
        if _config.bridge.hot_reload:
            _config.start_hot_reload()
    return _config


def reload_config() -> AppConfig:
    """Force reload configuration from disk."""
    global _config
    _config = AppConfig.load()
    if _config.bridge.hot_reload:
        _config.start_hot_reload()
    return _config
