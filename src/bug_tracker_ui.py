#!/usr/bin/env python3
"""
Bug Tracker UI - Terminal Interface for Voice Bridge Bug Database.

A rich terminal UI for viewing and managing bug reports.

Usage:
    python3 bug_tracker_ui.py [options]

Options:
    --db PATH       Path to bugs database (default: ~/.voice-bridge/bugs.db)
    --severity N    Filter by severity (critical, high, medium, low, info)
    --component C   Filter by component (audio_pipeline, wake_word, etc.)
    --status S      Filter by status (new, triaged, in_progress, fixed, closed)
    --limit N       Maximum bugs to display (default: 50)
    --stats         Show statistics only
    --export PATH   Export bugs to JSON or Markdown
    --watch         Watch mode - refresh automatically

Author: Voice Bridge Team
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Optional, List, Dict, Any

try:
    from rich.console import Console
    from rich.table import Table
    from rich.panel import Panel
    from rich.text import Text
    from rich.live import Live
    from rich.layout import Layout
    from rich.progress import Progress, BarColumn
    RICH_AVAILABLE = True
except ImportError:
    RICH_AVAILABLE = False
    print("Warning: 'rich' library not installed. Install with: pip install rich")
    print("Falling back to plain text output...")

from bridge.config import get_config


# Default database path
DEFAULT_DB_PATH = Path.home() / ".voice-bridge" / "bugs.db"


# Field labels for display
FIELD_LABELS = {
    "id": "ID",
    "timestamp": "Timestamp",
    "severity": "Severity",
    "component": "Component",
    "title": "Title",
    "description": "Description",
    "status": "Status",
    "created_at": "Created",
    "updated_at": "Updated",
    "github_issue": "GitHub Issue",
    "known_issue": "Known Issue",
}

# Severity styling
SEVERITY_STYLES = {
    "critical": ("bold red", "🔴"),
    "high": ("bold yellow", "🟠"),
    "medium": ("bold blue", "🔵"),
    "low": ("bold green", "🟢"),
    "info": ("bold white", "ℹ️"),
}

# Status styling
STATUS_STYLES = {
    "new": ("bold red", "🆕"),
    "triaged": ("bold yellow", "📋"),
    "in_progress": ("bold blue", "🔧"),
    "fixed": ("bold green", "✅"),
    "closed": ("bold white", "✓"),
    "duplicate": ("bold magenta", "📋"),
}

# Component display names
COMPONENT_NAMES = {
    "audio_pipeline": "Audio Pipeline",
    "wake_word": "Wake Word",
    "stt": "STT",
    "tts": "TTS",
    "orchestrator": "Orchestrator",
    "http_client": "HTTP Client",
    "websocket_client": "WebSocket",
    "config": "Config",
    "installer": "Installer",
    "known_issues": "Known Issues",
    "uncaught": "Uncaught",
}


class BugReport:
    """Bug report data class."""
    
    def __init__(self, row: sqlite3.Row):
        """Initialize from database row."""
        self.id = row["id"]
        self.timestamp = row["timestamp"]
        self.severity = row["severity"]
        self.component = row["component"]
        self.title = row["title"]
        self.description = row["description"]
        self.stack_trace = row["stack_trace"]
        self.system_state = json.loads(row["system_state"]) if row["system_state"] else {}
        self.user_context = row["user_context"]
        self.status = row["status"]
        self.created_at = row["created_at"]
        self.updated_at = row["updated_at"]
        self.github_issue = row["github_issue"]
        
    def is_known_issue(self) -> bool:
        """Check if this bug matches a known issue pattern."""
        # Check if component is 'known_issues' or title contains known patterns
        if self.component == "known_issues":
            return True
        
        known_patterns = [
            "wake_word_zero_scores",
            "audio_device_not_found",
            "stt_model_load_failed",
            "http_timeout",
            "vad_silence_loop",
            "tts_queue_overflow",
            "session_state_corrupted",
            "openclaw_connection_lost",
        ]
        
        for pattern in known_patterns:
            if pattern in self.title.lower() or pattern in self.description.lower():
                return True
        
        return False
    
    def get_component_display(self) -> str:
        """Get display name for component."""
        return COMPONENT_NAMES.get(self.component, self.component)
    
    def get_severity_emoji(self) -> str:
        """Get emoji for severity."""
        return SEVERITY_STYLES.get(self.severity, ("", ""))[1]
    
    def get_status_emoji(self) -> str:
        """Get emoji for status."""
        return STATUS_STYLES.get(self.status, ("", ""))[1]


class BugTrackerUI:
    """Bug Tracker Terminal UI."""
    
    def __init__(self, db_path: Optional[Path] = None):
        """
        Initialize Bug Tracker UI.
        
        Args:
            db_path: Path to SQLite database (default: from config or ~/.voice-bridge/bugs.db)
        """
        self.db_path = self._resolve_db_path(db_path)
        self.console = Console() if RICH_AVAILABLE else None
        
    def _resolve_db_path(self, db_path: Optional[Path]) -> Path:
        """Resolve database path from config or default."""
        if db_path:
            return Path(db_path)
        
        # Try to get from config
        try:
            config = get_config()
            if hasattr(config, 'bug_tracker') and hasattr(config.bug_tracker, 'db_path'):
                if config.bug_tracker.db_path:
                    return Path(config.bug_tracker.db_path)
        except Exception:
            pass
        
        # Fall back to default
        return DEFAULT_DB_PATH
    
    def get_bugs(
        self,
        severity: Optional[str] = None,
        component: Optional[str] = None,
        status: Optional[str] = None,
        limit: int = 50,
    ) -> List[BugReport]:
        """Get bugs from database with optional filtering."""
        if not self.db_path.exists():
            return []
        
        query = "SELECT * FROM bugs WHERE 1=1"
        params = []
        
        if severity:
            query += " AND severity = ?"
            params.append(severity.lower())
        if component:
            query += " AND component = ?"
            params.append(component.lower())
        if status:
            query += " AND status = ?"
            params.append(status.lower())
        
        query += " ORDER BY timestamp DESC LIMIT ?"
        params.append(limit)
        
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute(query, params)
            return [BugReport(row) for row in cursor.fetchall()]
    
    def get_bug_by_id(self, bug_id: int) -> Optional[BugReport]:
        """Get a single bug by ID."""
        if not self.db_path.exists():
            return None
        
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute("SELECT * FROM bugs WHERE id = ?", (bug_id,))
            row = cursor.fetchone()
            return BugReport(row) if row else None
    
    def get_stats(self) -> Dict[str, Any]:
        """Get bug statistics."""
        if not self.db_path.exists():
            return {
                "total": 0,
                "new": 0,
                "triaged": 0,
                "in_progress": 0,
                "fixed": 0,
                "closed": 0,
                "critical": 0,
                "high": 0,
                "medium": 0,
                "low": 0,
                "known_issues": 0,
            }
        
        with sqlite3.connect(self.db_path) as conn:
            stats = {}
            
            # Total and by status
            cursor = conn.execute("""
                SELECT 
                    COUNT(*) as total,
                    SUM(CASE WHEN status = 'new' THEN 1 ELSE 0 END) as new,
                    SUM(CASE WHEN status = 'triaged' THEN 1 ELSE 0 END) as triaged,
                    SUM(CASE WHEN status = 'in_progress' THEN 1 ELSE 0 END) as in_progress,
                    SUM(CASE WHEN status = 'fixed' THEN 1 ELSE 0 END) as fixed,
                    SUM(CASE WHEN status = 'closed' THEN 1 ELSE 0 END) as closed
                FROM bugs
            """)
            row = cursor.fetchone()
            stats["total"] = row[0]
            stats["new"] = row[1]
            stats["triaged"] = row[2]
            stats["in_progress"] = row[3]
            stats["fixed"] = row[4]
            stats["closed"] = row[5]
            
            # By severity
            cursor = conn.execute("""
                SELECT 
                    SUM(CASE WHEN severity = 'critical' THEN 1 ELSE 0 END) as critical,
                    SUM(CASE WHEN severity = 'high' THEN 1 ELSE 0 END) as high,
                    SUM(CASE WHEN severity = 'medium' THEN 1 ELSE 0 END) as medium,
                    SUM(CASE WHEN severity = 'low' THEN 1 ELSE 0 END) as low
                FROM bugs
            """)
            row = cursor.fetchone()
            stats["critical"] = row[0]
            stats["high"] = row[1]
            stats["medium"] = row[2]
            stats["low"] = row[3]
            
            # Known issues count
            cursor = conn.execute(
                "SELECT COUNT(*) FROM bugs WHERE component = 'known_issues'"
            )
            stats["known_issues"] = cursor.fetchone()[0]
            
            return stats
    
    def update_status(self, bug_id: int, new_status: str) -> bool:
        """Update bug status."""
        if not self.db_path.exists():
            return False
        
        valid_statuses = {"new", "triaged", "in_progress", "fixed", "closed", "duplicate"}
        if new_status.lower() not in valid_statuses:
            return False
        
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                "UPDATE bugs SET status = ?, updated_at = ? WHERE id = ?",
                (new_status.lower(), datetime.now().isoformat(), bug_id)
            )
            conn.commit()
            return cursor.rowcount > 0
    
    def display_stats(self):
        """Display bug statistics."""
        stats = self.get_stats()
        
        if RICH_AVAILABLE:
            self._display_stats_rich(stats)
        else:
            self._display_stats_plain(stats)
    
    def _display_stats_rich(self, stats: Dict[str, Any]):
        """Display statistics with rich formatting."""
        # Summary panel
        summary = Table.grid(padding=1)
        summary.add_column(justify="right")
        summary.add_column()
        
        summary.add_row("Total Bugs:", str(stats["total"]))
        summary.add_row("Known Issues:", str(stats["known_issues"]))
        
        self.console.print(Panel(summary, title="📊 Bug Tracker Statistics", expand=False))
        
        # By status
        status_table = Table(title="Status", show_header=True, header_style="bold cyan")
        status_table.add_column("Status", style="cyan")
        status_table.add_column("Count", justify="right")
        
        status_order = ["new", "triaged", "in_progress", "fixed", "closed"]
        for status in status_order:
            style, emoji = STATUS_STYLES.get(status, ("", ""))
            status_table.add_row(
                f"{emoji} {status.title()}",
                str(stats.get(status, 0)),
                style=style
            )
        
        # By severity
        severity_table = Table(title="Severity", show_header=True, header_style="bold cyan")
        severity_table.add_column("Severity", style="cyan")
        severity_table.add_column("Count", justify="right")
        
        severity_order = ["critical", "high", "medium", "low"]
        for severity in severity_order:
            style, emoji = SEVERITY_STYLES.get(severity, ("", ""))
            severity_table.add_row(
                f"{emoji} {severity.title()}",
                str(stats.get(severity, 0)),
                style=style
            )
        
        # Display side by side
        self.console.print()
        self.console.print(status_table)
        self.console.print()
        self.console.print(severity_table)
    
    def _display_stats_plain(self, stats: Dict[str, Any]):
        """Display statistics in plain text."""
        print("\n" + "=" * 50)
        print("📊 Bug Tracker Statistics")
        print("=" * 50)
        print(f"\nTotal Bugs: {stats['total']}")
        print(f"Known Issues: {stats['known_issues']}")
        
        print("\n--- By Status ---")
        status_order = ["new", "triaged", "in_progress", "fixed", "closed"]
        for status in status_order:
            emoji = STATUS_STYLES.get(status, ("", ""))[1]
            print(f"{emoji} {status.title():12} {stats.get(status, 0)}")
        
        print("\n--- By Severity ---")
        severity_order = ["critical", "high", "medium", "low"]
        for severity in severity_order:
            emoji = SEVERITY_STYLES.get(severity, ("", ""))[1]
            print(f"{emoji} {severity.title():12} {stats.get(severity, 0)}")
    
    def display_bugs(
        self,
        bugs: List[BugReport],
        show_details: bool = False,
    ):
        """Display list of bugs."""
        if not bugs:
            if RICH_AVAILABLE:
                self.console.print("[yellow]No bugs found matching criteria.[/]")
            else:
                print("No bugs found matching criteria.")
            return
        
        if RICH_AVAILABLE:
            self._display_bugs_rich(bugs, show_details)
        else:
            self._display_bugs_plain(bugs, show_details)
    
    def _display_bugs_rich(self, bugs: List[BugReport], show_details: bool):
        """Display bugs with rich formatting."""
        # Create main table
        table = Table(
            title=f"🐛 Bug Reports ({len(bugs)} results)",
            show_header=True,
            header_style="bold cyan",
            expand=True,
        )
        
        # Add columns with field labels
        table.add_column("ID", justify="right", style="bold", width=4)
        table.add_column("Timestamp", width=19)
        table.add_column("Severity", width=10)
        table.add_column("Component", width=15)
        table.add_column("Title", width=40)
        table.add_column("Description", width=50)
        table.add_column("Status", width=12)
        table.add_column("Created", width=19)
        table.add_column("Updated", width=19)
        table.add_column("GitHub", width=6)
        table.add_column("Known", width=6)
        
        for bug in bugs:
            # Truncate description
            desc = bug.description[:47] + "..." if len(bug.description) > 50 else bug.description
            
            # Format timestamps
            ts = self._format_timestamp(bug.timestamp)
            created = self._format_timestamp(bug.created_at)
            updated = self._format_timestamp(bug.updated_at)
            
            # Severity with color
            sev_style, sev_emoji = SEVERITY_STYLES.get(bug.severity, ("", ""))
            severity_text = f"{sev_emoji} {bug.severity.upper()}"
            
            # Status with color
            status_style, status_emoji = STATUS_STYLES.get(bug.status, ("", ""))
            status_text = f"{status_emoji} {bug.status}"
            
            # GitHub issue
            github_text = f"#{bug.github_issue}" if bug.github_issue else "-"
            
            # Known issue
            known_text = "✓ Yes" if bug.is_known_issue() else ""
            
            table.add_row(
                str(bug.id),
                ts,
                Text(severity_text, style=sev_style),
                bug.get_component_display(),
                bug.title[:40] + ("..." if len(bug.title) > 40 else ""),
                desc,
                Text(status_text, style=status_style),
                created,
                updated,
                github_text,
                known_text,
            )
        
        self.console.print(table)
        
        # Legend
        self.console.print()
        self.console.print("[bold]Legend:[/]")
        self.console.print("  Severity: 🔴 Critical | 🟠 High | 🔵 Medium | 🟢 Low | ℹ️ Info")
        self.console.print("  Status: 🆕 New | 📋 Triaged | 🔧 In Progress | ✅ Fixed | ✓ Closed")
        self.console.print("  Known: ✓ Yes = Matches known issue pattern")
    
    def _display_bugs_plain(self, bugs: List[BugReport], show_details: bool):
        """Display bugs in plain text."""
        print(f"\n{'='*120}")
        print(f"🐛 Bug Reports ({len(bugs)} results)")
        print(f"{'='*120}")
        
        # Header
        print(f"{'ID':>4} | {'Timestamp':<19} | {'Sev':<8} | {'Component':<15} | {'Status':<12} | {'Title':<40}")
        print(f"{'-'*4}-+-{'-'*19}-+-{'-'*8}-+-{'-'*15}-+-{'-'*12}-+-{'-'*40}")
        
        for bug in bugs:
            sev_emoji = SEVERITY_STYLES.get(bug.severity, ("", ""))[1]
            status_emoji = STATUS_STYLES.get(bug.status, ("", ""))[1]
            
            print(f"{bug.id:>4} | {self._format_timestamp(bug.timestamp):<19} | {sev_emoji} {bug.severity:<6} | {bug.get_component_display():<15} | {status_emoji} {bug.status:<10} | {bug.title[:40]}")
        
        print(f"\nLegend: Severity: 🔴 Critical | 🟠 High | 🔵 Medium | 🟢 Low")
        print(f"        Status: 🆕 New | 📋 Triaged | 🔧 In Progress | ✅ Fixed | ✓ Closed")
    
    def display_bug_detail(self, bug: BugReport):
        """Display detailed view of a single bug."""
        if RICH_AVAILABLE:
            self._display_bug_detail_rich(bug)
        else:
            self._display_bug_detail_plain(bug)
    
    def _display_bug_detail_rich(self, bug: BugReport):
        """Display detailed bug view with rich formatting."""
        # Header
        sev_style, sev_emoji = SEVERITY_STYLES.get(bug.severity, ("", ""))
        status_style, status_emoji = STATUS_STYLES.get(bug.status, ("", ""))
        
        header = Panel(
            f"[bold]Bug #{bug.id}[/]",
            subtitle=f"[bold]{sev_emoji} {bug.severity.upper()}[/] | [bold]{status_emoji} {bug.status}[/]",
        )
        self.console.print(header)
        
        # Main info table
        info = Table.grid(padding=(0, 2))
        info.add_column(justify="right", style="bold cyan")
        info.add_column()
        
        info.add_row("ID (id):", str(bug.id))
        info.add_row("Timestamp (timestamp):", self._format_timestamp(bug.timestamp, full=True))
        info.add_row("Severity (severity):", f"{sev_emoji} {bug.severity.upper()}")
        info.add_row("Component (component):", bug.get_component_display())
        info.add_row("Status (status):", f"{status_emoji} {bug.status}")
        info.add_row("Created (created_at):", self._format_timestamp(bug.created_at, full=True))
        info.add_row("Updated (updated_at):", self._format_timestamp(bug.updated_at, full=True))
        info.add_row("GitHub Issue (github_issue):", f"#{bug.github_issue}" if bug.github_issue else "None")
        info.add_row("Known Issue (known_issue):", "✓ Yes" if bug.is_known_issue() else "No")
        
        self.console.print(Panel(info, title="📋 Bug Details", expand=False))
        
        # Title
        self.console.print(f"\n[bold]Title (title):[/]")
        self.console.print(f"  {bug.title}")
        
        # Description
        self.console.print(f"\n[bold]Description (description):[/]")
        desc_panel = Panel(bug.description, expand=False)
        self.console.print(desc_panel)
        
        # User context
        if bug.user_context:
            self.console.print(f"\n[bold]User Context:[/]")
            self.console.print(f"  {bug.user_context}")
        
        # System state summary
        if bug.system_state:
            self.console.print(f"\n[bold]System State:[/]")
            state_table = Table.grid(padding=(0, 2))
            state_table.add_column(justify="right", style="bold")
            state_table.add_column()
            
            for key in ["platform", "python_version", "cpu_count", "session_id"]:
                if key in bug.system_state:
                    state_table.add_row(f"{key}:", str(bug.system_state.get(key, "N/A")))
            
            self.console.print(state_table)
    
    def _display_bug_detail_plain(self, bug: BugReport):
        """Display detailed bug view in plain text."""
        sev_emoji = SEVERITY_STYLES.get(bug.severity, ("", ""))[1]
        status_emoji = STATUS_STYLES.get(bug.status, ("", ""))[1]
        
        print(f"\n{'='*80}")
        print(f"🐛 Bug #{bug.id} | {sev_emoji} {bug.severity.upper()} | {status_emoji} {bug.status}")
        print(f"{'='*80}")
        
        print(f"\n{'ID (id):':>20} {bug.id}")
        print(f"{'Timestamp (timestamp):':>20} {self._format_timestamp(bug.timestamp, full=True)}")
        print(f"{'Severity (severity):':>20} {bug.severity.upper()}")
        print(f"{'Component (component):':>20} {bug.get_component_display()}")
        print(f"{'Status (status):':>20} {bug.status}")
        print(f"{'Created (created_at):':>20} {self._format_timestamp(bug.created_at, full=True)}")
        print(f"{'Updated (updated_at):':>20} {self._format_timestamp(bug.updated_at, full=True)}")
        print(f"{'GitHub Issue (github_issue):':>20} {'#' + str(bug.github_issue) if bug.github_issue else 'None'}")
        print(f"{'Known Issue (known_issue):':>20} {'Yes' if bug.is_known_issue() else 'No'}")
        
        print(f"\n{'Title (title):'}")
        print(f"  {bug.title}")
        
        print(f"\n{'Description (description):'}")
        print(f"  {bug.description}")
    
    def _format_timestamp(self, ts: str, full: bool = False) -> str:
        """Format timestamp for display."""
        try:
            # Parse ISO timestamp
            if 'T' in ts:
                dt = datetime.fromisoformat(ts.replace('Z', '+00:00'))
            else:
                dt = datetime.fromisoformat(ts)
            
            if full:
                return dt.strftime("%Y-%m-%d %H:%M:%S")
            else:
                return dt.strftime("%Y-%m-%d %H:%M")
        except (ValueError, TypeError, AttributeError):
            return ts[:19] if len(ts) > 19 else ts
    
    def export_bugs(self, output_path: Path, format: str = "json"):
        """Export bugs to file."""
        bugs = self.get_bugs(limit=10000)
        
        if format == "json":
            data = []
            for bug in bugs:
                data.append({
                    "id": bug.id,
                    "timestamp": bug.timestamp,
                    "severity": bug.severity,
                    "component": bug.component,
                    "title": bug.title,
                    "description": bug.description,
                    "status": bug.status,
                    "created_at": bug.created_at,
                    "updated_at": bug.updated_at,
                    "github_issue": bug.github_issue,
                    "known_issue": bug.is_known_issue(),
                    "system_state": bug.system_state,
                    "stack_trace": bug.stack_trace,
                })
            
            with open(output_path, "w") as f:
                json.dump(data, f, indent=2)
            
            if RICH_AVAILABLE:
                self.console.print(f"[green]✓ Exported {len(bugs)} bugs to {output_path}[/]")
            else:
                print(f"✓ Exported {len(bugs)} bugs to {output_path}")
        
        elif format == "markdown":
            with open(output_path, "w") as f:
                f.write("# Bug Reports\n\n")
                
                for bug in bugs:
                    sev_emoji = SEVERITY_STYLES.get(bug.severity, ("", ""))[1]
                    status_emoji = STATUS_STYLES.get(bug.status, ("", ""))[1]
                    
                    f.write(f"## {sev_emoji} Bug #{bug.id}: {bug.title}\n\n")
                    f.write(f"- **Severity:** {bug.severity}\n")
                    f.write(f"- **Status:** {bug.status}\n")
                    f.write(f"- **Component:** {bug.get_component_display()}\n")
                    f.write(f"- **Created:** {bug.created_at}\n")
                    f.write(f"- **GitHub Issue:** {f'#{bug.github_issue}' if bug.github_issue else 'None'}\n")
                    f.write(f"- **Known Issue:** {'Yes' if bug.is_known_issue() else 'No'}\n\n")
                    f.write(f"### Description\n\n{bug.description}\n\n")
                    f.write("---\n\n")
            
            if RICH_AVAILABLE:
                self.console.print(f"[green]✓ Exported {len(bugs)} bugs to {output_path}[/]")
            else:
                print(f"✓ Exported {len(bugs)} bugs to {output_path}")
    
    def watch_mode(self, refresh_interval: int = 5):
        """Watch mode - auto-refresh display."""
        if not RICH_AVAILABLE:
            print("Watch mode requires 'rich' library. Install with: pip install rich")
            return
        
        def generate_table() -> Table:
            bugs = self.get_bugs(limit=20)
            
            table = Table(
                title=f"🐛 Bug Tracker - Live (refreshed every {refresh_interval}s)",
                show_header=True,
                header_style="bold cyan",
            )
            
            table.add_column("ID", justify="right", width=4)
            table.add_column("Time", width=16)
            table.add_column("Sev", width=8)
            table.add_column("Component", width=12)
            table.add_column("Title", width=30)
            table.add_column("Status", width=10)
            
            for bug in bugs[:20]:
                sev_emoji = SEVERITY_STYLES.get(bug.severity, ("", ""))[1]
                status_emoji = STATUS_STYLES.get(bug.status, ("", ""))[1]
                
                table.add_row(
                    str(bug.id),
                    bug.timestamp[11:16],  # Just HH:MM
                    f"{sev_emoji} {bug.severity[:4].upper()}",
                    bug.component[:12],
                    bug.title[:30],
                    f"{status_emoji} {bug.status}",
                )
            
            return table
        
        with Live(generate_table(), refresh_per_second=0.5) as live:
            try:
                while True:
                    time.sleep(refresh_interval)
                    live.update(generate_table())
            except KeyboardInterrupt:
                self.console.print("\n[yellow]Watch mode stopped.[/]")


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Bug Tracker UI - Terminal Interface for Voice Bridge Bug Database",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Show all bugs
  python3 bug_tracker_ui.py

  # Show statistics only
  python3 bug_tracker_ui.py --stats

  # Filter by severity
  python3 bug_tracker_ui.py --severity critical

  # Filter by component
  python3 bug_tracker_ui.py --component audio_pipeline

  # View specific bug
  python3 bug_tracker_ui.py --bug 42

  # Export to JSON
  python3 bug_tracker_ui.py --export bugs.json --format json

  # Watch mode (auto-refresh)
  python3 bug_tracker_ui.py --watch
        """,
    )
    
    parser.add_argument(
        "--db",
        type=str,
        default=None,
        help="Path to bugs database (default: from config or ~/.voice-bridge/bugs.db)",
    )
    parser.add_argument(
        "--severity",
        type=str,
        choices=["critical", "high", "medium", "low", "info"],
        help="Filter by severity",
    )
    parser.add_argument(
        "--component",
        type=str,
        help="Filter by component (audio_pipeline, wake_word, stt, tts, etc.)",
    )
    parser.add_argument(
        "--status",
        type=str,
        choices=["new", "triaged", "in_progress", "fixed", "closed", "duplicate"],
        help="Filter by status",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=50,
        help="Maximum bugs to display (default: 50)",
    )
    parser.add_argument(
        "--bug",
        type=int,
        help="View specific bug by ID",
    )
    parser.add_argument(
        "--stats",
        action="store_true",
        help="Show statistics only",
    )
    parser.add_argument(
        "--export",
        type=str,
        help="Export bugs to file",
    )
    parser.add_argument(
        "--format",
        type=str,
        default="json",
        choices=["json", "markdown"],
        help="Export format (default: json)",
    )
    parser.add_argument(
        "--watch",
        action="store_true",
        help="Watch mode - auto-refresh display",
    )
    parser.add_argument(
        "--update-status",
        type=str,
        help="Update bug status (use with --bug ID)",
    )
    
    args = parser.parse_args()
    
    # Determine database path
    db_path = Path(args.db) if args.db else None
    
    # Create UI
    ui = BugTrackerUI(db_path=db_path)
    
    # Check if database exists
    if not ui.db_path.exists():
        if RICH_AVAILABLE:
            ui.console.print(f"[red]✗ Database not found: {ui.db_path}[/]")
            ui.console.print("[yellow]Run Voice Bridge to create the database.[/]")
        else:
            print(f"✗ Database not found: {ui.db_path}")
            print("Run Voice Bridge to create the database.")
        return 1
    
    # Handle commands
    if args.stats:
        ui.display_stats()
    
    elif args.export:
        ui.export_bugs(Path(args.export), args.format)
    
    elif args.watch:
        ui.watch_mode()
    
    elif args.bug:
        bug = ui.get_bug_by_id(args.bug)
        if bug:
            if args.update_status:
                if ui.update_status(args.bug, args.update_status):
                    if RICH_AVAILABLE:
                        ui.console.print(f"[green]✓ Updated bug #{args.bug} status to '{args.update_status}'[/]")
                    else:
                        print(f"✓ Updated bug #{args.bug} status to '{args.update_status}'")
                    # Re-fetch to show updated state
                    bug = ui.get_bug_by_id(args.bug)
            ui.display_bug_detail(bug)
        else:
            if RICH_AVAILABLE:
                ui.console.print(f"[red]✗ Bug #{args.bug} not found.[/]")
            else:
                print(f"✗ Bug #{args.bug} not found.")
            return 1
    
    else:
        # Default: list bugs
        bugs = ui.get_bugs(
            severity=args.severity,
            component=args.component,
            status=args.status,
            limit=args.limit,
        )
        ui.display_bugs(bugs)
    
    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)