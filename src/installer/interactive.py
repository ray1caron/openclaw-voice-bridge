"""Interactive Installer with User Prompts.

Provides a truly interactive installation experience with:
- Step-by-step prompts
- Audio testing wizard (mic, speaker, and STT)
- Configuration viewing
- Confirmation dialogs
"""

from __future__ import annotations

import os
import sys
import time
import numpy as np
from pathlib import Path
from typing import Optional, List, Callable

import structlog

logger = structlog.get_logger()


class InteractiveInstaller:
    """Interactive installation with user prompts."""
    
    def __init__(self, workspace: Optional[Path] = None):
        """Initialize interactive installer.
        
        Args:
            workspace: Optional workspace path
        """
        self.workspace = workspace or Path.cwd()
        self.logger = structlog.get_logger()
        self.results = []
        from installer.diagnostic import DiagnosticReport
        self._diag = DiagnosticReport()
        self._openclaw_test_result = None  # set by _step_integration_test
        
    def clear_screen(self):
        """Clear the terminal screen."""
        os.system('clear' if os.name == 'posix' else 'cls')
    
    def print_header(self, title: str):
        """Print a styled header."""
        print("\n" + "=" * 60)
        print(f"  {title}")
        print("=" * 60 + "\n")
    
    def print_step(self, step_num: int, total: int, title: str):
        """Print step header."""
        print(f"\n📍 Step {step_num}/{total}: {title}")
        print("-" * 50)
    
    def print_success(self, message: str):
        """Print success message."""
        print(f"  ✅ {message}")
    
    def print_error(self, message: str):
        """Print error message."""
        print(f"  ❌ {message}")
    
    def print_warning(self, message: str):
        """Print warning message."""
        print(f"  ⚠️  {message}")
    
    def print_info(self, message: str):
        """Print info message."""
        print(f"  ℹ️  {message}")
    
    def prompt_yes_no(self, question: str, default: bool = False) -> bool:
        """Prompt for yes/no answer.
        
        Args:
            question: Question to ask
            default: Default answer if user just presses Enter
            
        Returns:
            True for yes, False for no
        """
        default_str = "Y/n" if default else "y/N"
        while True:
            try:
                response = input(f"  {question} [{default_str}]: ").strip().lower()
                if not response:
                    return default
                if response in ('y', 'yes'):
                    return True
                if response in ('n', 'no'):
                    return False
                print("  Please answer y or n.")
            except (EOFError, KeyboardInterrupt):
                print()
                return default
    
    def prompt_continue(self, message: str = "Press Enter to continue"):
        """Wait for user to press Enter."""
        try:
            input(f"  {message}... ")
        except (EOFError, KeyboardInterrupt):
            print()
    
    def prompt_choice(self, question: str, choices: List[str], default: int = 0) -> int:
        """Prompt for choice from list.
        
        Args:
            question: Question to ask
            choices: List of choices
            default: Default choice index
            
        Returns:
            Index of selected choice
        """
        print(f"  {question}")
        for i, choice in enumerate(choices, 1):
            marker = "→" if i - 1 == default else " "
            print(f"  {marker} {i}. {choice}")
        
        while True:
            try:
                response = input(f"  Choose [1-{len(choices)}] (default {default + 1}): ").strip()
                if not response:
                    return default
                choice = int(response) - 1
                if 0 <= choice < len(choices):
                    return choice
                print(f"  Please choose 1-{len(choices)}.")
            except (ValueError, EOFError, KeyboardInterrupt):
                return default
    
    def run(self) -> bool:
        """Run the interactive installer.
        
        Returns:
            True if installation successful
        """
        self.clear_screen()
        self.print_header("🎙️  Voice Bridge Installer")
        
        print("Welcome! This installer will guide you through setting up Voice Bridge.\n")
        print("I'll check your system, configure hardware, and prepare everything for you.\n")
        
        self.prompt_continue("Ready to begin?")
        
        # Step 1: Detection
        if not self._step_detection():
            return False
        
        # Step 2: Cleanup (if needed)
        if not self._step_cleanup():
            return False
        
        # Step 3: Dependencies
        if not self._step_dependencies():
            return False
        
        # Step 4: Hardware
        if not self._step_hardware():
            return False
        
        # Step 5: Configuration
        if not self._step_configuration():
            return False
        
        # Step 6: Bug Check
        if not self._step_bug_check():
            return False
        
        # Step 7: Integration Test (OpenClaw communication)
        if not self._step_integration_test():
            # Non-fatal, continue anyway
            pass
        
        # Step 8: Summary
        self._step_summary()
        
        return True
    
    def _step_detection(self) -> bool:
        """Step 1: Detect previous installations."""
        self.print_step(1, 8, "Checking for Previous Installations")
        
        from installer.detector import detect_previous_installation
        
        print("  Scanning for existing Voice Bridge installations...\n")
        report = detect_previous_installation(workspace=self.workspace)
        
        if report.has_running_processes:
            self.print_warning(f"Found {len(report.running_processes)} running process(es)")
            for proc in report.running_processes[:3]:
                print(f"     • {proc}")
            if len(report.running_processes) > 3:
                print(f"     ... and {len(report.running_processes) - 3} more")
            
            if not self.prompt_yes_no("Stop these processes and continue?", default=True):
                self.print_error("Cannot continue with running processes.")
                return False
            
            print("\n  Stopping processes...")
            from installer.detector import cleanup_installation
            cleanup_installation(report, stop_processes=True)
            self.print_success("Processes stopped")
        
        if report.has_traces:
            self.print_info(f"Found {len(report.traces)} installation trace(s)")
            for trace in report.traces[:5]:
                print(f"     • {trace.description}: {trace.path}")
            
            if not self.prompt_yes_no("Remove previous installation files?", default=True):
                print("  Keeping existing files.")
            else:
                print("\n  Cleaning up...")
                from installer.detector import cleanup_installation
                cleanup_installation(report, stop_processes=False)
                self.print_success("Cleanup complete")
        else:
            self.print_success("No previous installations found - clean system")
        
        self.prompt_continue()
        return True
    
    def _step_cleanup(self) -> bool:
        """Step 2: Cleanup (if needed)."""
        self.print_step(2, 8, "Preparing Environment")
        
        print("  Checking installation directories...\n")
        
        # Ensure directories exist
        dirs = [
            Path.home() / ".voice-bridge",
            Path.home() / ".local" / "share" / "voice-bridge",
            Path.home() / ".local" / "state" / "voice-bridge" / "logs",
        ]
        
        for d in dirs:
            if not d.exists():
                if self.prompt_yes_no(f"Create {d}?", default=True):
                    d.mkdir(parents=True, exist_ok=True)
                    print(f"     Created {d}")
        
        self.print_success("Environment prepared")
        self.prompt_continue()
        return True
    
    def _step_dependencies(self) -> bool:
        """Step 3: Check dependencies."""
        self.print_step(3, 8, "Checking Dependencies")
        
        print("  Verifying required packages...\n")
        
        dependencies = [
            ("pydantic", None, "Configuration validation"),
            ("pyyaml", "yaml", "YAML parsing"),
            ("websockets", None, "WebSocket communication"),
            ("sounddevice", None, "Audio capture/playback"),
            ("numpy", None, "Audio processing"),
        ]
        
        missing = []
        installed = []
        
        for display_name, import_name, purpose in dependencies:
            try:
                __import__(import_name or display_name)
                print(f"  ✅ {display_name:15} - {purpose}")
                installed.append(display_name)
            except ImportError:
                print(f"  ❌ {display_name:15} - {purpose} (MISSING)")
                missing.append(display_name)
        
        if missing:
            self.print_error(f"Missing packages: {', '.join(missing)}")
            print(f"\n  Install with: pip install {' '.join(missing)}")
            
            if self.prompt_yes_no("Install missing packages now?", default=True):
                import subprocess
                cmd = [sys.executable, "-m", "pip", "install", "--break-system-packages"] + missing
                print(f"\n  Running: {' '.join(cmd)}\n")
                result = subprocess.run(cmd, capture_output=True, text=True)
                if result.returncode == 0:
                    self.print_success("Packages installed")
                else:
                    self.print_error("Failed to install packages")
                    print(result.stderr)
                    return False
            else:
                self.print_warning("Some features may not work without these packages")
        else:
            self.print_success("All dependencies satisfied")
        
        self.prompt_continue()
        return True
    
    def _step_hardware(self) -> bool:
        """Step 4: Test audio hardware."""
        self.print_step(4, 8, "Audio Hardware Setup")
        
        from installer.hardware_test import HardwareTester
        
        tester = HardwareTester()
        
        if not tester.audio_available:
            self.print_warning("Audio libraries not available")
            print("  Install with: pip install sounddevice numpy")
            if not self.prompt_yes_no("Continue without audio testing?", default=False):
                return False
            self.prompt_continue()
            return True
        
        # Discover devices
        print("  Discovering audio devices...\n")
        discovery = tester.test_device_discovery()
        print(f"     {discovery}\n")
        
        if not discovery.passed:
            self.print_error("No audio devices found!")
            if not self.prompt_yes_no("Continue anyway?", default=False):
                return False
        
        # Microphone test
        if self.prompt_yes_no("Test your microphone?", default=True):
            self._test_microphone(tester)
        
        # Speaker test
        if self.prompt_yes_no("Test your speakers?", default=True):
            self._test_speakers(tester)
        
        # Wake word acknowledgement test
        print()  # Blank line for separation
        if self.prompt_yes_no("Test wake word acknowledgement?", default=True):
            self._test_wake_word_acknowledgement()
        
        self.prompt_continue()
        return True
    
    def _test_microphone(self, tester):
        """Interactive microphone test with playback."""
        print("\n  🎤 Microphone Test")
        print("  " + "-" * 40)
        print("  I'll record 3 seconds of audio, then play it back.\n")
        
        self.prompt_continue("Speak into your microphone when ready")
        
        print("\n  Recording... ", end="", flush=True)
        
        result = tester.test_microphone_recording(duration_seconds=3.0)
        
        print("Done!")
        print(f"  {result}\n")
        
        if result.failed:
            self.print_error("Microphone test failed")
            print(f"     {result.message}")
            return
        
        self.print_success("Microphone recording successful!")
        
        # Ask if they want to hear playback
        if self.prompt_yes_no("Play back recording through speakers?", default=True):
            print("\n  Playing back recording...")
            
            playback_result = tester.play_last_recording()
            
            if playback_result.passed:
                print(f"  {playback_result}")
                
                heard = self.prompt_yes_no("Did you hear your voice?", default=True)
                if not heard:
                    self.print_warning("Please check your speaker volume and connections")
                    if self.prompt_yes_no("Try playing back again?", default=False):
                        print("\n  Playing back recording...")
                        tester.play_last_recording()
                else:
                    self.print_success("Audio input and output working!")
            else:
                self.print_error("Playback failed")
                print(f"     {playback_result.message}")
        
        # Offer STT test
        if self.prompt_yes_no("\n  Test Speech-to-Text (transcribe your recording)?", default=True):
            self._test_stt(tester)
    
    def _test_stt(self, tester):
        """Test speech-to-text with the recorded audio."""
        print("\n  📝 Speech-to-Text Test")
        print("  " + "-" * 40)
        
        # Check if we have a recording
        if not hasattr(tester, '_last_recording') or tester._last_recording is None:
            self.print_warning("No recording available. Please run microphone test first.")
            return
        
        print("  Loading speech-to-text engine...")
        
        try:
            from bridge.stt import STTEngine
            
            # Create STT engine with English forced for better accuracy
            from bridge.stt import STTConfig
            stt_config = STTConfig(language="en")  # Force English
            stt = STTEngine(config=stt_config)
            
            # Initialize the model
            if not stt.initialize():
                self.print_warning("STT engine not available (faster-whisper not installed)")
                print("     Install with: pip install faster-whisper")
                return
            
            print("  Transcribing your recording...")
            
            # Get the recording from the tester
            audio_data = tester._last_recording
            sample_rate = tester._last_sample_rate
            
            # Flatten audio if multi-channel (2D -> 1D)
            if len(audio_data.shape) > 1:
                audio_data = audio_data.flatten()
            
            # Transcribe (pass sample rate for resampling to 16kHz)
            import time as time_module
            start_time = time_module.time()
            text, confidence = stt.transcribe(audio_data, sample_rate=sample_rate)
            elapsed = time_module.time() - start_time
            
            print(f"\n  Transcribed in {elapsed:.2f}s:\n")
            print(f"  \"{text}\"")
            
            if hasattr(confidence, '__iter__') and not isinstance(confidence, (str, float)):
                # confidence is a list/array of segment confidences
                avg_conf = sum(confidence) / len(confidence) if confidence else 0
            else:
                avg_conf = confidence if confidence else 0.0
            
            print(f"\n  Confidence: {avg_conf:.1%}")
            
            if text.strip():
                self.print_success("Speech-to-text working!")
                
                # Ask if transcription was accurate
                accurate = self.prompt_yes_no("\n  Was the transcription accurate?", default=True)
                if not accurate:
                    self.print_info("Note: STT accuracy improves with:")
                    print("     • Speaking clearly and at normal pace")
                    print("     • Using a quality microphone")
                    print("     • Reducing background noise")
                    print("     • Larger Whisper models (medium, large)")
                
                # Offer TTS test with transcribed text
                if self.prompt_yes_no("\n  Test Text-to-Speech (hear your words)?", default=True):
                    self._test_tts(text, tester)
            else:
                self.print_warning("No speech detected in recording")
                print("     Try speaking louder or closer to the microphone")
                
        except ImportError:
            self.print_warning("STT module not available")
            print("     Install with: pip install faster-whisper")
        except Exception as e:
            self.print_error(f"STT test failed: {e}")
            self.logger.error("stt_test_failed", error=str(e))
    
    def _test_tts(self, text: str, tester):
        """Test text-to-speech with the transcribed text."""
        print("\n  🔊 Text-to-Speech Test")
        print("  " + "-" * 40)
        
        # Truncate text if too long
        display_text = text[:100] + "..." if len(text) > 100 else text
        print(f"  Speaking: \"{display_text}\"")
        
        try:
            from bridge.tts import TTSEngine
            
            # Create TTS engine
            tts = TTSEngine()
            
            # Initialize the model
            if not tts.initialize():
                self.print_warning("TTS engine not available (piper-tts not installed)")
                print("     Install with: pip install piper-tts")
                print("     And download a voice: piper download en_US-lessac-medium")
                return
            
            print("  Generating speech...")
            
            # Generate audio from text
            import time as time_module
            start_time = time_module.time()
            audio_data = tts.speak(text)
            elapsed = time_module.time() - start_time
            
            if audio_data is None or len(audio_data) == 0:
                self.print_warning("No audio generated")
                return
            
            print(f"  Generated in {elapsed:.2f}s")
            
            # Play audio through speakers
            print("  Playing through speakers...")
            
            import sounddevice as sd
            sd.play(audio_data, samplerate=22050)  # Piper uses 22050 Hz
            sd.wait()
            
            # Ask if user heard it
            heard = self.prompt_yes_no("\n  Did you hear the speech?", default=True)
            if heard:
                self.print_success("Text-to-speech working!")
            else:
                self.print_warning("Check your speaker volume and connections")
                if self.prompt_yes_no("Try playing again?", default=False):
                    sd.play(audio_data, samplerate=22050)
                    sd.wait()
            
        except ImportError:
            self.print_warning("TTS module not available")
            print("     Install with: pip install piper-tts")
        except Exception as e:
            self.print_error(f"TTS test failed: {e}")
            self.logger.error("tts_test_failed", error=str(e))
    
    def _test_speakers(self, tester):
        """Interactive speaker test."""
        print("\n  🔊 Speaker Test")
        print("  " + "-" * 40)
        
        # If the mic test with playback worked, speakers are already tested
        print("  Already tested during microphone playback.")
        print("  If you heard your voice, speakers are working! ✅\n")
        
        # Offer a separate tone test if they want
        if self.prompt_yes_no("Test with a system beep tone?", default=False):
            print("\n  Playing test tone...")
            
            result = tester.test_speaker_playback(duration_seconds=0.5)
            
            if result.passed:
                heard = self.prompt_yes_no("Did you hear the beep?", default=True)
                if heard:
                    self.print_success("Speakers confirmed working!")
                else:
                    self.print_warning("Check your speaker volume and connections")
            else:
                self.print_error("Speaker test failed")
                print(f"     {result.message}")
    
    def _test_wake_word_acknowledgement(self):
        """Test wake word detection and acknowledgement flow.
        
        This test:
        1. Prompts user to say the wake word
        2. Detects the wake word
        3. Sends acknowledgement to OpenClaw and plays back its response
        4. Plays the response
        5. Asks user to confirm they heard it
        """
        print("\n  🎙️  Wake Word Acknowledgement Test")
        print("  " + "-" * 40)
        
        # Load configuration
        try:
            from bridge.config import get_config
            config = get_config()
            wake_word = config.wake_word.wake_word
            ack_enabled = config.bridge.acknowledgement.enabled
            timeout_ms = config.bridge.acknowledgement.timeout_ms
        except Exception as e:
            self.logger.warning("config_load_failed", error=str(e))
            wake_word = "hey hal"
            ack_enabled = True
            timeout_ms = 5000

        if not ack_enabled:
            self.print_info("Wake word acknowledgement is disabled in configuration.")
            if not self.prompt_yes_no("Test anyway?", default=True):
                return

        print(f"\n  This test will verify your wake word detection and acknowledgement.")
        print(f"  Wake word: '{wake_word}'")
        print(f"  OpenClaw will provide the spoken response.")
        print()
        
        # Check if OpenClaw is running
        openclaw_running = self._check_openclaw_connection()
        
        if openclaw_running:
            self.print_success("OpenClaw connection detected")
            print("  The test will send the wake word to OpenClaw and play back its response.")
        else:
            self.print_warning("OpenClaw is not running or not connected")
            print("  This test requires OpenClaw to be running — start OpenClaw and retry.")
            return
        
        # Import the wake word tester
        try:
            from installer.wake_word_test import WakeWordAckTester, WakeWordTestStatus
        except ImportError as e:
            self.print_error("Wake word test module not available")
            print(f"     Error: {e}")
            return
        
        # Create tester
        tester = WakeWordAckTester(
            wake_word=wake_word,
            ack_timeout_ms=timeout_ms,
            listen_timeout_ms=15000,  # 15 seconds to say wake word
        )
        
        if not tester.audio_available:
            self.print_error("Audio libraries not available")
            print("     Install with: pip install sounddevice numpy")
            return
        
        # Print instructions
        print(f"\n  📢 Say '{wake_word}' when prompted to test wake word detection.")
        print("     I'll listen for up to 15 seconds.\n")
        
        if not self.prompt_yes_no("Ready to begin wake word test?", default=True):
            return
        
        print("\n  🎧 Listening for wake word...")
        print(f"     Say '{wake_word}' now!\n")
        
        # Define callbacks for real-time feedback
        def on_speech_detected():
            print("  👂 Speech detected...")
        
        def on_wake_detected(text):
            print(f"  ✅ Wake word detected: '{text}'")
        
        def on_ack_sent():
            print("  📤 Acknowledgement sent to OpenClaw...")
        
        def on_response_received(phrase):
            print(f"  📥 Response received: '{phrase}'")
        
        def on_playing():
            print("  🔊 Playing response...")
        
        def on_timeout():
            print("  ⏰ No wake word detected in time.")
        
        # Run the test
        result = tester.test_full_acknowledgement(
            on_speech_detected=on_speech_detected,
            on_wake_detected=on_wake_detected,
            on_ack_sent=on_ack_sent,
            on_response_received=on_response_received,
            on_playing=on_playing,
            on_timeout=on_timeout,
        )
        
        print()  # Blank line after test output
        
        # Handle result
        if result.status == WakeWordTestStatus.PASSED:
            self.print_success("Wake word acknowledgement test passed!")
            print(f"     Wake word: '{result.wake_word}'")
            print(f"     Detected: '{result.detected_text}'")
            if result.response_phrase:
                print(f"     OpenClaw response: '{result.response_phrase}'")
            print("     OpenClaw responded: ✅")

            # Ask user to confirm they heard the response
            heard = self.prompt_yes_no(
                "\n  Did you hear OpenClaw's response?",
                default=True
            )
            
            if heard:
                self.print_success("Wake word acknowledgement is working! 🎉")
            # Troubleshooting removed per user request
                
        elif result.status == WakeWordTestStatus.TIMEOUT:
            self.print_error("Wake word not detected within timeout")
            print(f"     Make sure you said '{wake_word}' clearly")
            print()
            
            if self.prompt_yes_no("Try again?", default=True):
                self._test_wake_word_acknowledgement()
            return
            
        elif result.status == WakeWordTestStatus.SKIPPED:
            self.print_warning("Test skipped")
            print(f"     {result.message}")
            
        else:
            self.print_error(f"Test failed: {result.message}")
            if result.error:
                print(f"     Error: {result.error}")
    
    def _probe_openclaw_verbose(self, host: str, port: int, auth_token: str | None) -> bool:
        """Run a verbose HTTP probe against OpenClaw and print curl-equivalent output.

        Shows the user exactly what command would be used and what OpenClaw returns,
        making it easy to diagnose slow/wrong responses before the timed test runs.

        Returns:
            True if probe succeeded (HTTP 200), False otherwise.
        """
        import json
        import socket
        import time
        import urllib.error
        import urllib.request

        url = f"http://{host}:{port}/v1/chat/completions"
        payload = json.dumps({
            "model": "openclaw:main",
            "messages": [{"role": "user", "content": "ping"}],
            "max_tokens": 5,
        })

        # Print the equivalent curl command so the user can reproduce it manually
        token_flag = (
            f' \\\n    -H "Authorization: Bearer $OPENCLAW_GATEWAY_TOKEN"'
            if auth_token else ""
        )
        print("  Equivalent curl command:")
        print(f"    curl -v --max-time 10 \\")
        print(f"      -H \"Content-Type: application/json\"{token_flag} \\")
        print(f"      -d '{payload}' \\")
        print(f"      {url}")
        print()

        # Run the probe with a generous 10-second timeout
        headers = {"Content-Type": "application/json"}
        if auth_token:
            headers["Authorization"] = f"Bearer {auth_token}"

        req = urllib.request.Request(
            url, data=payload.encode("utf-8"), headers=headers, method="POST"
        )

        print("  Probing OpenClaw (10s timeout)...")
        start = time.time()
        try:
            with urllib.request.urlopen(req, timeout=10.0) as resp:
                elapsed = (time.time() - start) * 1000
                body = resp.read(512).decode("utf-8", errors="replace")
                print(f"  > HTTP {resp.status}  ({elapsed:.0f}ms)")
                # Pretty-print JSON if possible
                try:
                    parsed = json.loads(body)
                    choices = parsed.get("choices", [])
                    preview = choices[0]["message"]["content"].strip() if choices else body[:120]
                    print(f"  > Response: {preview[:120]}")
                except Exception:
                    print(f"  > Body: {body[:120]}")
                print()
                return resp.status == 200

        except urllib.error.HTTPError as exc:
            elapsed = (time.time() - start) * 1000
            print(f"  > HTTP {exc.code} {exc.reason}  ({elapsed:.0f}ms)")
            try:
                body = exc.read(256).decode("utf-8", errors="replace")
                if body.strip():
                    print(f"  > Body: {body[:200]}")
            except Exception:
                pass
            if exc.code in (401, 403):
                self._show_auth_token_diagnostic()
            print()
            return False

        except urllib.error.URLError as exc:
            elapsed = (time.time() - start) * 1000
            print(f"  > Failed: {exc.reason}  ({elapsed:.0f}ms)")
            print()
            return False

        except (socket.timeout, OSError) as exc:
            elapsed = (time.time() - start) * 1000
            print(f"  > Failed: {exc}  ({elapsed:.0f}ms)")
            print()
            return False

    def _show_auth_token_diagnostic(self) -> None:
        """Auto-discover OpenClaw auth token; fall back to manual entry if not found."""
        import getpass
        import os

        host = self._openclaw_test_result.host if self._openclaw_test_result else "localhost"
        port = self._openclaw_test_result.port if self._openclaw_test_result else 18789

        # Find the voice-bridge config path for display / saving
        config_path = None
        for p in [os.path.expanduser("~/.voice-bridge/config.yaml"), "config.yaml"]:
            if os.path.exists(p):
                config_path = p
                break

        print()
        print("  Searching for OpenClaw auth token automatically...")

        # Reuse the bridge's own discovery logic — it searches all known locations
        # and validates each token against OpenClaw before returning it.
        discovered_token = None
        discovered_source = None
        try:
            from bridge.config import get_config
            cfg_obj = get_config()
            # Temporarily point discovery at the live host/port
            cfg_obj.openclaw.host = host
            cfg_obj.openclaw.port = port
            # Walk every source the bridge knows about
            token_sources = [
                ("OPENCLAW_GATEWAY_TOKEN env var",          lambda: os.environ.get("OPENCLAW_GATEWAY_TOKEN")),
                ("OPENCLAW_TOKEN env var",                   lambda: os.environ.get("OPENCLAW_TOKEN")),
                ("~/.openclaw/gateway_token",                lambda: cfg_obj._get_token_from_file(
                                                                 __import__("pathlib").Path.home() / ".openclaw" / "gateway_token")),
                ("~/.openclaw/.token",                       lambda: cfg_obj._get_token_from_file(
                                                                 __import__("pathlib").Path.home() / ".openclaw" / ".token")),
                ("~/.openclaw/openclaw.json",                lambda: cfg_obj._get_token_from_openclaw_json()),
                ("~/.openclaw/config.yaml",                  lambda: cfg_obj._get_token_from_yaml(
                                                                 __import__("pathlib").Path.home() / ".openclaw" / "config.yaml")),
                ("~/.openclaw/workspace/openclaw.yaml",      lambda: cfg_obj._get_token_from_yaml(
                                                                 __import__("pathlib").Path.home() / ".openclaw" / "workspace" / "openclaw.yaml")),
            ]
            openclaw_url = f"http://{host}:{port}"
            for source_name, getter in token_sources:
                try:
                    token = getter()
                    if not token:
                        continue
                    masked = token[:4] + "****" if len(token) > 4 else "****"
                    print(f"  > Found token in {source_name} ({masked}) — validating...")
                    if cfg_obj._validate_token(openclaw_url, token):
                        discovered_token = token
                        discovered_source = source_name
                        print(f"  > Token is valid!")
                        break
                    else:
                        print(f"  > Token rejected by OpenClaw (401)")
                except Exception:
                    continue
        except Exception as exc:
            print(f"  > Auto-discovery failed: {exc}")

        if discovered_token:
            # Save the working token to the voice-bridge config
            print()
            print(f"  Token found in: {discovered_source}")
            print("  Saving to Voice Bridge config...")
            self._save_token_to_config(discovered_token, config_path)
            print()
            print("  Re-probing OpenClaw with discovered token...")
            self._probe_openclaw_verbose(host, port, discovered_token)
            return

        # Auto-discovery failed — tell the user where to look and offer manual entry
        print("  No valid token found automatically.")
        print()
        print("  Expected token locations:")
        print("    ~/.openclaw/gateway_token           (plain text, one line)")
        print("    ~/.openclaw/.token                  (plain text, one line)")
        print("    ~/.openclaw/openclaw.json           (gateway > auth > token)")
        print("    ~/.openclaw/config.yaml             (gateway.auth_token)")
        print("    ~/.openclaw/workspace/openclaw.yaml (openclaw.auth_token)")
        print("    env: OPENCLAW_GATEWAY_TOKEN")
        print()

        if not self.prompt_yes_no("  Enter the auth token manually?", default=True):
            print()
            print("  Once you have the token:")
            print(f"    Edit {config_path or '~/.voice-bridge/config.yaml'}")
            print("    Set:  auth_token: 'your-token-here'")
            return

        print()
        print("  Enter the OpenClaw auth token (input is hidden):")
        try:
            token = getpass.getpass("  Token: ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            print("  Token entry cancelled.")
            return

        if not token:
            print("  No token entered — skipping.")
            return

        self._save_token_to_config(token, config_path)
        print()
        print("  Re-probing OpenClaw with the new token...")
        self._probe_openclaw_verbose(host, port, token)

    def _save_token_to_config(self, token: str, config_path: str | None) -> None:
        """Write auth token to the voice-bridge config file."""
        import os
        try:
            from bridge.config import get_config
            cfg_obj = get_config()
            cfg_obj.openclaw.auth_token = token
            cfg_obj.save()
            saved_path = config_path or os.path.expanduser("~/.voice-bridge/config.yaml")
            print(f"  Token saved to {saved_path}")
        except Exception as exc:
            # Fallback: edit the YAML line directly
            target = config_path or os.path.expanduser("~/.voice-bridge/config.yaml")
            try:
                with open(target) as f:
                    lines = f.readlines()
                written = False
                for i, line in enumerate(lines):
                    if line.strip().startswith("auth_token"):
                        lines[i] = f"  auth_token: '{token}'\n"
                        written = True
                        break
                if not written:
                    lines.append(f"  auth_token: '{token}'\n")
                with open(target, "w") as f:
                    f.writelines(lines)
                print(f"  Token written to {target}")
            except OSError as write_exc:
                print(f"  Could not write config file: {write_exc}")
                print(f"  Set manually:  export OPENCLAW_GATEWAY_TOKEN='{token}'")

    def _check_openclaw_connection(self) -> bool:
        """Check if OpenClaw is running and accessible.

        Uses a real TCP + HTTP test so we don't falsely report success.

        Returns:
            True if OpenClaw is reachable and responding
        """
        try:
            from bridge.config import get_config
            from installer.openclaw_test import test_openclaw_connection
            cfg = get_config().openclaw
            result = test_openclaw_connection(
                host=cfg.host,
                port=cfg.port,
                timeout=min(cfg.timeout, 5.0),
                auth_token=cfg.get_auth_token() if hasattr(cfg, "get_auth_token") else getattr(cfg, "auth_token", None),
            )
            self._openclaw_test_result = result
            return result.passed
        except Exception as e:
            self.logger.debug("openclaw_connection_check_failed", error=str(e))
            return False
    
    def _step_configuration(self) -> bool:
        """Step 5: Configuration."""
        self.print_step(5, 8, "Configuration")
        
        from installer.config_summary import ConfigSummary, validate_config
        
        summary = ConfigSummary()
        config = summary.get_config()
        
        if summary.config_path:
            print(f"  Found config: {summary.config_path}\n")
            
            if self.prompt_yes_no("View your configuration?", default=False):
                self._display_config(summary)
        else:
            print("  No configuration file found.")
            print("  Default configuration will be used.\n")
            
            if self.prompt_yes_no("View default configuration?", default=True):
                self._display_defaults()
        
        # Validate config
        report = validate_config()
        
        if report.has_errors:
            self.print_error("Configuration has errors:")
            for issue in report.issues:
                if issue.is_error:
                    print(f"     {issue}")
            
            if not self.prompt_yes_no("Continue with errors?", default=False):
                return False
        elif report.has_warnings:
            self.print_warning("Configuration has warnings:")
            for issue in report.issues:
                if issue.is_warning:
                    print(f"     {issue}")
        else:
            self.print_success("Configuration is valid")
        
        self.prompt_continue()
        return True
    
    def _display_config(self, summary: ConfigSummary):
        """Display current configuration."""
        from installer.config_summary import validate_config
        
        self.clear_screen()
        self.print_header("📄 Configuration File")
        
        print(f"Location: {summary.config_path}\n")
        
        report = validate_config()
        
        for section in report.sections:
            print(f"\n{section.icon} {section.display_name}:")
            print("-" * 40)
            for key, value in section.fields.items():
                print(f"  {key}: {value}")
            
            if section.issues:
                for issue in section.issues:
                    print(f"  {issue}")
        
        print("\n" + "=" * 60)
        self.prompt_continue()
    
    def _display_defaults(self):
        """Display default configuration."""
        self.clear_screen()
        self.print_header("📄 Default Configuration")
        
        from installer.config_summary import ConfigSummary
        
        summary = ConfigSummary()
        defaults = summary.get_defaults()
        
        for section_name, section_config in defaults.items():
            print(f"\n{section_name}:")
            print("-" * 40)
            for key, value in section_config.items():
                print(f"  {key}: {value}")
        
        print("\n" + "=" * 60)
        print("Configuration will be saved to: ~/.voice-bridge/config.yaml")
        self.prompt_continue()
    
    def _step_bug_check(self) -> bool:
        """Step 6: Check for known bugs."""
        self.print_step(6, 8, "Known Issues Check")
        
        from installer.bug_display import get_bug_summary
        
        print("  Checking bug tracker...\n")
        
        summary = get_bug_summary()
        
        if summary.is_clean:
            self.print_success("No known issues")
        else:
            print(f"  Total bugs: {summary.total_bugs}")
            print(f"  Unfixed: {summary.unfixed_count}\n")
            
            if summary.has_critical:
                self.print_warning("CRITICAL issues found:")
                for bug in summary.bugs:
                    if bug.severity.name == "CRITICAL":
                        print(f"     • #{bug.bug_id}: {bug.title}")
            
            if summary.has_high:
                self.print_warning("HIGH priority issues:")
                for bug in summary.bugs[:3]:
                    if bug.severity.name == "HIGH":
                        print(f"     • #{bug.bug_id}: {bug.title}")
            
            if summary.unfixed_count > 0:
                print()
                if not self.prompt_yes_no("Continue despite known issues?", default=True):
                    return False
        
        self.prompt_continue()
        return True
    
    def _discover_openclaw_token(self, config) -> Optional[str]:
        """Auto-discover OpenClaw authentication token from multiple sources.
        
        Searches in order:
        1. Voice Bridge config file (~/.voice-bridge/config.yaml)
        2. Environment variable (OPENCLAW_GATEWAY_TOKEN)
        3. OpenClaw config directory (~/.openclaw/)
        4. OpenClaw environment files
        5. Shell profile files
        
        Returns:
            Token string if found, None otherwise
        """
        import os
        from pathlib import Path
        
        # 1. Voice Bridge config
        if config and config.openclaw:
            if config.openclaw.auth_token:
                return config.openclaw.auth_token
            if config.openclaw.api_key:
                return config.openclaw.api_key
        
        # 2. Environment variable
        token = os.environ.get("OPENCLAW_GATEWAY_TOKEN")
        if token:
            return token
        
        # 3. OpenClaw config directory
        openclaw_dir = Path.home() / ".openclaw"
        if openclaw_dir.exists():
            # Check for token file
            token_file = openclaw_dir / "gateway_token"
            if token_file.exists():
                try:
                    return token_file.read_text().strip()
                except Exception:
                    pass
            
            # Check for config.yaml
            openclaw_config = openclaw_dir / "config.yaml"
            if openclaw_config.exists():
                try:
                    import yaml
                    with open(openclaw_config) as f:
                        oc_config = yaml.safe_load(f)
                        if oc_config:
                            # Check various token locations
                            if "gateway_token" in oc_config:
                                return oc_config["gateway_token"]
                            if "token" in oc_config:
                                return oc_config["token"]
                            if "auth" in oc_config and isinstance(oc_config["auth"], dict):
                                if "token" in oc_config["auth"]:
                                    return oc_config["auth"]["token"]
                except Exception:
                    pass
            
            # Check for .token file
            token_file = openclaw_dir / ".token"
            if token_file.exists():
                try:
                    return token_file.read_text().strip()
                except Exception:
                    pass
        
        # 4. OpenClaw workspace config
        workspace_config = Path.home() / ".openclaw" / "workspace" / "openclaw.yaml"
        if workspace_config.exists():
            try:
                import yaml
                with open(workspace_config) as f:
                    ws_config = yaml.safe_load(f)
                    if ws_config:
                        if "gateway_token" in ws_config:
                            return ws_config["gateway_token"]
                        if "token" in ws_config:
                            return ws_config["token"]
            except Exception:
                pass
        
        # 5. Check for OPENCLAW_TOKEN env var (alternate)
        token = os.environ.get("OPENCLAW_TOKEN")
        if token:
            return token
        
        return None
    
    def _save_openclaw_token(self, token: str) -> bool:
        """Save discovered token to Voice Bridge config for future use."""
        try:
            from bridge.config import get_config
            import yaml
            from pathlib import Path
            
            config = get_config()
            config_file = Path.home() / ".voice-bridge" / "config.yaml"
            
            # Load existing config
            config_data = {}
            if config_file.exists():
                with open(config_file) as f:
                    config_data = yaml.safe_load(f) or {}
            
            # Update with token
            if "openclaw" not in config_data:
                config_data["openclaw"] = {}
            config_data["openclaw"]["auth_token"] = token
            
            # Save
            config_file.parent.mkdir(parents=True, exist_ok=True)
            with open(config_file, 'w') as f:
                yaml.dump(config_data, f, default_flow_style=False)
            
            return True
        except Exception as e:
            self.logger.warning("failed_to_save_token", error=str(e))
            return False
    
    def _test_websocket_server(self) -> bool:
        """Test WebSocket server functionality.
        
        Tests:
        1. Server can start
        2. Client can connect
        3. Ping/pong works
        4. Protocol serialization works
        
        Returns:
            True if all tests pass
        """
        print("\n  🔌 WebSocket Server Test")
        print("  " + "-" * 40)
        
        try:
            import asyncio
            import websockets
            from bridge.websocket_server import WebSocketServer
            from bridge.protocol import PingMessage, PongMessage, HelloMessage
        except ImportError as e:
            self.print_error(f"Required modules not available: {e}")
            return False
        
        port = 18793  # Use different port for testing
        
        print(f"\n  Starting WebSocket server on port {port}...")
        
        server = None
        try:
            # Create server
            server = WebSocketServer(host="127.0.0.1", port=port, max_connections=5)
            
            async def run_test():
                # Start server
                server_task = asyncio.create_task(server.start())
                await asyncio.sleep(0.2)  # Wait for server to start
                
                if not server.is_running():
                    return False, "Server failed to start"
                
                print(f"  ✅ Server started on ws://127.0.0.1:{port}")
                
                # Connect client
                try:
                    async with websockets.connect(f"ws://127.0.0.1:{port}") as ws:
                        print("  ✅ Client connected")
                        
                        # Receive hello
                        greeting = await asyncio.wait_for(ws.recv(), timeout=2.0)
                        import json
                        greeting_data = json.loads(greeting)
                        
                        if greeting_data.get("type") == "hello":
                            print("  ✅ Received hello message")
                        else:
                            print(f"  ⚠️ Received unexpected: {greeting_data.get('type')}")
                        
                        # Test ping/pong
                        ping = PingMessage()
                        await ws.send(ping.to_json())
                        print("  ✅ Sent ping")
                        
                        response = await asyncio.wait_for(ws.recv(), timeout=2.0)
                        pong_data = json.loads(response)
                        
                        if pong_data.get("type") == "pong":
                            print("  ✅ Received pong")
                        else:
                            print(f"  ⚠️ Received unexpected: {pong_data.get('type')}")
                        
                        return True, "All tests passed"
                        
                except asyncio.TimeoutError:
                    return False, "Connection timed out"
                except Exception as e:
                    return False, f"Client error: {e}"
                finally:
                    await server.stop()
            
            # Run async test
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                success, message = loop.run_until_complete(run_test())
            finally:
                loop.close()
                # Reset event loop policy for subsequent tests
                asyncio.set_event_loop_policy(asyncio.DefaultEventLoopPolicy())
            
            if success:
                self.print_success(f"WebSocket test passed: {message}")
                return True
            else:
                self.print_error(f"WebSocket test failed: {message}")
                return False
                
        except Exception as e:
            self.print_error(f"WebSocket test failed: {e}")
            return False
        finally:
            if server:
                WebSocketServer.reset_instance()
        
        return True
    
    def _step_integration_test(self) -> bool:
        """Step 7: Test OpenClaw integration.
        
        This tests:
        1. Connection to OpenClaw
        2. Wake word simulation
        3. Message sending and response
        4. WebSocket server functionality
        """
        self.print_step(7, 8, "OpenClaw Integration Test")
        
        print("  This test verifies communication with OpenClaw.\n")
        
        # Test WebSocket server first
        print("  Testing WebSocket server...")
        ws_passed = self._test_websocket_server()
        print()
        
        # Verbose probe first — shows curl equivalent + raw response before the timed test
        probe_ok = False
        try:
            from bridge.config import get_config
            _cfg = get_config().openclaw
            _token = _cfg.get_auth_token() if hasattr(_cfg, "get_auth_token") else getattr(_cfg, "auth_token", None)
            print("  Testing OpenClaw HTTP endpoint...")
            probe_ok = self._probe_openclaw_verbose(_cfg.host, _cfg.port, _token)
        except Exception:
            pass  # config unreadable — the connection test will surface the error

        # If the probe timed out, OpenClaw may have been cold-starting. Run a
        # second check to give it a chance to respond now that it is warm.
        if not probe_ok:
            print("  Retrying connection check (OpenClaw may have been cold-starting)...")
        openclaw_running = self._check_openclaw_connection()
        
        if not openclaw_running:
            self.print_error("OpenClaw is NOT reachable at the configured address")
            print()

            # Show detailed diagnostic from the real connection test
            r = self._openclaw_test_result
            if r:
                hw = r.as_hardware_result()
                if hw.details:
                    for line in hw.details.splitlines():
                        print(f"  {line}")
                print()

            # Add to the diagnostic report so it appears in the summary fix guide
            from installer.diagnostic import Issue
            ctx = []
            if r:
                hw = r.as_hardware_result()
                ctx = [l.strip() for l in (hw.details or "").splitlines() if l.strip()]
            self._diag.add(Issue(
                step="OpenClaw Integration Test",
                title=r.as_hardware_result().message if r else "OpenClaw is not reachable",
                context=ctx,
                fix_steps=[
                    "Start OpenClaw and make sure it is listening on the configured host:port.",
                    "Check config: ~/.voice-bridge/config.yaml  (openclaw.host / openclaw.port)",
                    "Verify with: curl http://<host>:<port>/v1/chat/completions",
                    "If auth required: export OPENCLAW_GATEWAY_TOKEN=your_token",
                    "Re-run installer: python -m installer",
                ],
                is_blocking=True,
            ))

            if not self.prompt_yes_no("Continue without integration test?", default=True):
                return False

            self.print_info("Integration test skipped — see fix guide in summary")
            self.prompt_continue()
            return True
        
        self.print_success("OpenClaw connection detected")
        
        # Ask if user wants to run the integration test
        print()
        if not self.prompt_yes_no("Run integration test?", default=True):
            self.print_info("Integration test skipped")
            self.prompt_continue()
            return True
        
        print("\n  Running integration test...\n")
        
        # Run the test
        try:
            self._run_integration_test()
        except Exception as e:
            self.print_error(f"Integration test failed: {e}")
            self.logger.error("integration_test_failed", error=str(e))
            self.prompt_continue()
            return False
        
        self.prompt_continue()
        return True
    
    def _run_integration_test(self):
        """Run the actual integration test."""
        from bridge.config import get_config
        from bridge.constants import DEFAULT_WAKE_WORD
        import os
        
        config = get_config()
        wake_word = config.wake_word.wake_word or DEFAULT_WAKE_WORD
        openclaw_url = f"{'https' if config.openclaw.secure else 'http'}://{config.openclaw.host}:{config.openclaw.port}"
        
        # Auto-discover auth token from multiple sources
        auth_token = self._discover_openclaw_token(config)
        
        print(f"  📡 OpenClaw URL: {openclaw_url}")
        if auth_token:
            print(f"  🔑 Auth Token: {'*' * 8}{auth_token[-4:] if len(auth_token) > 8 else '****'} (auto-discovered)")
        else:
            print(f"  ⚠️  No auth token found")
        print(f"  🎙️  Wake Word: '{wake_word}'\n")
        
        # Import test components
        try:
            from bridge.http_client import OpenClawHTTPClient
            import asyncio
        except ImportError as e:
            self.print_error("Required modules not available")
            print(f"     Error: {e}")
            return
        
        # Create HTTP client
        try:
            # Update config with discovered token if found
            if auth_token and not config.openclaw.auth_token:
                config.openclaw.auth_token = auth_token
                # Save for future runs
                self._save_openclaw_token(auth_token)
            client = OpenClawHTTPClient(config=config.openclaw)
        except Exception as e:
            self.print_error(f"Failed to create HTTP client: {e}")
            return
        
        # Test message
        test_message = "Hello, this is a test of the voice bridge."
        
        # Step 1: Simulate wake word
        print(f"  📢 Simulating wake word: '{wake_word}'")
        print("     In a real scenario, this would be detected from audio input.\n")
        
        # Step 2: Send message to OpenClaw
        print(f"  📤 Sending test message...")
        print(f"     Message: '{test_message}'")
        
        try:
            # Use async method with fresh event loop
            async def send_test():
                messages = [{"role": "user", "content": test_message}]
                return await client.send_chat_request(messages=messages)
            
            # Run with fresh event loop (handles cleanup properly)
            import asyncio
            try:
                response = asyncio.run(send_test())
            except RuntimeError:
                # If event loop already exists, use thread pool
                import concurrent.futures
                with concurrent.futures.ThreadPoolExecutor() as executor:
                    future = executor.submit(asyncio.run, send_test())
                    response = future.result(timeout=30)
            
            # Step 3: Display response
            print("\n  📥 Response received:")
            print("  " + "-" * 50)
            
            if response and hasattr(response, 'content') and response.content:
                print(f"\n{response.content}\n")
                print("  " + "-" * 50)

                # Show metadata
                if hasattr(response, 'model'):
                    print(f"\n  Model: {response.model}")
                if hasattr(response, 'finish_reason'):
                    print(f"  Finish Reason: {response.finish_reason}")

                print(f"  Response Length: {len(response.content)} characters")

                # Speak the response via TTS so the user hears it
                print("\n  🔊 Playing response via TTS...")
                try:
                    from bridge.tts import TTSEngine
                    import sounddevice as sd
                    tts = TTSEngine()
                    if tts.initialize():
                        audio = tts.speak(response.content)
                        if audio is not None and len(audio) > 0:
                            sd.play(audio, samplerate=22050)
                            sd.wait()
                            print("  ✅ TTS playback: OK")
                        else:
                            print("  ⚠️  TTS returned empty audio")
                    else:
                        print("  ⚠️  TTS engine not available (voice model missing?)")
                except ImportError:
                    print("  ⚠️  TTS/audio libraries not available — skipping playback")
                except Exception as e:
                    print(f"  ⚠️  TTS playback failed: {e}")

                self.print_success("Integration test passed!")
                print("  ✅ Wake word simulation: OK")
                print("  ✅ Message sending: OK")
                print("  ✅ Response receiving: OK")
            else:
                self.print_warning("Received empty response from OpenClaw")
                print("     The server may not have a model configured.")
                print("\n  ⚠️  Integration test completed with warnings")
                
        except asyncio.TimeoutError:
            self.print_error("Request timed out")
            print("     OpenClaw took too long to respond.")
            print("     Check if the server is busy or increase timeout in config.")
            raise
            
        except Exception as e:
            error_msg = str(e)
            if "401" in error_msg or "Unauthorized" in error_msg:
                self.print_error("Authentication failed (401 Unauthorized)")
                print("     OpenClaw requires an authentication token.")
                print()
                print("     Set the token with:")
                print("       export OPENCLAW_GATEWAY_TOKEN='your-token-here'")
                print()
                print("     Or add to config.yaml:")
                print("       openclaw:")
                print("         auth_token: 'your-token-here'")
            else:
                self.print_error(f"Failed to send message: {e}")
                print("     Check if OpenClaw is running and accessible.")
            raise
    
    def _step_summary(self):
        """Step 8: Installation summary."""
        self.clear_screen()

        if self._diag.has_issues:
            if self._diag.has_blocking:
                self.print_header("⚠️  Installation Complete — Action Required")
                print("Voice Bridge is installed but will NOT work until the issues below are fixed.\n")
            else:
                self.print_header("⚠️  Installation Complete — Warnings")
                print("Voice Bridge is installed. Review the issues below before starting.\n")

            for line in self._diag.render().splitlines():
                print(line)
        else:
            self.print_header("✅ Installation Complete!")
            print("Voice Bridge is ready to use.\n")
        
        print("Next steps:")
        print("  1. Start Voice Bridge:")
        print("     python -m bridge.main")
        print()
        print("  2. Or use the run script:")
        print("     ./run_bridge.sh")
        print()
        print("  3. Configure audio devices:")
        print("     Edit ~/.voice-bridge/config.yaml")
        print()

        if not self._diag.has_issues:
            self.print_success("Installation successful!")

        print("\n" + "=" * 60)

        # Ask if user wants to start the bridge (skip if blocking issues exist)
        if self._diag.has_blocking:
            print("\nFix the blocking issues above before starting Voice Bridge.")
        elif self.prompt_yes_no("\nWould you like to start Voice Bridge now?", default=True):
            print("\n  🎙️  Starting Voice Bridge...\n")
            
            # Set PYTHONPATH and run
            import subprocess
            import sys
            import os
            
            workspace_src = self.workspace / "src"
            env = os.environ.copy()
            env["PYTHONPATH"] = str(workspace_src)
            
            try:
                # Run the bridge in the foreground
                result = subprocess.run(
                    [sys.executable, "-m", "bridge.main"],
                    cwd=self.workspace,
                    env=env,
                )
            except KeyboardInterrupt:
                print("\n\n  Voice Bridge stopped.")
            except Exception as e:
                self.print_error(f"Failed to start Voice Bridge: {e}")
                print(f"\n  You can start it manually with:")
                print(f"     cd {self.workspace}")
                print(f"     PYTHONPATH=src python3 -m bridge.main")


def run_interactive(workspace: Optional[Path] = None) -> bool:
    """Run interactive installer.
    
    Args:
        workspace: Optional workspace path
        
    Returns:
        True if successful
    """
    installer = InteractiveInstaller(workspace=workspace)
    return installer.run()