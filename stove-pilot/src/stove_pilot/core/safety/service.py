"""Safety SQL adapter: one config row and one state row in the safety schema."""

import logging
import sqlite3
from datetime import UTC, datetime
from typing import Final

from stove_pilot.common.exceptions import ConfigurationError
from stove_pilot.core.safety.models import (
    PAUSE_REASONS,
    SafetyConfig,
    SafetyPhase,
    SafetyState,
)
from stove_pilot.lib.sqlite import MigrationStep, SqliteDatabase

_LOGGER = logging.getLogger(__name__)

SCHEMA_NAME: Final = "safety"
_CONFIG_TABLE: Final = "safety_config"
_STATE_TABLE: Final = "safety_state"
_CONFIG_DDL: Final = (
    "CREATE TABLE IF NOT EXISTS safety_config ("
    "id INTEGER PRIMARY KEY CHECK (id = 1), "
    "enabled INTEGER NOT NULL, "
    "threshold_c INTEGER NOT NULL, "
    "max_attempts INTEGER NOT NULL, "
    "retry_interval_min INTEGER NOT NULL, "
    "updated_at TEXT NOT NULL)"
)
_STATE_DDL: Final = (
    "CREATE TABLE IF NOT EXISTS safety_state ("
    "id INTEGER PRIMARY KEY CHECK (id = 1), "
    "phase TEXT NOT NULL, "
    "attempts INTEGER NOT NULL, "
    "confirm_count INTEGER NOT NULL, "
    "last_attempt_at TEXT, "
    "next_attempt_at TEXT, "
    "failure_reason TEXT, "
    "paused_reason TEXT, "
    "last_check_at TEXT, "
    "last_stove_state TEXT, "
    "last_polled_at TEXT, "
    "last_app_stop_at TEXT, "
    "vacation_was_active INTEGER NOT NULL, "
    "updated_at TEXT NOT NULL)"
)


def _create_v1(conn: sqlite3.Connection) -> None:
    conn.execute(_CONFIG_DDL)
    conn.execute(_STATE_DDL)


MIGRATIONS: Final[tuple[MigrationStep, ...]] = (_create_v1,)


class SafetyStore:
    """Persistence of the safety card and the episode, so a restart keeps both."""

    def __init__(self, db: SqliteDatabase) -> None:
        """Bind the database and apply the safety schema (idempotent).

        :param db: Shared SQLite database.
        """
        self._db = db
        self._db.upgrade(SCHEMA_NAME, MIGRATIONS)

    def load_config(self) -> SafetyConfig | None:
        """Read the stored card.

        :return: The stored config, or ``None`` until the user saves one.
        :raises ConfigurationError: The stored row cannot be decoded.
        """
        with self._db.connection() as conn:
            row = conn.execute(
                "SELECT enabled, threshold_c, max_attempts, retry_interval_min "
                f"FROM {_CONFIG_TABLE} WHERE id = 1"
            ).fetchone()
        if row is None:
            return None
        try:
            return SafetyConfig(
                enabled=self._decode_bool(row["enabled"]),
                threshold_c=self._decode_int(row["threshold_c"]),
                max_attempts=self._decode_int(row["max_attempts"]),
                retry_interval_min=self._decode_int(row["retry_interval_min"]),
            )
        except (ValueError, TypeError) as exc:
            raise ConfigurationError(
                "The stored safety configuration is unreadable; inspect the database"
            ) from exc

    def save_config(self, config: SafetyConfig) -> None:
        """Store the card (insert or replace the single row).

        :param config: Config to store. Range checks belong to the caller.
        """
        values = (
            int(config.enabled),
            config.threshold_c,
            config.max_attempts,
            config.retry_interval_min,
            datetime.now(UTC).isoformat(timespec="microseconds"),
        )
        with self._db.connection() as conn:
            conn.execute(
                f"INSERT INTO {_CONFIG_TABLE} "
                "(id, enabled, threshold_c, max_attempts, retry_interval_min, updated_at) "
                "VALUES (1, ?, ?, ?, ?, ?) "
                "ON CONFLICT(id) DO UPDATE SET enabled = excluded.enabled, "
                "threshold_c = excluded.threshold_c, max_attempts = excluded.max_attempts, "
                "retry_interval_min = excluded.retry_interval_min, "
                "updated_at = excluded.updated_at",
                values,
            )
        _LOGGER.debug("Stored the safety config (enabled=%s)", config.enabled)

    def load_state(self) -> SafetyState:
        """Read the stored episode.

        :return: The stored state, or the idle default when nothing is stored.
        :raises ConfigurationError: The stored row cannot be decoded.
        """
        with self._db.connection() as conn:
            row = conn.execute(
                "SELECT phase, attempts, confirm_count, last_attempt_at, next_attempt_at, "
                "failure_reason, paused_reason, last_check_at, last_stove_state, "
                "last_polled_at, last_app_stop_at, vacation_was_active "
                f"FROM {_STATE_TABLE} WHERE id = 1"
            ).fetchone()
        if row is None:
            return SafetyState()
        try:
            return self._decode_state(row)
        except (ValueError, TypeError) as exc:
            raise ConfigurationError(
                "The stored safety state is unreadable; inspect the database"
            ) from exc

    def save_state(self, state: SafetyState) -> None:
        """Store the episode (insert or replace the single row).

        :param state: Episode to store. Datetimes must be timezone-aware.
        :raises ValueError: A datetime is naive.
        """
        values = (
            state.phase.value,
            state.attempts,
            state.confirm_count,
            self._encode_at(state.last_attempt_at),
            self._encode_at(state.next_attempt_at),
            state.failure_reason,
            state.paused_reason,
            self._encode_at(state.last_check_at),
            state.last_stove_state,
            self._encode_at(state.last_polled_at),
            self._encode_at(state.last_app_stop_at),
            int(state.vacation_was_active),
            datetime.now(UTC).isoformat(timespec="microseconds"),
        )
        with self._db.connection() as conn:
            conn.execute(
                f"INSERT INTO {_STATE_TABLE} "
                "(id, phase, attempts, confirm_count, last_attempt_at, next_attempt_at, "
                "failure_reason, paused_reason, last_check_at, last_stove_state, "
                "last_polled_at, last_app_stop_at, vacation_was_active, updated_at) "
                "VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(id) DO UPDATE SET phase = excluded.phase, "
                "attempts = excluded.attempts, confirm_count = excluded.confirm_count, "
                "last_attempt_at = excluded.last_attempt_at, "
                "next_attempt_at = excluded.next_attempt_at, "
                "failure_reason = excluded.failure_reason, "
                "paused_reason = excluded.paused_reason, "
                "last_check_at = excluded.last_check_at, "
                "last_stove_state = excluded.last_stove_state, "
                "last_polled_at = excluded.last_polled_at, "
                "last_app_stop_at = excluded.last_app_stop_at, "
                "vacation_was_active = excluded.vacation_was_active, "
                "updated_at = excluded.updated_at",
                values,
            )
        _LOGGER.debug("Stored the safety state (phase=%s attempts=%s)", state.phase, state.attempts)

    @staticmethod
    def _decode_state(row: sqlite3.Row) -> SafetyState:
        paused = row["paused_reason"]
        if paused is not None and paused not in PAUSE_REASONS:
            raise ValueError(f"Unknown pause reason {paused!r}")
        failure = row["failure_reason"]
        if failure is not None and not isinstance(failure, str):
            raise TypeError(f"Expected a failure reason string, got {failure!r}")
        stove_state = row["last_stove_state"]
        if stove_state is not None and not isinstance(stove_state, str):
            raise TypeError(f"Expected a stove state string, got {stove_state!r}")
        return SafetyState(
            phase=SafetyPhase(row["phase"]),
            attempts=SafetyStore._decode_int(row["attempts"]),
            confirm_count=SafetyStore._decode_int(row["confirm_count"]),
            last_attempt_at=SafetyStore._decode_at(row["last_attempt_at"]),
            next_attempt_at=SafetyStore._decode_at(row["next_attempt_at"]),
            failure_reason=failure,
            paused_reason=paused,
            last_check_at=SafetyStore._decode_at(row["last_check_at"]),
            last_stove_state=stove_state,
            last_polled_at=SafetyStore._decode_at(row["last_polled_at"]),
            last_app_stop_at=SafetyStore._decode_at(row["last_app_stop_at"]),
            vacation_was_active=SafetyStore._decode_bool(row["vacation_was_active"]),
        )

    @staticmethod
    def _encode_at(at: datetime | None) -> str | None:
        if at is None:
            return None
        if at.tzinfo is None or at.utcoffset() is None:
            raise ValueError("Safety timestamps must be timezone-aware")
        return at.isoformat()

    @staticmethod
    def _decode_at(text: str | None) -> datetime | None:
        if text is None:
            return None
        value = datetime.fromisoformat(text)
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Safety timestamps must be timezone-aware")
        return value

    @staticmethod
    def _decode_bool(value: object) -> bool:
        if value not in (0, 1):
            raise ValueError(f"Expected 0 or 1, got {value!r}")
        return bool(value)

    @staticmethod
    def _decode_int(value: object) -> int:
        # Reason: bool is a subclass of int and is not a stored counter.
        if isinstance(value, bool) or not isinstance(value, int):
            raise TypeError(f"Expected an integer, got {value!r}")
        return value
