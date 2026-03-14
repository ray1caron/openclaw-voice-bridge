#!/usr/bin/env python3
"""Voice Bridge Installer CLI.

Run with: python -m installer [OPTIONS]

Options:
    --interactive    Run with full interactive prompts (default)
    --auto           Run automatically without prompts
    --test-audio     Run interactive audio tests
    --show-bugs      Show known bugs before installing
    --show-config    Show configuration file
    --skip-hardware  Skip hardware validation
    --clean          Clean previous installation before installing
    --verbose        Show detailed output
    --debug          Show full debug output during bridge test
    --help           Show this message
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


def main():
    """Main entry point for the installer CLI."""
    parser = argparse.ArgumentParser(
        description="Voice Bridge Installer",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    python -m installer                    Interactive installation (recommended)
    python -m installer --auto              Automatic installation
    python -m installer --test-audio        Test audio hardware
    python -m installer --show-config        View configuration
    python -m installer --show-bugs         Show known bugs
    python -m installer --clean             Clean up previous installation
        """,
    )
    
    parser.add_argument(
        "--interactive",
        action="store_true",
        default=True,
        help="Run with full interactive prompts (default)",
    )
    
    parser.add_argument(
        "--auto",
        action="store_true",
        help="Run automatically without prompts",
    )
    
    parser.add_argument(
        "--test-audio",
        action="store_true",
        help="Run interactive audio hardware tests",
    )
    
    parser.add_argument(
        "--show-bugs",
        action="store_true",
        help="Show known bugs from tracker",
    )
    
    parser.add_argument(
        "--show-config",
        action="store_true",
        help="Show configuration file",
    )
    
    parser.add_argument(
        "--skip-hardware",
        action="store_true",
        help="Skip hardware validation",
    )
    
    parser.add_argument(
        "--clean",
        action="store_true",
        help="Clean previous installation before installing",
    )
    
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Show detailed output",
    )

    parser.add_argument(
        "--debug",
        action="store_true",
        help="Show full debug output during bridge test (traceback, bridge logs, component status)",
    )
    
    parser.add_argument(
        "--workspace",
        type=Path,
        default=None,
        help="Workspace directory (default: current directory)",
    )
    
    args = parser.parse_args()
    
    # If --auto, disable interactive
    if args.auto:
        args.interactive = False
    
    # Handle --show-config
    if args.show_config:
        return show_config()
    
    # Handle --show-bugs
    if args.show_bugs:
        return show_bugs()
    
    # Handle --test-audio
    if args.test_audio:
        return test_audio()
    
    # Handle --clean only
    if args.clean and not args.test_audio:
        return run_cleanup(args.workspace)
    
    # Run installation
    if args.interactive:
        return run_interactive_mode(args.workspace)
    else:
        return run_automatic_mode(args.workspace, args.verbose, args.skip_hardware, args.debug)


def show_config():
    """Show configuration file."""
    from installer.config_summary import show_config_summary
    
    print("\n" + "=" * 60)
    print("  Configuration")
    print("=" * 60 + "\n")
    
    print(show_config_summary())
    return 0


def show_bugs():
    """Show bugs from the bug tracker."""
    from installer.bug_display import get_bug_summary
    
    print("\n" + "=" * 60)
    print("  Known Issues")
    print("=" * 60 + "\n")
    
    summary = get_bug_summary()
    
    if summary.is_clean:
        print("✅ No known issues\n")
        return 0
    
    print(f"Total Bugs: {summary.total_bugs}")
    print(f"Unfixed: {summary.unfixed_count}")
    print("")
    
    if summary.has_critical:
        print("🔴 CRITICAL:")
        for bug in summary.bugs:
            if bug.severity.name == "CRITICAL":
                print(f"   #{bug.bug_id}: {bug.title}")
                print(f"      Component: {bug.component}")
                print("")
    
    if summary.has_high:
        print("🟠 HIGH:")
        for bug in summary.bugs:
            if bug.severity.name == "HIGH":
                print(f"   #{bug.bug_id}: {bug.title}")
                print(f"      Component: {bug.component}")
                print("")
    
    if summary.medium_count:
        print(f"🟡 MEDIUM: {summary.medium_count} bugs")
    
    if summary.low_count:
        print(f"🟢 LOW: {summary.low_count} bugs")
    
    print("")
    print("Run 'python -m bridge.bug_cli list' for details")
    
    return 1 if summary.has_critical else 0


def test_audio():
    """Run interactive audio tests."""
    from installer.hardware_test import HardwareTester
    
    print("\n" + "=" * 60)
    print("  🎙️ Audio Hardware Test")
    print("=" * 60 + "\n")
    
    tester = HardwareTester()
    
    if not tester.audio_available:
        print("❌ Audio libraries not installed")
        print("   Install with: pip install sounddevice numpy")
        return 1
    
    results = tester.run_all_tests(interactive=True)
    
    print("\n" + "-" * 60)
    print("Results:")
    print("-" * 60)
    
    all_passed = True
    for result in results:
        print(f"  {result}")
        if result.failed:
            all_passed = False
    
    print("-" * 60)
    
    if all_passed:
        print("\n✅ All audio tests passed")
        return 0
    else:
        print("\n❌ Some tests failed - check your audio setup")
        return 1


def run_cleanup(workspace: Path | None):
    """Run cleanup of previous installation."""
    from installer.detector import detect_previous_installation, cleanup_installation
    
    print("\n" + "=" * 60)
    print("  🧹 Cleaning Previous Installation")
    print("=" * 60 + "\n")
    
    # Detect previous installation
    report = detect_previous_installation(workspace=workspace)
    
    if not report.has_traces and not report.has_running_processes:
        print("✅ No previous installation found")
        return 0
    
    print(f"Found {len(report.traces)} trace(s)")
    print("")
    
    for trace in report.traces:
        print(f"  📂 {trace}")
    
    if report.has_running_processes:
        print("\n⚠️  Running processes:")
        for proc in report.running_processes:
            print(f"  • {proc}")
    
    print("")
    
    # Run cleanup
    success = cleanup_installation(report, force=False, stop_processes=True)
    
    if success:
        print("\n✅ Cleanup complete")
        return 0
    else:
        print("\n⚠️  Cleanup partially failed - some files may remain")
        return 1


def run_interactive_mode(workspace: Path | None):
    """Run interactive installation."""
    from installer.interactive import run_interactive
    return 0 if run_interactive(workspace) else 1


def run_automatic_mode(workspace: Path | None, verbose: bool, skip_hardware: bool, debug: bool = False):
    """Run automatic installation."""
    from installer.core import Installer

    installer = Installer(
        workspace=workspace,
        interactive=False,
        verbose=verbose,
        debug=debug,
        stop_on_error=False,
    )
    
    # Set up output callback
    installer.on_message(print)
    
    print("\n" + "=" * 60)
    print("  🎙️  Voice Bridge Installer")
    print("=" * 60)
    
    if skip_hardware:
        print("\n⚠️  Skipping hardware validation")
    
    success = installer.run()
    
    print("\n" + "=" * 60)
    
    if success:
        print("✅ Installation ready!")
        print("\nNext steps:")
        print("  1. Test audio: python -m installer --test-audio")
        print("  2. Start bridge: python -m bridge.main")
        return 0
    else:
        print("❌ Installation completed with issues")
        print("\nReview the warnings above and fix any problems.")
        return 1


if __name__ == "__main__":
    sys.exit(main())