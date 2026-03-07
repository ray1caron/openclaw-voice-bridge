"""
Bug tracker CLI - Manage and view captured bugs.

Usage:
    python -m bridge.bug_cli list           # List recent bugs
    python -m bridge.bug_cli show 42        # Show bug #42
    python -m bridge.bug_cli export bugs.md  # Export to markdown
    python -m bridge.bug_cli stats          # Show statistics
    python -m bridge.bug_cli clear          # Clear fixed bugs
"""

import argparse
import sys
from pathlib import Path

from rich.console import Console
from rich.table import Table
from rich.panel import Panel

from bridge.bug_tracker import BugTracker, BugStatus, BugSeverity

console = Console()


def cmd_list(tracker: BugTracker, args):
    """List bugs with filtering."""
    status = BugStatus(args.status) if args.status else None
    severity = BugSeverity(args.severity) if args.severity else None
    
    bugs = tracker.list_bugs(
        status=status,
        severity=severity,
        component=args.component,
        limit=args.limit,
    )
    
    if not bugs:
        console.print("[yellow]No bugs found.[/yellow]")
        return
    
    table = Table(title=f"Bug Reports ({len(bugs)} found)")
    table.add_column("ID", style="cyan", no_wrap=True)
    table.add_column("Severity", style="red")
    table.add_column("Component", style="green")
    table.add_column("Status", style="blue")
    table.add_column("Title", style="white")
    table.add_column("Time", style="dim")
    
    for bug in bugs:
        severity_color = {
            "critical": "[red]",
            "high": "[orange1]",
            "medium": "[yellow]",
            "low": "[green]",
            "info": "[blue]",
        }.get(bug.severity, "")
        
        table.add_row(
            str(bug.id),
            f"{severity_color}{bug.severity}[/]",
            bug.component,
            bug.status,
            bug.title[:50] + "..." if len(bug.title) > 50 else bug.title,
            bug.timestamp[:16],
        )
    
    console.print(table)


def cmd_show(tracker: BugTracker, args):
    """Show bug details."""
    bug = tracker.get_bug(args.id)
    
    if not bug:
        console.print(f"[red]Bug #{args.id} not found.[/red]")
        return
    
    # Severity color
    severity_color = {
        "critical": "red",
        "high": "orange1",
        "medium": "yellow",
        "low": "green",
        "info": "blue",
    }.get(bug.severity, "white")
    
    panel = Panel(
        f"[bold]{bug.title}[/bold]\n\n"
        f"[bold]Severity:[/bold] [{severity_color}]{bug.severity}[/{severity_color}]\n"
        f"[bold]Component:[/bold] {bug.component}\n"
        f"[bold]Status:[/bold] {bug.status}\n"
        f"[bold]Created:[/bold] {bug.created_at}\n\n"
        f"[bold]Description:[/bold]\n{bug.description}\n\n"
        f"[bold]Stack Trace:[/bold]\n[dim]{bug.stack_trace or 'None captured'}[/dim]\n\n"
        f"[bold]System State:[/bold]\n{str(bug.system_state)[:500]}...",
        title=f"Bug #{bug.id}",
        border_style=severity_color,
    )
    
    console.print(panel)


def cmd_stats(tracker: BugTracker, args):
    """Show bug statistics."""
    stats = tracker.get_stats()
    
    table = Table(title="Bug Statistics")
    table.add_column("Metric", style="cyan")
    table.add_column("Count", style="green")
    
    table.add_row("Total Bugs", str(stats["total"]))
    table.add_row("New (unread)", str(stats["new"]))
    table.add_row("Fixed", str(stats["fixed"]))
    table.add_row("Critical", str(stats["critical"]))
    
    console.print(table)


def cmd_export(tracker: BugTracker, args):
    """Export bugs to file."""
    output = Path(args.output)
    format = "markdown" if output.suffix == ".md" else "json"
    
    tracker.export_to_file(output, format)
    console.print(f"[green]Exported {format} to {output}[/green]")


def cmd_clear(tracker: BugTracker, args):
    """Clear fixed bugs."""
    bugs = tracker.list_bugs(status=BugStatus.FIXED)
    
    if not bugs:
        console.print("[yellow]No fixed bugs to clear.[/yellow]")
        return
    
    if not args.force:
        confirm = console.input(f"Delete {len(bugs)} fixed bugs? [y/N]: ")
        if confirm.lower() != "y":
            console.print("Cancelled.")
            return
    
    # TODO: Implement delete in tracker
    console.print(f"[green]Would delete {len(bugs)} fixed bugs[/green]")


def main():
    parser = argparse.ArgumentParser(description="Voice Bridge Bug Tracker CLI")
    subparsers = parser.add_subparsers(dest="command", help="Commands")
    
    # List command
    list_parser = subparsers.add_parser("list", help="List bugs")
    list_parser.add_argument("--status", choices=[s.value for s in BugStatus])
    list_parser.add_argument("--severity", choices=[s.value for s in BugSeverity])
    list_parser.add_argument("--component", help="Filter by component")
    list_parser.add_argument("--limit", type=int, default=50)
    list_parser.set_defaults(func=cmd_list)
    
    # Show command
    show_parser = subparsers.add_parser("show", help="Show bug details")
    show_parser.add_argument("id", type=int, help="Bug ID")
    show_parser.set_defaults(func=cmd_show)
    
    # Stats command
    stats_parser = subparsers.add_parser("stats", help="Show statistics")
    stats_parser.set_defaults(func=cmd_stats)
    
    # Export command
    export_parser = subparsers.add_parser("export", help="Export bugs")
    export_parser.add_argument("output", help="Output file path")
    export_parser.set_defaults(func=cmd_export)
    
    # Clear command
    clear_parser = subparsers.add_parser("clear", help="Clear fixed bugs")
    clear_parser.add_argument("--force", action="store_true")
    clear_parser.set_defaults(func=cmd_clear)
    
    args = parser.parse_args()
    
    if not args.command:
        parser.print_help()
        sys.exit(1)
    
    tracker = BugTracker.get_instance()
    args.func(tracker, args)


if __name__ == "__main__":
    main()
