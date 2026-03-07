#!/usr/bin/env python3
"""
Manual test script for Bug Tracker GitHub integration.

This script simulates real error scenarios and can optionally
create actual GitHub issues for testing.

Usage:
    python tests/manual_test_bug_tracker.py
    python tests/manual_test_bug_tracker.py --create-github  # Creates real issues
    python tests/manual_test_bug_tracker.py --cleanup        # Clear test bugs

"""

import sys
import os
import argparse
from datetime import datetime
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from bridge.bug_tracker import (
    BugTracker, BugSeverity, BugStatus, SystemSnapshot, setup_global_exception_handler
)
from bridge.config import get_config


def simulate_websocket_error():
    """Simulate a WebSocket connection error."""
    try:
        raise ConnectionError("WebSocket connection dropped unexpectedly")
    except Exception as e:
        return e


def simulate_audio_processing_error():
    """Simulate an audio pipeline error."""
    try:
        raise RuntimeError("Audio buffer overflow: 48000 samples queued")
    except Exception as e:
        return e


def simulate_stt_error():
    """Simulate STT processing error."""
    try:
        raise ValueError("Whisper model failed to transcribe: confidence < 0.3")
    except Exception as e:
        return e


def simulate_config_error():
    """Simulate configuration error."""
    try:
        raise KeyError("Missing required config key: 'openclaw.port'")
    except Exception as e:
        return e


def test_bug_capture():
    """Test basic bug capture without GitHub."""
    print("=" * 60)
    print("TEST 1: Basic Bug Capture (No GitHub)")
    print("=" * 60)
    
    tracker = BugTracker.get_instance()
    
    errors = [
        (simulate_websocket_error(), BugSeverity.HIGH, "websocket", "WebSocket connection failed"),
        (simulate_audio_processing_error(), BugSeverity.CRITICAL, "audio", "Audio buffer overflow"),
        (simulate_stt_error(), BugSeverity.MEDIUM, "stt", "STT transcription failed"),
        (simulate_config_error(), BugSeverity.HIGH, "config", "Configuration error"),
    ]
    
    bug_ids = []
    for error, severity, component, title in errors:
        bug_id = tracker.capture_exception(
            exception=error,
            severity=severity,
            component=component,
            title=title,
            context={"test_run": True, "timestamp": datetime.now().isoformat()}
        )
        bug_ids.append(bug_id)
        print(f"  ✓ Bug #{bug_id} captured: {title}")
    
    print(f"\n  Created {len(bug_ids)} test bugs")
    return bug_ids


def test_system_snapshot():
    """Test system snapshot capture."""
    print("\n" + "=" * 60)
    print("TEST 2: System Snapshot Capture")
    print("=" * 60)
    
    try:
        config = get_config()
    except:
        config = None
    
    snapshot = SystemSnapshot.capture(config=config)
    
    print(f"  Python Version: {snapshot.python_version[:30]}...")
    print(f"  Platform: {snapshot.platform}")
    print(f"  Platform Version: {snapshot.platform_version[:40]}...")
    print(f"  CPU Count: {snapshot.cpu_count}")
    print(f"  Memory Available: {snapshot.memory_available}")
    print(f"  Disk Free: {snapshot.disk_free}")
    print(f"  Audio Devices: {len(snapshot.audio_devices) if snapshot.audio_devices else 0} detected")
    print(f"  Timestamp: {snapshot.timestamp}")
    print("  ✓ System snapshot captured successfully")


def test_github_formatting():
    """Test GitHub issue formatting."""
    print("\n" + "=" * 60)
    print("TEST 3: GitHub Issue Formatting")
    print("=" * 60)
    
    tracker = BugTracker.get_instance()
    bugs = tracker.list_bugs(limit=1)
    
    if not bugs:
        print("  ✗ No bugs found. Run test 1 first.")
        return
    
    bug = bugs[0]
    github_data = bug.to_github_issue()
    
    print(f"  Title: {github_data['title'][:50]}...")
    print(f"  Labels: {', '.join(github_data['labels'])}")
    print(f"  Body length: {len(github_data['body'])} characters")
    print("  ✓ GitHub formatting successful")
    
    # Show sample of body
    print("\n  Sample body (first 500 chars):")
    print("  " + "-" * 56)
    for line in github_data['body'][:500].split('\n')[:10]:
        print(f"  {line}")
    print("  " + "-" * 56)


def test_github_integration(create_real_issue=False):
    """Test actual GitHub issue creation."""
    print("\n" + "=" * 60)
    print("TEST 4: GitHub Integration" + (" (Live)" if create_real_issue else " (Mock)"))
    print("=" * 60)
    
    tracker = BugTracker.get_instance()
    
    # Check for token
    token_path = Path.home() / ".github_token"
    if not token_path.exists():
        print(f"  ✗ GitHub token not found at {token_path}")
        print("  Create a token with 'repo' scope and save it to ~/.github_token")
        return False
    
    token = token_path.read_text().strip()
    tracker._github_token = token
    tracker._github_repo = "ray1caron/voice-openclaw-bridge-v2"
    
    print(f"  Token found: {token[:10]}...")
    print(f"  Repository: {tracker._github_repo}")
    
    # Create a test bug
    try:
        raise RuntimeError("Test error for GitHub integration")
    except Exception as e:
        bug_id = tracker.capture_exception(
            exception=e,
            severity=BugSeverity.MEDIUM,
            component="test",
            title="Test: Bug Tracker GitHub Integration",
            context={"test_mode": True, "manual_test": True}
        )
    
    bug = tracker.get_bug(bug_id)
    print(f"  Created test bug #{bug_id}")
    
    if create_real_issue:
        print("\n  Creating GitHub issue...")
        issue_number = tracker.create_github_issue(bug)
        
        if issue_number:
            print(f"  ✓ GitHub issue #{issue_number} created!")
            print(f"  URL: https://github.com/{tracker._github_repo}/issues/{issue_number}")
            
            # Update local bug with issue number
            tracker.update_bug_github_issue(bug_id, issue_number)
            print(f"  ✓ Bug #{bug_id} linked to GitHub issue #{issue_number}")
        else:
            print("  ✗ Failed to create GitHub issue")
            return False
    else:
        print("\n  (Mock mode - no actual issue created)")
        print("  Run with --create-github to create a real issue")
    
    return True


def test_cli_export():
    """Test CLI export functionality."""
    print("\n" + "=" * 60)
    print("TEST 5: CLI Export")
    print("=" * 60)
    
    tracker = BugTracker.get_instance()
    
    # Export to markdown
    output_path = Path("/tmp/test_bug_export.md")
    tracker.export_to_file(output_path, format="markdown")
    
    print(f"  Exported to: {output_path}")
    print(f"  File size: {output_path.stat().st_size} bytes")
    
    # Show first few lines
    with open(output_path) as f:
        lines = f.readlines()[:15]
        print("\n  Sample content:")
        print("  " + "-" * 56)
        for line in lines:
            print(f"  {line.rstrip()[:57]}")
        print("  " + "-" * 56)
    
    print("  ✓ Export successful")


def test_global_exception_handler():
    """Test global exception handler setup."""
    print("\n" + "=" * 60)
    print("TEST 6: Global Exception Handler")
    print("=" * 60)
    
    tracker = BugTracker.get_instance()
    setup_global_exception_handler(tracker)
    
    print("  ✓ Global exception handler installed")
    print("  (Uncaught exceptions will now be captured)")


def show_stats():
    """Show bug statistics."""
    print("\n" + "=" * 60)
    print("Bug Statistics")
    print("=" * 60)
    
    tracker = BugTracker.get_instance()
    stats = tracker.get_stats()
    
    print(f"  Total Bugs: {stats.get('total', 0)}")
    print(f"  New: {stats.get('new', 0)}")
    print(f"  Fixed: {stats.get('fixed', 0)}")
    print(f"  Critical: {stats.get('critical', 0)}")
    print(f"  High: {stats.get('high', 0)}")
    print(f"  Medium: {stats.get('medium', 0)}")
    print(f"  Low: {stats.get('low', 0)}")


def cleanup_test_bugs():
    """Clean up test bugs."""
    print("\n" + "=" * 60)
    print("Cleanup: Test Bugs")
    print("=" * 60)
    
    tracker = BugTracker.get_instance()
    bugs = tracker.list_bugs(limit=100)
    
    test_bugs = [b for b in bugs if b.title.startswith("Test:") or b.user_context and "test_mode" in str(b.user_context)]
    
    print(f"  Found {len(test_bugs)} test bugs")
    
    if not test_bugs:
        print("  No test bugs to clean up")
        return
    
    confirm = input(f"  Delete {len(test_bugs)} test bugs? [y/N]: ")
    if confirm.lower() != 'y':
        print("  Cancelled")
        return
    
    # Delete test bugs (would need delete method in tracker)
    print("  (Delete functionality would go here)")
    print("  Note: Run 'python -m bridge.bug_cli clear --force' to clear fixed bugs")


def main():
    parser = argparse.ArgumentParser(description="Bug Tracker Test Script")
    parser.add_argument('--create-github', action='store_true',
                       help='Create actual GitHub issues (requires token)')
    parser.add_argument('--cleanup', action='store_true',
                       help='Clean up test bugs')
    parser.add_argument('--test', choices=['capture', 'snapshot', 'format', 'github', 'export', 'handler', 'stats', 'all'],
                       default='all', help='Run specific test')
    
    args = parser.parse_args()
    
    print("Bug Tracker GitHub Integration Test")
    print("=" * 60)
    print()
    
    if args.cleanup:
        cleanup_test_bugs()
        return
    
    # Run tests
    tests_to_run = {
        'capture': [test_bug_capture],
        'snapshot': [test_system_snapshot],
        'format': [test_github_formatting],
        'github': [lambda: test_github_integration(args.create_github)],
        'export': [test_cli_export],
        'handler': [test_global_exception_handler],
        'stats': [show_stats],
        'all': [test_bug_capture, test_system_snapshot, test_github_formatting,
                lambda: test_github_integration(args.create_github),
                test_cli_export, test_global_exception_handler, show_stats]
    }
    
    for test_func in tests_to_run[args.test]:
        try:
            result = test_func()
            if result is False:
                print("\n  ✗ Test failed")
                sys.exit(1)
        except Exception as e:
            print(f"\n  ✗ Test failed with exception: {e}")
            import traceback
            traceback.print_exc()
            sys.exit(1)
    
    print("\n" + "=" * 60)
    print("All tests completed!")
    print("=" * 60)
    print()
    print("Next steps:")
    print("  1. Review bugs: python -m bridge.bug_cli list")
    print("  2. Export bugs: python -m bridge.bug_cli export bugs.md")
    print("  3. To create real GitHub issues: python tests/manual_test_bug_tracker.py --create-github")
    print("  4. Cleanup: python tests/manual_test_bug_tracker.py --cleanup")


if __name__ == "__main__":
    main()
