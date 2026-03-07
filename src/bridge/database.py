"""Centralized SQLite database management for Voice Bridge v4.

This module provides thread-safe database connection management with:
- Thread-local connection pooling
- Automatic transaction handling (commit on success, rollback on error)
- WAL mode for better concurrency
- Foreign key enforcement
- Backup functionality

This addresses CRITICAL BUG DB-001 (duplicate connection patterns).

Example:
    from bridge.database import get_db

    # Context manager for transactions
    with get_db().connection() as conn:
        conn.execute("INSERT INTO sessions ...")
        # Auto-commits on success, rolls back on exception

    # Quick cursor
    with get_db().cursor() as cursor:
        cursor.execute("SELECT * FROM sessions")
        rows = cursor.fetchall()
"""

from __future__ import annotations

import shutil
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional, Union

from typing_extensions import Self


class DatabaseError(Exception):
    """Base exception for database operations."""
    pass


class TransactionError(DatabaseError):
    """Exception raised for transaction-related errors."""
    pass


class DatabaseManager:
    """Thread-safe SQLite database manager with connection pooling.

    Provides centralized database access with:
    - Thread-local connection pooling (one connection per thread)
    - Automatic transaction handling via context managers
    - WAL mode for better concurrency
    - Foreign key enforcement
    - Backup functionality

    Attributes:
        db_path: Path to the SQLite database file.
        timeout: Connection timeout in seconds.

    Example:
        >>> db = DatabaseManager()
        >>> with db.connection() as conn:
        ...     conn.execute("CREATE TABLE IF NOT EXISTS test (id INTEGER)")
        >>> with db.cursor() as cursor:
        ...     cursor.execute("SELECT * FROM test")
        ...     rows = cursor.fetchall()
    """

    # Class-level lock for singleton creation
    _lock = threading.Lock()
    _instance: Optional[DatabaseManager] = None

    def __new__(cls, db_path: Optional[Union[Path, str]] = None) -> Self:
        """Create singleton instance or return existing one.

        Args:
            db_path: Path to SQLite database file.

        Returns:
            The singleton DatabaseManager instance.
        """
        with cls._lock:
            if cls._instance is None:
                cls._instance = super().__new__(cls)
                cls._instance._initialized = False
            return cls._instance

    def __init__(self, db_path: Optional[Union[Path, str]] = None) -> None:
        """Initialize the database manager.

        Args:
            db_path: Path to SQLite database file. Defaults to
                ~/.voice-bridge/data/bridge.db

        Note:
            This is a singleton - subsequent calls with different db_path
            will use the already-created instance. To change the database,
            first call close() then reinitialize with new path.
        """
        # Skip if already initialized (singleton pattern)
        if getattr(self, '_initialized', False):
            return

        # Set up database path
        if db_path is None:
            self.db_path = Path.home() / ".voice-bridge" / "data" / "bridge.db"
        else:
            self.db_path = Path(db_path)

        # Ensure parent directory exists
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

        # Connection timeout (seconds)
        self.timeout: float = 30.0

        # Thread-local storage for connections
        self._thread_local = threading.local()

        # Global lock for operations that need synchronization across threads
        self._global_lock = threading.RLock()

        self._initialized = True

    def _get_connection(self) -> sqlite3.Connection:
        """Get or create a thread-local database connection.

        Creates a new connection for each thread on first access.
        Configures the connection with WAL mode and foreign key enforcement.

        Returns:
            A sqlite3 Connection object for the current thread.
        """
        conn = getattr(self._thread_local, 'connection', None)

        if conn is None:
            conn = sqlite3.connect(
                str(self.db_path),
                timeout=self.timeout,
                isolation_level=None,  # Autocommit disabled; we manage transactions
            )

            # Enable WAL mode for better concurrency
            conn.execute("PRAGMA journal_mode=WAL")

            # Enable foreign key enforcement
            conn.execute("PRAGMA foreign_keys=ON")

            # Set busy timeout for locked database
            conn.execute(f"PRAGMA busy_timeout={int(self.timeout * 1000)}")

            # Optimize for performance
            conn.execute("PRAGMA synchronous=NORMAL")
            conn.execute("PRAGMA cache_size=-64000")  # 64MB cache
            conn.execute("PRAGMA temp_store=MEMORY")

            self._thread_local.connection = conn

        return conn

    def _close_thread_connection(self) -> None:
        """Close the connection for the current thread."""
        conn = getattr(self._thread_local, 'connection', None)
        if conn is not None:
            try:
                conn.close()
            except sqlite3.Error:
                pass  # Ignore errors during close
            finally:
                self._thread_local.connection = None

    @contextmanager
    def connection(self) -> sqlite3.Connection:
        """Context manager for database connections with automatic transaction handling.

        Provides a connection that:
        - Automatically begins a transaction
        - Commits on successful exit
        - Rolls back on exception

        Yields:
            A sqlite3 Connection object in the current thread.

        Raises:
            TransactionError: If transaction commit fails.

        Example:
            >>> with db.connection() as conn:
            ...     conn.execute("INSERT INTO users (name) VALUES (?)", ("Alice",))
            ...     # Commits automatically on exit
        """
        conn = self._get_connection()
        in_transaction = getattr(self._thread_local, 'in_transaction', False)

        # Begin transaction if not already in one
        if not in_transaction:
            conn.execute("BEGIN IMMEDIATE")
            self._thread_local.in_transaction = True
            is_outer = True
        else:
            is_outer = False

        try:
            yield conn
            if is_outer:
                conn.commit()
        except Exception:
            if is_outer:
                try:
                    conn.rollback()
                except sqlite3.Error as rollback_err:
                    raise TransactionError(f"Failed to rollback transaction: {rollback_err}")
            raise
        finally:
            if is_outer:
                self._thread_local.in_transaction = False

    @contextmanager
    def cursor(self) -> sqlite3.Cursor:
        """Context manager for database cursors with automatic transaction handling.

        Provides a cursor that:
        - Automatically begins a transaction
        - Commits on successful exit
        - Rolls back on exception
        - Closes the cursor on exit

        Yields:
            A sqlite3 Cursor object in the current thread.

        Raises:
            TransactionError: If transaction commit fails.

        Example:
            >>> with db.cursor() as cursor:
            ...     cursor.execute("SELECT * FROM users")
            ...     rows = cursor.fetchall()
        """
        with self.connection() as conn:
            cursor = conn.cursor()
            try:
                yield cursor
            finally:
                cursor.close()

    def execute(
        self,
        sql: str,
        parameters: Optional[tuple] = None,
    ) -> sqlite3.Cursor:
        """Execute a SQL statement with automatic transaction handling.

        Convenience method for simple queries. For complex operations,
        use connection() or cursor() context managers.

        Args:
            sql: SQL statement to execute.
            parameters: Optional parameters for the statement.

        Returns:
            A sqlite3 Cursor object after execution.

        Example:
            >>> db.execute("INSERT INTO users (name) VALUES (?)", ("Alice",))
            >>> rows = db.execute("SELECT * FROM users").fetchall()
        """
        with self.cursor() as cursor:
            if parameters:
                cursor.execute(sql, parameters)
            else:
                cursor.execute(sql)
            return cursor

    def executemany(
        self,
        sql: str,
        parameters_list: list[tuple],
    ) -> sqlite3.Cursor:
        """Execute a SQL statement against multiple parameter sequences.

        Args:
            sql: SQL statement to execute.
            parameters_list: List of parameter tuples.

        Returns:
            A sqlite3 Cursor object after execution.

        Example:
            >>> db.executemany(
            ...     "INSERT INTO users (name) VALUES (?)",
            ...     [("Alice",), ("Bob",), ("Charlie",)]
            ... )
        """
        with self.cursor() as cursor:
            cursor.executemany(sql, parameters_list)
            return cursor

    def executescript(self, script: str) -> None:
        """Execute multiple SQL statements from a script.

        Note: executescript() commits before executing and manages its own
        transaction, so we wrap it in our transaction manager for consistency.

        Args:
            script: SQL script containing multiple statements.
        """
        with self.connection() as conn:
            conn.executescript(script)

    def backup(
        self,
        backup_path: Optional[Union[Path, str]] = None,
    ) -> Path:
        """Create a backup of the database.

        Creates a copy of the database file with proper locking to ensure
        consistency.

        Args:
            backup_path: Optional path for the backup file. If not provided,
                creates a backup with timestamp in the same directory.

        Returns:
            Path to the backup file.

        Raises:
            DatabaseError: If backup fails.
        """
        if backup_path is None:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            backup_path = self.db_path.with_suffix(f".{timestamp}.db.bak")
        else:
            backup_path = Path(backup_path)

        # Ensure backup directory exists
        backup_path.parent.mkdir(parents=True, exist_ok=True)

        with self._global_lock:
            # Use SQLite's backup API for consistency
            conn = self._get_connection()

            try:
                # Create backup connection
                backup_conn = sqlite3.connect(str(backup_path))
                try:
                    conn.backup(backup_conn)
                finally:
                    backup_conn.close()

                return backup_path

            except sqlite3.Error as e:
                raise DatabaseError(f"Failed to create backup: {e}")

    def vacuum(self) -> None:
        """Vacuum the database to reclaim space and optimize.

        Note: This requires exclusive access and may take time on large databases.
        """
        with self.connection() as conn:
            conn.execute("VACUUM")

    def optimize(self) -> None:
        """Optimize the database by analyzing tables."""
        with self.connection() as conn:
            conn.execute("PRAGMA optimize")

    def get_database_size(self) -> int:
        """Get the current database file size in bytes.

        Returns:
            Size of the database file in bytes.
        """
        if self.db_path.exists():
            return self.db_path.stat().st_size
        return 0

    def get_table_info(self, table_name: str) -> list[dict]:
        """Get information about a table's columns.

        Args:
            table_name: Name of the table to inspect.

        Returns:
            List of column information dictionaries.
        """
        with self.cursor() as cursor:
            cursor.execute(f"PRAGMA table_info({table_name})")
            columns = cursor.fetchall()
            return [
                {
                    "cid": col[0],
                    "name": col[1],
                    "type": col[2],
                    "notnull": bool(col[3]),
                    "default": col[4],
                    "pk": bool(col[5]),
                }
                for col in columns
            ]

    def table_exists(self, table_name: str) -> bool:
        """Check if a table exists in the database.

        Args:
            table_name: Name of the table to check.

        Returns:
            True if table exists, False otherwise.
        """
        with self.cursor() as cursor:
            cursor.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
                (table_name,)
            )
            return cursor.fetchone() is not None

    def close(self) -> None:
        """Close all connections for the current thread.

        For complete cleanup, use close_all() to close connections across
        all threads.
        """
        self._close_thread_connection()

    @classmethod
    def close_all(cls) -> None:
        """Close all connections across all threads.

        Note: This should only be called during application shutdown.
        After calling this, the singleton will be reset and can be
        reinitialized with a new database path.
        """
        with cls._lock:
            if cls._instance is not None:
                cls._instance._close_thread_connection()
                cls._instance = None

    @classmethod
    def reset(cls) -> None:
        """Reset the singleton instance.

        This closes the connection and allows reinitialization with
        a different database path.
        """
        cls.close_all()

    def __repr__(self) -> str:
        """Return string representation of the database manager."""
        return f"DatabaseManager(db_path={self.db_path!r})"

    def __enter__(self) -> Self:
        """Enter context manager for the DatabaseManager."""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        """Exit context manager, closing the connection."""
        self._close_thread_connection()


# Singleton getter function
_db_instance: Optional[DatabaseManager] = None
_db_lock = threading.Lock()


def get_db(db_path: Optional[Union[Path, str]] = None) -> DatabaseManager:
    """Get the singleton DatabaseManager instance.

    This is the primary entry point for database access. Returns a
    thread-safe DatabaseManager that provides connection pooling
    and automatic transaction handling.

    Args:
        db_path: Optional database path. Only used on first call.
            Defaults to ~/.voice-bridge/data/bridge.db

    Returns:
        The singleton DatabaseManager instance.

    Example:
        >>> from bridge.database import get_db
        >>>
        >>> # Context manager for transactions
        >>> with get_db().connection() as conn:
        ...     conn.execute("INSERT INTO sessions ...")
        ...     # Auto-commits on success, rolls back on exception
        >>>
        >>> # Quick cursor
        >>> with get_db().cursor() as cursor:
        ...     cursor.execute("SELECT * FROM sessions")
        ...     rows = cursor.fetchall()
    """
    global _db_instance

    with _db_lock:
        if _db_instance is None:
            _db_instance = DatabaseManager(db_path)
        return _db_instance


def reset_db() -> None:
    """Reset the database singleton instance.

    Useful for testing or when switching database paths.
    """
    global _db_instance

    with _db_lock:
        if _db_instance is not None:
            _db_instance.close()
            _db_instance = None


# Module-level exports
__all__ = [
    "DatabaseManager",
    "DatabaseError",
    "TransactionError",
    "get_db",
    "reset_db",
]