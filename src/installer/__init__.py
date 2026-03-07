"""Voice Bridge Installer Module.

Provides interactive installation with:
- Previous installation detection and cleanup
- Hardware testing (mic/speaker validation)
- Wake word acknowledgement testing
- Bug tracker integration
- Configuration summary display
"""

from installer.detector import (
    InstallationDetector,
    InstallationState,
    InstallationTrace,
    detect_previous_installation,
    cleanup_installation,
)
from installer.hardware_test import (
    HardwareTester,
    HardwareTestResult,
    TestStatus,
    test_microphone,
    test_speakers,
)
from installer.wake_word_test import (
    WakeWordAckTester,
    WakeWordTestResult,
    WakeWordTestStatus,
    test_wake_word_detection,
    test_full_acknowledgement,
)
from installer.bug_display import (
    BugDisplay,
    show_unfixed_bugs,
    get_bug_summary,
)
from installer.config_summary import (
    ConfigSummary,
    show_config_summary,
    validate_config,
)
from installer.core import (
    Installer,
    InstallResult,
    InstallStep,
    run_interactive_install,
)
from installer.interactive import (
    InteractiveInstaller,
    run_interactive,
)

__all__ = [
    # Detector
    "InstallationDetector",
    "InstallationState",
    "InstallationTrace",
    "detect_previous_installation",
    "cleanup_installation",
    # Hardware Tester
    "HardwareTester",
    "HardwareTestResult",
    "TestStatus",
    "test_microphone",
    "test_speakers",
    # Wake Word Tester
    "WakeWordAckTester",
    "WakeWordTestResult",
    "WakeWordTestStatus",
    "test_wake_word_detection",
    "test_full_acknowledgement",
    # Bug Display
    "BugDisplay",
    "show_unfixed_bugs",
    "get_bug_summary",
    # Config Summary
    "ConfigSummary",
    "show_config_summary",
    "validate_config",
    # Core Installer
    "Installer",
    "InstallResult",
    "InstallStep",
    "run_interactive_install",
    # Interactive
    "InteractiveInstaller",
    "run_interactive",
]