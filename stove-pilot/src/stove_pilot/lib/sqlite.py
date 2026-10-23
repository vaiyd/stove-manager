"""SQLite transport: serialized connections in WAL mode and idempotent schema migrations."""

import logging
import sqlite3
import threading
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path

_LOGGER = logging.getLogger(__name__)

MigrationStep = Callable[[sqlite3.Connection], None]

MEMORY_PATH = ":memory:"
_BUSY_TIMEOUT_S = 10.0
_VERSIONS_DDL = (
    "CREATE TABLE IF NOT EXISTS schema_versions ("
    "name TEXT PRIMARY KEY, version INTEGER NOT NULL)"
)


class SqliteDatabase:
    """One SQLite file shared by several threads, with every access serialized by a lock.

    A file database opens a short-lived connection per ``connection()`` call. A ``:memory:``
    database lives and dies with its connection, so one shared connection is kept for the
    whole lifetime of the object instead (``check_same_thread=False``; the lock serializes it).
    """

    def __init__(self, path: Path | str) -> None:
        """Prepare the database location (the file is created on first use).

        :param path: Database file path, or ``:memory:``; parent directories are created.
        """
        self._in_memory = str(path) == MEMORY_PATH
        self._path = MEMORY_PATH if self._in_memory else str(Path(path))
        if not self._in_memory:
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._shared: sqlite3.Connection | None = None

    @property
    def path(self) -> str:
        """Return the database path.

        :return: File path or ``:memory:``.
        """
        return self._path

    def _open(self) -> sqlite3.Connection:
        # Reason: isolation_level=None lets us issue explicit BEGIN/COMMIT, so DDL is transactional.
        conn = sqlite3.connect(
            self._path,
            timeout=_BUSY_TIMEOUT_S,
            isolation_level=None,
            check_same_thread=not self._in_memory,
        )
        conn.row_factory = sqlite3.Row
        if not self._in_memory:
            conn.execute("PRAGMA journal_mode=WAL")
        return conn

    @contextmanager
    def connection(self, *, immediate: bool = False) -> Iterator[sqlite3.Connection]:
        """Yield a connection inside one transaction (commit on success, rollback on error).

        Do not nest calls: the lock is not reentrant.

        :param immediate: Take the write lock at ``BEGIN`` (read-then-write across processes).
        :return: Context manager yielding the connection (rows are ``sqlite3.Row``).
        """
        with self._lock:
            if self._in_memory:
                if self._shared is None:
                    self._shared = self._open()
                conn = self._shared
            else:
                conn = self._open()
            try:
                conn.execute("BEGIN IMMEDIATE" if immediate else "BEGIN")
                try:
                    yield conn
                except BaseException:
                    conn.execute("ROLLBACK")
                    raise
                conn.execute("COMMIT")
            finally:
                if not self._in_memory:
                    conn.close()

    def migrate(self, name: str, version: int, statements: Sequence[str]) -> bool:
        """Apply a schema once: run the statements only if the stored version is lower.

        :param name: Schema name (for example the owning feature).
        :param version: Target schema version.
        :param statements: SQL statements (DDL) that bring the schema to ``version``.
        :return: ``True`` when the statements ran, ``False`` when already up to date.
        """
        with self.connection() as conn:
            conn.execute(_VERSIONS_DDL)
            row = conn.execute(
                "SELECT version FROM schema_versions WHERE name = ?", (name,)
            ).fetchone()
            if row is not None and row["version"] >= version:
                return False
            for statement in statements:
                conn.execute(statement)
            conn.execute(
                "INSERT INTO schema_versions (name, version) VALUES (?, ?) "
                "ON CONFLICT(name) DO UPDATE SET version = excluded.version",
                (name, version),
            )
        _LOGGER.info("Schema %s migrated to version %d", name, version)
        return True

    def upgrade(self, name: str, steps: Sequence[MigrationStep]) -> bool:
        """Apply the pending ordered migration steps in one write transaction.

        Step ``n`` (1-based) brings the schema to version ``n``. The version is read after the
        write lock is taken, so two processes starting together apply each step only once.

        :param name: Schema name (for example the owning feature).
        :param steps: Ordered steps; each receives the connection and must be idempotent.
        :return: ``True`` when at least one step ran, ``False`` when already up to date.
        """
        with self.connection(immediate=True) as conn:
            conn.execute(_VERSIONS_DDL)
            row = conn.execute(
                "SELECT version FROM schema_versions WHERE name = ?", (name,)
            ).fetchone()
            current = 0 if row is None else row["version"]
            if current >= len(steps):
                return False
            for step in steps[current:]:
                step(conn)
            conn.execute(
                "INSERT INTO schema_versions (name, version) VALUES (?, ?) "
                "ON CONFLICT(name) DO UPDATE SET version = excluded.version",
                (name, len(steps)),
            )
        _LOGGER.info("Schema %s upgraded from version %d to %d", name, current, len(steps))
        return True

    def close(self) -> None:
        """Close the shared in-memory connection (a no-op for file databases)."""
        with self._lock:
            if self._shared is not None:
                self._shared.close()
                self._shared = None
