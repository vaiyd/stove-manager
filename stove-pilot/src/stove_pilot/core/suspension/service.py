"""Suspension SQL adapter over ``SqliteDatabase`` (one row, snapshots stored as canonical JSON)."""

import json
import logging
import sqlite3
from datetime import UTC, datetime
from typing import Any, Final

from stove_pilot.common.exceptions import ConfigurationError
from stove_pilot.core.suspension.models import (
    PHASE_ACTIVATING,
    PHASE_ACTIVE,
    PHASE_RESTORING,
    SuspensionState,
)
from stove_pilot.lib.sqlite import MigrationStep, SqliteDatabase

_LOGGER = logging.getLogger(__name__)

SCHEMA_NAME: Final = "suspension"
_TABLE: Final = "programming_suspension"
_V1_STATEMENT: Final = (
    "CREATE TABLE IF NOT EXISTS programming_suspension ("
    "id INTEGER PRIMARY KEY CHECK (id = 1), suspended INTEGER NOT NULL, since TEXT, until TEXT, "
    "thermostat_mode INTEGER NOT NULL, snapshots_json TEXT NOT NULL, updated_at TEXT NOT NULL)"
)
_V2_COLUMNS: Final = (
    (
        "kind",
        "ALTER TABLE programming_suspension "
        "ADD COLUMN kind TEXT NOT NULL DEFAULT 'programming'",
    ),
    ("frost_setpoint_c", "ALTER TABLE programming_suspension ADD COLUMN frost_setpoint_c REAL"),
    (
        "stove_snapshot_json",
        "ALTER TABLE programming_suspension "
        "ADD COLUMN stove_snapshot_json TEXT NOT NULL DEFAULT '{}'",
    ),
)
_V3_COLUMNS: Final = (
    (
        "phase",
        "ALTER TABLE programming_suspension "
        "ADD COLUMN phase TEXT NOT NULL DEFAULT 'active'",
    ),
)
_KINDS: Final = ("programming", "vacation")
_PHASES: Final = (PHASE_ACTIVATING, PHASE_ACTIVE, PHASE_RESTORING)


def _add_columns(conn: sqlite3.Connection, columns: tuple[tuple[str, str], ...]) -> None:
    # Reason: SQLite has no ADD COLUMN IF NOT EXISTS, so the existing columns are checked first.
    existing = {row["name"] for row in conn.execute(f"PRAGMA table_info({_TABLE})")}
    for column, statement in columns:
        if column not in existing:
            conn.execute(statement)


def _create_v1(conn: sqlite3.Connection) -> None:
    conn.execute(_V1_STATEMENT)


def _upgrade_to_v2(conn: sqlite3.Connection) -> None:
    _add_columns(conn, _V2_COLUMNS)


def _upgrade_to_v3(conn: sqlite3.Connection) -> None:
    _add_columns(conn, _V3_COLUMNS)


MIGRATIONS: Final[tuple[MigrationStep, ...]] = (_create_v1, _upgrade_to_v2, _upgrade_to_v3)


class SuspensionStore:
    """Persistence of the suspension state, so a restart keeps it with its snapshots."""

    def __init__(self, db: SqliteDatabase) -> None:
        """Bind the database and apply the suspension schema (idempotent).

        :param db: Shared SQLite database.
        """
        self._db = db
        self._db.upgrade(SCHEMA_NAME, MIGRATIONS)

    def load(self) -> SuspensionState:
        """Read the stored state.

        :return: The stored state, or the default (not suspended) when nothing is stored.
        :raises ConfigurationError: The stored row cannot be decoded.
        """
        with self._db.connection() as conn:
            row = conn.execute(
                "SELECT suspended, since, until, thermostat_mode, snapshots_json, "
                "kind, frost_setpoint_c, stove_snapshot_json, phase "
                "FROM programming_suspension WHERE id = 1"
            ).fetchone()
        if row is None:
            return SuspensionState()
        try:
            snapshots: dict[str, dict[str, Any]] = json.loads(row["snapshots_json"])
            stove_snapshot: dict[str, Any] = json.loads(row["stove_snapshot_json"])
            kind = row["kind"]
            if kind not in _KINDS:
                raise ValueError(f"Unknown suspension kind {kind!r}")
            phase = row["phase"]
            if phase not in _PHASES:
                raise ValueError(f"Unknown suspension phase {phase!r}")
            frost = row["frost_setpoint_c"]
            return SuspensionState(
                suspended=bool(row["suspended"]),
                since=self._decode_at(row["since"]),
                until=self._decode_at(row["until"]),
                thermostat_mode=bool(row["thermostat_mode"]),
                snapshots=snapshots,
                kind=kind,
                frost_setpoint_c=None if frost is None else float(frost),
                stove_snapshot=stove_snapshot,
                phase=phase,
            )
        except (ValueError, TypeError) as exc:
            raise ConfigurationError(
                "The stored programming suspension is unreadable; inspect the database"
            ) from exc

    def save(self, state: SuspensionState) -> None:
        """Store the state (insert or replace the single row).

        :param state: State to store; ``since`` and ``until`` must be timezone-aware when set.
        :raises ValueError: A datetime is naive.
        """
        values = (
            int(state.suspended),
            self._encode_at(state.since),
            self._encode_at(state.until),
            int(state.thermostat_mode),
            json.dumps(state.snapshots, sort_keys=True, separators=(",", ":")),
            datetime.now(UTC).isoformat(timespec="microseconds"),
            state.kind,
            state.frost_setpoint_c,
            json.dumps(state.stove_snapshot, sort_keys=True, separators=(",", ":")),
            state.phase,
        )
        with self._db.connection() as conn:
            conn.execute(
                "INSERT INTO programming_suspension "
                "(id, suspended, since, until, thermostat_mode, snapshots_json, updated_at, "
                "kind, frost_setpoint_c, stove_snapshot_json, phase) "
                "VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(id) DO UPDATE SET suspended = excluded.suspended, "
                "since = excluded.since, until = excluded.until, "
                "thermostat_mode = excluded.thermostat_mode, "
                "snapshots_json = excluded.snapshots_json, updated_at = excluded.updated_at, "
                "kind = excluded.kind, frost_setpoint_c = excluded.frost_setpoint_c, "
                "stove_snapshot_json = excluded.stove_snapshot_json, phase = excluded.phase",
                values,
            )
        _LOGGER.debug("Stored the suspension state (suspended=%s)", state.suspended)

    @staticmethod
    def _encode_at(at: datetime | None) -> str | None:
        if at is None:
            return None
        if at.tzinfo is None or at.utcoffset() is None:
            raise ValueError("Suspension timestamps must be timezone-aware")
        return at.isoformat()

    @staticmethod
    def _decode_at(text: str | None) -> datetime | None:
        return None if text is None else datetime.fromisoformat(text)
