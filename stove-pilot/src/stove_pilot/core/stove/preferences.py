"""Runtime preferences of the stove feature: a tiny key/value table in the shared SQLite file."""

import sqlite3
from datetime import UTC, datetime
from typing import Final

from stove_pilot.lib.sqlite import MigrationStep, SqliteDatabase

SCHEMA_NAME: Final = "stove_preferences"
CLOCK_AUTO_SYNC_KEY: Final = "clock_auto_sync"
_TRUE: Final = "1"
_FALSE: Final = "0"
_V1_STATEMENT: Final = (
    "CREATE TABLE IF NOT EXISTS stove_runtime_preferences ("
    "key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at TEXT NOT NULL)"
)


def _create_v1(conn: sqlite3.Connection) -> None:
    conn.execute(_V1_STATEMENT)


MIGRATIONS: Final[tuple[MigrationStep, ...]] = (_create_v1,)


class RuntimePreferences:
    """SQLite-backed ``PreferenceStore``; a flag is stored as ``1`` or ``0``."""

    def __init__(self, db: SqliteDatabase) -> None:
        """Bind the database and apply the preferences schema (idempotent).

        :param db: Shared SQLite database.
        """
        self._db = db
        self._db.upgrade(SCHEMA_NAME, MIGRATIONS)

    def get_bool(self, key: str) -> bool | None:
        """Read a flag.

        :param key: Preference name.
        :return: The stored value, or ``None`` when nothing (or an unreadable value) is stored.
        """
        with self._db.connection() as conn:
            row = conn.execute(
                "SELECT value FROM stove_runtime_preferences WHERE key = ?", (key,)
            ).fetchone()
        if row is None or row["value"] not in (_TRUE, _FALSE):
            return None
        return row["value"] == _TRUE

    def set_bool(self, key: str, value: bool) -> None:
        """Store a flag (insert or replace).

        :param key: Preference name.
        :param value: New value.
        """
        with self._db.connection(immediate=True) as conn:
            conn.execute(
                "INSERT INTO stove_runtime_preferences (key, value, updated_at) VALUES (?, ?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value, "
                "updated_at = excluded.updated_at",
                (key, _TRUE if value else _FALSE, datetime.now(UTC).isoformat()),
            )
