"""Bug Tracker Display for Installation UI.

Shows current unfixed bugs during installation to inform users
of known issues before they proceed.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import List, Optional

import structlog

logger = structlog.get_logger()


class BugSeverity(Enum):
    """Bug severity levels."""
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    INFO = "info"


class BugStatus(Enum):
    """Bug status."""
    NEW = "new"
    TRIAGED = "triaged"
    IN_PROGRESS = "in_progress"
    FIXED = "fixed"
    CLOSED = "closed"
    DUPLICATE = "duplicate"


@dataclass
class BugInfo:
    """Information about a bug."""
    bug_id: int
    title: str
    severity: BugSeverity
    status: BugStatus
    component: str
    description: str
    timestamp: datetime
    
    @property
    def is_unfixed(self) -> bool:
        """Check if bug is not fixed."""
        return self.status not in (BugStatus.FIXED, BugStatus.CLOSED, BugStatus.DUPLICATE)
    
    @property
    def severity_icon(self) -> str:
        """Get icon for severity level."""
        return {
            BugSeverity.CRITICAL: "🔴",
            BugSeverity.HIGH: "🟠",
            BugSeverity.MEDIUM: "🟡",
            BugSeverity.LOW: "🟢",
            BugSeverity.INFO: "ℹ️",
        }.get(self.severity, "❓")
    
    @property
    def status_icon(self) -> str:
        """Get icon for status."""
        return {
            BugStatus.NEW: "🆕",
            BugStatus.TRIAGED: "📋",
            BugStatus.IN_PROGRESS: "🔨",
            BugStatus.FIXED: "✅",
            BugStatus.CLOSED: "📁",
            BugStatus.DUPLICATE: "📎",
        }.get(self.status, "❓")


@dataclass
class BugSummary:
    """Summary of bugs in the system."""
    total_bugs: int
    unfixed_count: int
    critical_count: int
    high_count: int
    medium_count: int
    low_count: int
    by_component: dict
    bugs: List[BugInfo]
    
    @property
    def has_critical(self) -> bool:
        return self.critical_count > 0
    
    @property
    def has_high(self) -> bool:
        return self.high_count > 0
    
    @property
    def is_clean(self) -> bool:
        """No unfixed bugs."""
        return self.unfixed_count == 0
    
    def summary_line(self) -> str:
        """Generate a one-line summary."""
        if self.is_clean:
            return "✅ No known bugs"
        
        parts = [f"{self.unfixed_count} unfixed"]
        if self.critical_count:
            parts.append(f"{self.critical_count} critical")
        if self.high_count:
            parts.append(f"{self.high_count} high")
        
        return f"⚠️ {', '.join(parts)}"
    
    def detailed_summary(self) -> str:
        """Generate a detailed summary."""
        lines = [
            f"Total Bugs: {self.total_bugs}",
            f"Unfixed: {self.unfixed_count}",
            "",
            "By Severity:",
            f"  🔴 Critical: {self.critical_count}",
            f"  🟠 High: {self.high_count}",
            f"  🟡 Medium: {self.medium_count}",
            f"  🟢 Low: {self.low_count}",
            "",
            "By Component:",
        ]
        
        for component, count in sorted(self.by_component.items()):
            lines.append(f"  {component}: {count}")
        
        return "\n".join(lines)


class BugDisplay:
    """Displays bug information from the bug tracker."""
    
    def __init__(self, db_path: Optional[str] = None):
        """Initialize the bug display.
        
        Args:
            db_path: Optional path to bugs database
        """
        self.db_path = db_path
        self.logger = structlog.get_logger()
        self._bug_tracker = None
    
    def _get_bug_tracker(self):
        """Get or create bug tracker instance."""
        if self._bug_tracker is None:
            try:
                from bridge.bug_tracker import BugTracker
                self._bug_tracker = BugTracker(db_path=self.db_path)
            except ImportError:
                self.logger.warning("BugTracker not available - bug display will be limited")
                return None
        return self._bug_tracker
    
    def get_unfixed_bugs(self) -> List[BugInfo]:
        """Get all unfixed bugs from the tracker.
        
        Returns:
            List of unfixed bugs
        """
        tracker = self._get_bug_tracker()
        if not tracker:
            # Try direct database access
            return self._get_bugs_from_db(unfixed_only=True)
        
        try:
            bugs = tracker.list_bugs()
            
            bug_infos = []
            for bug in bugs:
                # Convert to BugInfo
                severity = BugSeverity(bug.severity.value if hasattr(bug.severity, 'value') else bug.severity)
                status = BugStatus(bug.status.value if hasattr(bug.status, 'value') else bug.status)
                
                bug_info = BugInfo(
                    bug_id=bug.id,
                    title=bug.title,
                    severity=severity,
                    status=status,
                    component=bug.component,
                    description=bug.description,
                    timestamp=datetime.fromisoformat(bug.timestamp) if isinstance(bug.timestamp, str) else bug.timestamp,
                )
                
                if bug_info.is_unfixed:
                    bug_infos.append(bug_info)
            
            return bug_infos
            
        except Exception as e:
            self.logger.error("Failed to get bugs from tracker", error=str(e))
            return self._get_bugs_from_db(unfixed_only=True)
    
    def _get_bugs_from_db(self, unfixed_only: bool = True) -> List[BugInfo]:
        """Get bugs directly from database.
        
        Args:
            unfixed_only: Only return unfixed bugs
            
        Returns:
            List of bugs
        """
        from pathlib import Path
        
        # Default database path
        if not self.db_path:
            self.db_path = str(Path.home() / ".local" / "share" / "voice-bridge" / "bugs.db")
        
        db_path = Path(self.db_path)
        if not db_path.exists():
            self.logger.debug("No bugs database found", path=str(db_path))
            return []
        
        try:
            import sqlite3
            
            conn = sqlite3.connect(str(db_path))
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            
            if unfixed_only:
                query = """
                    SELECT * FROM bugs 
                    WHERE status NOT IN ('fixed', 'closed', 'duplicate')
                    ORDER BY 
                        CASE severity 
                            WHEN 'critical' THEN 1 
                            WHEN 'high' THEN 2 
                            WHEN 'medium' THEN 3 
                            WHEN 'low' THEN 4 
                            ELSE 5 
                        END,
                        timestamp DESC
                """
            else:
                query = "SELECT * FROM bugs ORDER BY timestamp DESC"
            
            cursor.execute(query)
            rows = cursor.fetchall()
            conn.close()
            
            bugs = []
            for row in rows:
                try:
                    bug_info = BugInfo(
                        bug_id=row["id"],
                        title=row["title"],
                        severity=BugSeverity(row["severity"]),
                        status=BugStatus(row["status"]),
                        component=row["component"],
                        description=row["description"],
                        timestamp=datetime.fromisoformat(row["timestamp"]),
                    )
                    bugs.append(bug_info)
                except (KeyError, ValueError) as e:
                    self.logger.warning("Could not parse bug row", error=str(e))
            
            return bugs
            
        except sqlite3.Error as e:
            self.logger.error("Database error", error=str(e))
            return []
        except Exception as e:
            self.logger.error("Failed to read bugs database", error=str(e))
            return []
    
    def get_bug_summary(self) -> BugSummary:
        """Get summary of all bugs.
        
        Returns:
            BugSummary with counts and details
        """
        all_bugs = self._get_bugs_from_db(unfixed_only=False)
        unfixed_bugs = [b for b in all_bugs if b.is_unfixed]
        
        critical_count = sum(1 for b in unfixed_bugs if b.severity == BugSeverity.CRITICAL)
        high_count = sum(1 for b in unfixed_bugs if b.severity == BugSeverity.HIGH)
        medium_count = sum(1 for b in unfixed_bugs if b.severity == BugSeverity.MEDIUM)
        low_count = sum(1 for b in unfixed_bugs if b.severity == BugSeverity.LOW)
        
        by_component = {}
        for bug in unfixed_bugs:
            by_component[bug.component] = by_component.get(bug.component, 0) + 1
        
        return BugSummary(
            total_bugs=len(all_bugs),
            unfixed_count=len(unfixed_bugs),
            critical_count=critical_count,
            high_count=high_count,
            medium_count=medium_count,
            low_count=low_count,
            by_component=by_component,
            bugs=unfixed_bugs,
        )
    
    def format_bug_list(self, bugs: List[BugInfo], max_display: int = 10) -> str:
        """Format a list of bugs for display.
        
        Args:
            bugs: List of bugs to format
            max_display: Maximum number of bugs to show
            
        Returns:
            Formatted string
        """
        if not bugs:
            return "No bugs found."
        
        lines = []
        lines.append(f"Found {len(bugs)} unfixed bug(s):")
        lines.append("")
        
        for i, bug in enumerate(bugs[:max_display]):
            lines.append(
                f"{bug.severity_icon} {bug.status_icon} "
                f"Bug #{bug.bug_id}: {bug.title}"
            )
            lines.append(f"    Component: {bug.component}")
            lines.append(f"    Severity: {bug.severity.value}, Status: {bug.status.value}")
            lines.append("")
        
        if len(bugs) > max_display:
            lines.append(f"... and {len(bugs) - max_display} more")
        
        return "\n".join(lines)
    
    def display_summary(self) -> str:
        """Generate a summary for display during installation.
        
        Returns:
            Formatted summary string
        """
        summary = self.get_bug_summary()
        
        if summary.is_clean:
            return "✅ No known bugs - System is healthy"
        
        lines = [
            "⚠️ Warning: Known issues detected",
            "",
            summary.summary_line(),
            "",
        ]
        
        if summary.has_critical:
            lines.append("🔴 CRITICAL bugs present:")
            for bug in summary.bugs:
                if bug.severity == BugSeverity.CRITICAL:
                    lines.append(f"   #{bug.bug_id}: {bug.title}")
            lines.append("")
        
        if summary.has_high:
            lines.append("🟠 HIGH priority bugs:")
            for bug in summary.bugs:
                if bug.severity == BugSeverity.HIGH:
                    lines.append(f"   #{bug.bug_id}: {bug.title}")
            lines.append("")
        
        lines.append("Run 'python -m bridge.bug_cli list' for details")
        
        return "\n".join(lines)
    
    def should_warn_user(self) -> bool:
        """Check if user should be warned about bugs.
        
        Returns:
            True if there are significant unfixed bugs
        """
        summary = self.get_bug_summary()
        
        # Warn if critical or high bugs exist
        if summary.has_critical or summary.has_high:
            return True
        
        # Warn if more than 5 medium bugs
        if summary.medium_count > 5:
            return True
        
        return False
    
    def get_blocking_bugs(self) -> List[BugInfo]:
        """Get bugs that should block installation.
        
        Returns:
            List of critical bugs that are unfixed
        """
        unfixed = self.get_unfixed_bugs()
        return [b for b in unfixed if b.severity == BugSeverity.CRITICAL]


def show_unfixed_bugs(db_path: Optional[str] = None) -> str:
    """Convenience function to show unfixed bugs.
    
    Args:
        db_path: Optional database path
        
    Returns:
        Formatted bug list
    """
    display = BugDisplay(db_path=db_path)
    return display.display_summary()


def get_bug_summary(db_path: Optional[str] = None) -> BugSummary:
    """Convenience function to get bug summary.
    
    Args:
        db_path: Optional database path
        
    Returns:
        BugSummary object
    """
    display = BugDisplay(db_path=db_path)
    return display.get_bug_summary()