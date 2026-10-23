"""History schema: ordered, idempotent migration steps (step ``n`` gives version ``n``)."""

import sqlite3
from typing import Final

from stove_pilot.lib.sqlite import MigrationStep

SCHEMA_NAME: Final = "history"

_V1_STATEMENTS: Final = (
    "CREATE TABLE IF NOT EXISTS temperature_samples ("
    "at TEXT PRIMARY KEY, ambient_c REAL, fumes_c REAL, state TEXT NOT NULL)",
    "CREATE TABLE IF NOT EXISTS stove_events ("
    "id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT NOT NULL, kind TEXT NOT NULL, "
    "source TEXT NOT NULL, detail TEXT NOT NULL)",
    "CREATE INDEX IF NOT EXISTS ix_stove_events_at ON stove_events(at)",
)
_V2_COLUMNS: Final = (
    ("power_level", "ALTER TABLE temperature_samples ADD COLUMN power_level INTEGER"),
    ("setpoint_c", "ALTER TABLE temperature_samples ADD COLUMN setpoint_c REAL"),
)


def _create_v1(conn: sqlite3.Connection) -> None:
    for statement in _V1_STATEMENTS:
        conn.execute(statement)


def _upgrade_to_v2(conn: sqlite3.Connection) -> None:
    # Reason: SQLite has no ADD COLUMN IF NOT EXISTS, so the existing columns are checked first.
    existing = {row["name"] for row in conn.execute("PRAGMA table_info(temperature_samples)")}
    for column, statement in _V2_COLUMNS:
        if column not in existing:
            conn.execute(statement)


# Reason: CREATE IF NOT EXISTS so a retried step (version not yet bumped) is safe.
_V3_STATEMENTS: Final = (
    "CREATE TABLE IF NOT EXISTS display_alerts ("
    "code TEXT PRIMARY KEY, title TEXT NOT NULL, severity TEXT NOT NULL, "
    "occurrences INTEGER NOT NULL, first_seen_at TEXT NOT NULL, last_seen_at TEXT NOT NULL, "
    "state TEXT NOT NULL CHECK (state IN ('active', 'cleared')), cleared_at TEXT)",
    "CREATE INDEX IF NOT EXISTS ix_display_alerts_last_seen ON display_alerts (last_seen_at)",
)


def _upgrade_to_v3(conn: sqlite3.Connection) -> None:
    for statement in _V3_STATEMENTS:
        conn.execute(statement)


MIGRATIONS: Final[tuple[MigrationStep, ...]] = (_create_v1, _upgrade_to_v2, _upgrade_to_v3)
