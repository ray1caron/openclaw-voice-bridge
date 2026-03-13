"""
Automated bug tracking and error collection system.

Captures errors, system state, and context for debugging.
Stores locally in SQLite for privacy and offline use.
"""

from __future__ import annotations

import json
import queue
import sqlite3
import sys
import threading
import time
import traceback
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, Dict, Any, List
from dataclasses import dataclass, asdict
from enum import Enum

import structlog
import platform

logger = structlog.get_logger()


class BugSeverity(Enum):
    """Bug severity levels."""
    CRITICAL = "critical"      # Crash, data loss, security
    HIGH = "high"              # Feature broken, bad UX
    MEDIUM = "medium"          # Annoyance, workaround exists
    LOW = "low"                # Cosmetic, minor
    INFO = "info"              # For telemetry


class BugStatus(Enum):
    """Bug tracking status."""
    NEW = "new"
    TRIAGED = "triaged"
    IN_PROGRESS = "in_progress"
    FIXED = "fixed"
    CLOSED = "closed"
    DUPLICATE = "duplicate"


@dataclass
class SystemSnapshot:
    """System state at time of error."""
    timestamp: str
    python_version: str
    platform: str
    platform_version: str
    cpu_count: int
    memory_available: Optional[int]
    disk_free: Optional[int]
    audio_devices: Optional[List[Dict]]
    config_hash: Optional[str]
    session_id: Optional[str]
    uptime_seconds: Optional[float]

    @classmethod
    def capture(cls, config=None, session_id=None) -> SystemSnapshot:
        """Capture current system state."""
        import psutil

        # Get audio devices if available
        audio_devices = None
        try:
            import sounddevice as sd
            devices = sd.query_devices()
            audio_devices = [
                {"name": d.get("name"), "channels": d.get("max_input_channels", 0)}
                for d in devices
            ]
        except Exception:
            pass

        # Config hash for detecting config-related bugs
        config_hash = None
        if config:
            try:
                import hashlib
                config_str = json.dumps(config.model_dump(), sort_keys=True)
                config_hash = hashlib.md5(config_str.encode()).hexdigest()[:8]
            except Exception:
                pass

        # Uptime if tracker has start time
        uptime = None
        if hasattr(BugTracker, '_start_time') and BugTracker._start_time:
            uptime = (datetime.now() - BugTracker._start_time).total_seconds()

        return cls(
            timestamp=datetime.now().isoformat(),
            python_version=sys.version,
            platform=platform.system(),
            platform_version=platform.version(),
            cpu_count=psutil.cpu_count(),
            memory_available=psutil.virtual_memory().available if hasattr(psutil, 'virtual_memory') else None,
            disk_free=psutil.disk_usage('/').free if hasattr(psutil, 'disk_usage') else None,
            audio_devices=audio_devices,
            config_hash=config_hash,
            session_id=session_id,
            uptime_seconds=uptime,
        )


@dataclass
class BugReport:
    """A captured bug report."""
    id: Optional[int]
    timestamp: str
    severity: str
    component: str
    title: str
    description: str
    stack_trace: Optional[str]
    system_state: Dict
    user_context: Optional[str]
    status: str
    created_at: str
    updated_at: str

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return asdict(self)


class BugTracker:
    """
    Automated bug tracking system.

    Captures errors with full context and stores them locally in SQLite.
    Includes deduplication to prevent flooding the database with repeated
    identical errors, and emits console alerts for CRITICAL / HIGH severity.
    """

    _instance: Optional[BugTracker] = None
    _start_time: Optional[datetime] = None

    # Time window for deduplication (same component + title → skip new report)
    DEDUP_WINDOW_MINUTES: int = 5

    def __init__(self, db_path: Optional[Path] = None):
        """
        Initialize bug tracker.

        Args:
            db_path: Path to SQLite database (default: ~/.voice-bridge/bugs.db)
        """
        if db_path is None:
            db_path = Path.home() / ".voice-bridge" / "bugs.db"

        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

        self._init_db()

        BugTracker._start_time = datetime.now()

        # Background writer for non-blocking event recording.
        # Events are queued and flushed by a daemon thread so that
        # hot paths (state transitions, audio callbacks) never block on DB I/O.
        self._event_queue: queue.Queue = queue.Queue()
        self._event_writer_thread = threading.Thread(
            target=self._event_writer_loop,
            name="bug-tracker-event-writer",
            daemon=True,
        )
        self._event_writer_thread.start()

        logger.info(
            "bug_tracker_initialized",
            db_path=str(self.db_path),
        )

    @classmethod
    def get_instance(cls) -> BugTracker:
        """Get or create singleton instance."""
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def _init_db(self) -> None:
        """Initialize SQLite database schema (bugs + events tables)."""
        with sqlite3.connect(self.db_path) as conn:
            # Enable WAL for better concurrency and crash safety
            conn.execute("PRAGMA journal_mode=WAL")

            conn.execute("""
                CREATE TABLE IF NOT EXISTS bugs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    severity TEXT NOT NULL,
                    component TEXT NOT NULL,
                    title TEXT NOT NULL,
                    description TEXT NOT NULL,
                    stack_trace TEXT,
                    system_state TEXT NOT NULL,
                    user_context TEXT,
                    status TEXT NOT NULL DEFAULT 'new',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
            """)

            # Diagnostic events table — records state transitions, timeouts,
            # interactive mode lifecycle, and HTTP request outcomes so that
            # lockups and unexpected behaviour can be replayed from the DB
            # rather than guessed from logs.
            conn.execute("""
                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    component TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    from_state TEXT,
                    to_state TEXT,
                    duration_ms REAL,
                    trigger TEXT,
                    metadata TEXT,
                    session_uptime_ms REAL
                )
            """)

            # Indexes on bugs
            conn.execute("CREATE INDEX IF NOT EXISTS idx_severity ON bugs(severity)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_status ON bugs(status)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_component ON bugs(component)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_timestamp ON bugs(timestamp)")

            # Indexes on events
            conn.execute("CREATE INDEX IF NOT EXISTS idx_events_timestamp ON events(timestamp)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_events_component ON events(component)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_events_type ON events(event_type)")

            conn.commit()

    # ------------------------------------------------------------------
    # Deduplication
    # ------------------------------------------------------------------

    def _find_recent_duplicate(self, component: str, title: str) -> Optional[int]:
        """
        Check whether an identical report was filed within the dedup window.

        Returns the existing bug ID if a duplicate exists, else None.
        """
        cutoff = (
            datetime.now() - timedelta(minutes=self.DEDUP_WINDOW_MINUTES)
        ).isoformat()

        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                "SELECT id FROM bugs WHERE component = ? AND title = ? AND timestamp > ? LIMIT 1",
                (component, title[:100], cutoff),
            )
            row = cursor.fetchone()
            return row[0] if row else None

    # ------------------------------------------------------------------
    # Diagnostic event recording
    # ------------------------------------------------------------------

    def record_event(
        self,
        component: str,
        event_type: str,
        from_state: Optional[str] = None,
        to_state: Optional[str] = None,
        duration_ms: Optional[float] = None,
        trigger: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        """
        Record a diagnostic event to the events table.

        Non-blocking: the event is queued and written by a background thread
        so that hot paths (state transitions, audio callbacks) are never
        delayed by database I/O.

        Useful event_type values:
          state_change        - orchestrator state machine transition
          interactive_enter   - entered interactive conversation mode
          interactive_exit    - exited interactive mode (see trigger for reason)
          idle_timeout        - idle timer fired
          cancel_phrase       - user said a cancel phrase
          ack_timeout         - wake word ack timed out
          http_request        - HTTP request to OpenClaw
          http_timeout        - HTTP request timed out
          stt_complete        - speech-to-text finished
          tts_complete        - text-to-speech playback finished
          barge_in            - user interrupted TTS playback
          audio_error         - audio pipeline error

        Args:
            component:   Source component (e.g. "orchestrator", "http_client")
            event_type:  Category of event (see above)
            from_state:  Previous state, if applicable
            to_state:    New state, if applicable
            duration_ms: Time spent in previous state / request duration
            trigger:     What caused the event (e.g. "wake_word", "idle_timeout")
            metadata:    Arbitrary JSON-serialisable dict with extra context
        """
        uptime_ms: Optional[float] = None
        if BugTracker._start_time:
            uptime_ms = (datetime.now() - BugTracker._start_time).total_seconds() * 1000

        self._event_queue.put({
            "timestamp": datetime.now().isoformat(),
            "component": component,
            "event_type": event_type,
            "from_state": from_state,
            "to_state": to_state,
            "duration_ms": duration_ms,
            "trigger": trigger,
            "metadata": json.dumps(metadata) if metadata else None,
            "session_uptime_ms": uptime_ms,
        })

    def _event_writer_loop(self) -> None:
        """Background thread: drain the event queue and write to SQLite."""
        while True:
            try:
                event = self._event_queue.get()
                if event is None:
                    break  # Sentinel: shut down

                with sqlite3.connect(self.db_path) as conn:
                    conn.execute(
                        """INSERT INTO events
                           (timestamp, component, event_type, from_state, to_state,
                            duration_ms, trigger, metadata, session_uptime_ms)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (
                            event["timestamp"],
                            event["component"],
                            event["event_type"],
                            event["from_state"],
                            event["to_state"],
                            event["duration_ms"],
                            event["trigger"],
                            event["metadata"],
                            event["session_uptime_ms"],
                        ),
                    )
                    conn.commit()
            except Exception as e:
                # Never crash the writer thread — just log and continue
                logger.debug("event_writer_error", error=str(e))

    def get_recent_events(
        self,
        component: Optional[str] = None,
        event_type: Optional[str] = None,
        limit: int = 200,
    ) -> List[Dict[str, Any]]:
        """
        Return recent diagnostic events for debugging.

        Args:
            component:  Filter by component name (None = all)
            event_type: Filter by event type (None = all)
            limit:      Maximum rows to return (most-recent first)

        Returns:
            List of event dicts with keys matching the events table columns.
        """
        query = "SELECT * FROM events WHERE 1=1"
        params: list = []

        if component:
            query += " AND component = ?"
            params.append(component)
        if event_type:
            query += " AND event_type = ?"
            params.append(event_type)

        query += " ORDER BY timestamp DESC LIMIT ?"
        params.append(limit)

        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute(query, params)
            rows = cursor.fetchall()

        return [dict(row) for row in rows]

    def get_state_history(self, limit: int = 50) -> List[Dict[str, Any]]:
        """
        Return recent orchestrator state transitions for lockup diagnosis.

        Convenience wrapper around get_recent_events() focused on
        state_change events so callers don't have to specify filters.
        """
        return self.get_recent_events(
            component="orchestrator",
            event_type="state_change",
            limit=limit,
        )

    # ------------------------------------------------------------------
    # Core capture
    # ------------------------------------------------------------------

    def capture_error(
        self,
        error: Exception,
        component: str,
        severity: BugSeverity = BugSeverity.HIGH,
        title: Optional[str] = None,
        user_context: Optional[str] = None,
        config=None,
        session_id=None,
    ) -> int:
        """
        Capture an error with full context.

        Deduplication: if an identical (component + title) report was filed
        within DEDUP_WINDOW_MINUTES, the existing ID is returned and no new
        row is inserted.

        Args:
            error: The exception that occurred
            component: Component where error occurred (e.g., "audio", "stt")
            severity: Bug severity level
            title: Optional custom title (defaults to error message)
            user_context: Additional context from user
            config: Current configuration (for hash)
            session_id: Current session ID

        Returns:
            Bug report ID (new or existing duplicate)
        """
        bug_title = (title if title else str(error))[:100]

        # Deduplication check
        existing_id = self._find_recent_duplicate(component, bug_title)
        if existing_id is not None:
            logger.debug(
                "bug_deduplicated",
                existing_id=existing_id,
                component=component,
                title=bug_title,
            )
            return existing_id

        # Capture stack trace
        stack_trace = "".join(
            traceback.format_exception(type(error), error, error.__traceback__)
        )

        # Capture system state
        system_state = SystemSnapshot.capture(config, session_id)

        # Create bug report
        now = datetime.now().isoformat()
        bug = BugReport(
            id=None,
            timestamp=now,
            severity=severity.value,
            component=component,
            title=bug_title,
            description=f"{type(error).__name__}: {str(error)}",
            stack_trace=stack_trace,
            system_state=system_state.__dict__,
            user_context=user_context,
            status=BugStatus.NEW.value,
            created_at=now,
            updated_at=now,
        )

        # Save to database
        bug_id = self._save_bug(bug)

        logger.error(
            "bug_captured",
            bug_id=bug_id,
            severity=severity.value,
            component=component,
            error_type=type(error).__name__,
            error_message=str(error)[:200],
        )

        # Console alert for actionable severities so operators notice
        # immediately without needing to query the database.
        if severity in (BugSeverity.CRITICAL, BugSeverity.HIGH):
            print(
                f"\n[BUG TRACKER] {severity.value.upper()} #{bug_id} "
                f"in '{component}': {bug_title}",
                file=sys.stderr,
                flush=True,
            )

        return bug_id

    def _save_bug(self, bug: BugReport) -> int:
        """Save bug to database."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute("""
                INSERT INTO bugs (
                    timestamp, severity, component, title, description,
                    stack_trace, system_state, user_context, status,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                bug.timestamp,
                bug.severity,
                bug.component,
                bug.title,
                bug.description,
                bug.stack_trace,
                json.dumps(bug.system_state),
                bug.user_context,
                bug.status,
                bug.created_at,
                bug.updated_at,
            ))
            conn.commit()
            return cursor.lastrowid

    def get_bug(self, bug_id: int) -> Optional[BugReport]:
        """Get a bug report by ID."""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute("SELECT * FROM bugs WHERE id = ?", (bug_id,))
            row = cursor.fetchone()

            if row:
                return BugReport(
                    id=row["id"],
                    timestamp=row["timestamp"],
                    severity=row["severity"],
                    component=row["component"],
                    title=row["title"],
                    description=row["description"],
                    stack_trace=row["stack_trace"],
                    system_state=json.loads(row["system_state"]),
                    user_context=row["user_context"],
                    status=row["status"],
                    created_at=row["created_at"],
                    updated_at=row["updated_at"],
                )
            return None

    def list_bugs(
        self,
        status: Optional[BugStatus] = None,
        severity: Optional[BugSeverity] = None,
        component: Optional[str] = None,
        limit: int = 100,
    ) -> List[BugReport]:
        """List bugs with optional filtering."""
        query = "SELECT * FROM bugs WHERE 1=1"
        params = []

        if status:
            query += " AND status = ?"
            params.append(status.value)
        if severity:
            query += " AND severity = ?"
            params.append(severity.value)
        if component:
            query += " AND component = ?"
            params.append(component)

        query += " ORDER BY timestamp DESC LIMIT ?"
        params.append(limit)

        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute(query, params)
            rows = cursor.fetchall()

            bugs = []
            for row in rows:
                bugs.append(BugReport(
                    id=row["id"],
                    timestamp=row["timestamp"],
                    severity=row["severity"],
                    component=row["component"],
                    title=row["title"],
                    description=row["description"],
                    stack_trace=row["stack_trace"],
                    system_state=json.loads(row["system_state"]),
                    user_context=row["user_context"],
                    status=row["status"],
                    created_at=row["created_at"],
                    updated_at=row["updated_at"],
                ))
            return bugs

    def update_status(self, bug_id: int, status: BugStatus) -> bool:
        """Update bug status."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                "UPDATE bugs SET status = ?, updated_at = ? WHERE id = ?",
                (status.value, datetime.now().isoformat(), bug_id)
            )
            conn.commit()
            return cursor.rowcount > 0

    def export_to_file(self, output_path: Path, format: str = "json") -> None:
        """Export all bugs to file."""
        bugs = self.list_bugs(limit=10000)

        if format == "json":
            with open(output_path, "w") as f:
                json.dump([b.to_dict() for b in bugs], f, indent=2)
        elif format == "markdown":
            with open(output_path, "w") as f:
                f.write("# Bug Reports\n\n")
                for bug in bugs:
                    f.write(f"## #{bug.id}: {bug.title}\n\n")
                    f.write(f"- **Severity:** {bug.severity}\n")
                    f.write(f"- **Component:** {bug.component}\n")
                    f.write(f"- **Status:** {bug.status}\n")
                    f.write(f"- **Timestamp:** {bug.timestamp}\n\n")
                    f.write(f"**Description:**\n{bug.description}\n\n")
                    f.write(
                        f"**System State:**\n```json\n"
                        f"{json.dumps(bug.system_state, indent=2)}\n```\n\n"
                    )
                    f.write("---\n\n")

    def get_stats(self) -> Dict[str, Any]:
        """Get bug statistics."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute("""
                SELECT
                    COUNT(*) as total,
                    SUM(CASE WHEN status = 'new' THEN 1 ELSE 0 END) as new,
                    SUM(CASE WHEN status = 'fixed' THEN 1 ELSE 0 END) as fixed,
                    SUM(CASE WHEN severity = 'critical' THEN 1 ELSE 0 END) as critical
                FROM bugs
            """)
            row = cursor.fetchone()
            return {
                "total": row[0],
                "new": row[1],
                "fixed": row[2],
                "critical": row[3],
            }

    # ------------------------------------------------------------------
    # Public API (kept for callers that used capture_exception)
    # ------------------------------------------------------------------

    def capture_exception(
        self,
        exception: Exception,
        severity: BugSeverity,
        component: str,
        title: str,
        user_context: Optional[str] = None,
        context: Optional[Dict] = None,
    ) -> int:
        """
        Public wrapper to capture an exception as a bug.

        Args:
            exception: The exception that occurred
            severity: Bug severity level
            component: Component where error occurred
            title: Bug title
            user_context: Additional context from user
            context: Additional context dict (may include session_id)

        Returns:
            Bug report ID
        """
        session_id = context.get("session_id") if context else None
        return self.capture_error(
            error=exception,
            component=component,
            severity=severity,
            title=title,
            user_context=user_context,
            session_id=session_id,
        )


# ------------------------------------------------------------------
# Global exception handler
# ------------------------------------------------------------------

def install_global_handler(tracker: Optional[BugTracker] = None):
    """Install global exception handler to auto-capture uncaught errors."""
    if tracker is None:
        tracker = BugTracker.get_instance()

    original_hook = sys.excepthook

    def exception_handler(exc_type, exc_value, exc_traceback):
        """Handle uncaught exceptions."""
        try:
            tracker.capture_error(
                error=exc_value,
                component="uncaught",
                severity=BugSeverity.CRITICAL,
                user_context="Uncaught exception - see stack trace",
            )
        except Exception as e:
            logger.error("failed_to_capture_bug", error=str(e))

        original_hook(exc_type, exc_value, exc_traceback)

    sys.excepthook = exception_handler

    logger.info("global_exception_handler_installed")


# ------------------------------------------------------------------
# Convenience function
# ------------------------------------------------------------------

def capture_bug(
    error: Exception,
    component: str,
    severity: BugSeverity = BugSeverity.HIGH,
    **kwargs
) -> int:
    """Quick function to capture a bug."""
    tracker = BugTracker.get_instance()
    return tracker.capture_error(error, component, severity, **kwargs)
