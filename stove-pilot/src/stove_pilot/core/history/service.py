"""History SQL adapter over ``SqliteDatabase`` (timestamps are stored as UTC ISO 8601 text)."""

import json
import logging
import sqlite3
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any, Final, Literal

from stove_pilot.core.history.alerts import AlertChange
from stove_pilot.core.history.models import (
    DisplayAlert,
    EventPage,
    StoveEvent,
    TemperatureSample,
    page_numbers,
    previous_page_offset,
)
from stove_pilot.core.history.schema import MIGRATIONS, SCHEMA_NAME
from stove_pilot.core.stove.models import EventSource, StoveState
from stove_pilot.lib.sqlite import SqliteDatabase

_LOGGER = logging.getLogger(__name__)

_EVENT_COLUMNS: Final = "id, at, kind, source, detail"
_ALERT_COLUMNS: Final = (
    "code, title, severity, occurrences, first_seen_at, last_seen_at, state, cleared_at"
)
_ALERT_INSERT: Final = (
    "INSERT INTO display_alerts "
    "(code, title, severity, occurrences, first_seen_at, last_seen_at, state, cleared_at) "
    "VALUES (?, ?, ?, 1, ?, ?, 'active', NULL) "
    "ON CONFLICT(code) DO UPDATE SET "
    "title = excluded.title, severity = excluded.severity, "
    "occurrences = display_alerts.occurrences + 1, "
    "last_seen_at = excluded.last_seen_at, state = 'active', cleared_at = NULL"
)
_ALERT_REFRESH: Final = (
    "UPDATE display_alerts SET title = ?, severity = ?, last_seen_at = ?, "
    "state = 'active', cleared_at = NULL WHERE code = ?"
)
_ALERT_CLEAR: Final = (
    "UPDATE display_alerts SET state = 'cleared', cleared_at = ? "
    "WHERE code = ? AND state = 'active'"
)
# Reason: bounds the in-Python detail match of ``last_event``.
MATCH_CANDIDATE_LIMIT: Final = 50


class HistoryStore:
    """Persistence of temperature samples, display alerts and stove events.

    Datetimes are normalized to UTC with a fixed-width ISO 8601 format, so text order is
    chronological order and range queries stay correct across DST changes. Readers get
    timezone-aware UTC datetimes back.
    """

    def __init__(self, db: SqliteDatabase) -> None:
        """Bind the database and apply the history schema migrations (idempotent).

        :param db: Shared SQLite database.
        """
        self._db = db
        self._db.upgrade(SCHEMA_NAME, MIGRATIONS)

    def insert_sample(self, sample: TemperatureSample) -> bool:
        """Store a sample; a sample with the same ``at`` is silently ignored.

        :param sample: Sample with a timezone-aware ``at``.
        :return: ``True`` when a row was inserted.
        """
        with self._db.connection() as conn:
            cursor = conn.execute(
                "INSERT OR IGNORE INTO temperature_samples "
                "(at, ambient_c, fumes_c, state, power_level, setpoint_c) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    self._encode_at(sample.at),
                    sample.ambient_c,
                    sample.fumes_c,
                    sample.state.value,
                    sample.power_level,
                    sample.setpoint_c,
                ),
            )
            return cursor.rowcount == 1

    def insert_event(self, event: StoveEvent) -> None:
        """Store an event (``detail`` is serialized as canonical JSON).

        :param event: Event with a timezone-aware ``at``.
        """
        detail = json.dumps(event.detail, sort_keys=True, default=str)
        with self._db.connection() as conn:
            conn.execute(
                "INSERT INTO stove_events (at, kind, source, detail) VALUES (?, ?, ?, ?)",
                (self._encode_at(event.at), event.kind, event.source.value, detail),
            )

    def samples(self, start: datetime, end: datetime) -> list[TemperatureSample]:
        """Return the raw samples of a range, oldest first.

        :param start: Range start (inclusive, timezone-aware).
        :param end: Range end (inclusive, timezone-aware).
        :return: Samples ordered by time.
        """
        with self._db.connection() as conn:
            rows = conn.execute(
                "SELECT at, ambient_c, fumes_c, state, power_level, setpoint_c "
                "FROM temperature_samples WHERE at >= ? AND at <= ? ORDER BY at",
                (self._encode_at(start), self._encode_at(end)),
            ).fetchall()
        _LOGGER.debug("Read %d samples", len(rows))
        return [self._sample_from_row(row) for row in rows]

    def events(
        self,
        limit: int,
        *,
        start: datetime | None = None,
        end: datetime | None = None,
        before_at: datetime | None = None,
        before_id: int | None = None,
    ) -> list[StoveEvent]:
        """Return events newest first, optionally bounded by a range and a keyset cursor.

        ``before_at`` and ``before_id`` are given together: only events strictly older than
        ``(at, id)`` in the ``at DESC, id DESC`` order are returned.

        :param limit: Maximum number of events.
        :param start: Oldest ``at`` to include (inclusive, timezone-aware).
        :param end: Newest ``at`` to include (inclusive, timezone-aware).
        :param before_at: Cursor instant (timezone-aware).
        :param before_id: Cursor event id.
        :return: Events ordered by time, newest first.
        """
        clauses, params = self._range_clauses(start, end)
        if before_at is not None and before_id is not None:
            # Reason: the cursor must use the exact stored text format (UTC, microseconds),
            # otherwise the string comparison of ``at`` breaks ties and ordering.
            clauses.append("(at, id) < (?, ?)")
            params.extend((self._encode_at(before_at), before_id))
        with self._db.connection() as conn:
            rows = self._select_events(conn, clauses, params, limit)
        _LOGGER.debug("Read %d events", len(rows))
        return [self._event_from_row(row) for row in rows]

    def events_window(
        self,
        limit: int,
        *,
        start: datetime | None = None,
        end: datetime | None = None,
        before_at: datetime | None = None,
        before_id: int | None = None,
        page_size: int | None = None,
    ) -> EventPage:
        """Return one event page and the range counts, from a single snapshot.

        ``newer_count`` is computed only when a cursor is given: events whose ``(at, id)``
        is at or newer than the cursor, inside the same range. The ``at`` range uses
        ``ix_stove_events_at``.

        :param limit: Maximum number of events.
        :param start: Oldest ``at`` to include (inclusive, timezone-aware).
        :param end: Newest ``at`` to include (inclusive, timezone-aware).
        :param before_at: Cursor instant (timezone-aware).
        :param before_id: Cursor event id.
        :param page_size: Events per page, used to number pages and to find the previous cursor.
        :return: The page, the counts and, when ``page_size`` is set, the page numbers.
        """
        ranged, range_params = self._range_clauses(start, end)
        listed = list(ranged)
        listed_params = list(range_params)
        if before_at is not None and before_id is not None:
            listed.append("(at, id) < (?, ?)")
            listed_params.extend((self._encode_at(before_at), before_id))
        with self._db.connection() as conn:
            total = self._count_events(conn, ranged, range_params)
            newer = None
            if before_at is not None and before_id is not None:
                newer = self._count_at_or_after(
                    conn, ranged, range_params, before_at, before_id
                )
            rows = self._select_events(conn, listed, listed_params, limit)
            events = [self._event_from_row(row) for row in rows]
            page, pages, previous_at, previous_id = self._page_label(
                conn, total, newer, page_size, start, end
            )
        _LOGGER.debug("Read %d events (total %d)", len(events), total)
        return EventPage(events, total, newer, page, pages, previous_at, previous_id)

    def last_event(self, kind: str, **match: Any) -> StoveEvent | None:
        """Return the newest event of a kind whose detail contains every ``match`` pair.

        The match runs in Python over the ``MATCH_CANDIDATE_LIMIT`` newest events of the kind,
        so an older matching event beyond that window is not found.

        :param kind: Event kind.
        :param match: Detail key/value pairs that must all be present.
        :return: The matching event, or ``None``.
        """
        with self._db.connection() as conn:
            rows = conn.execute(
                f"SELECT {_EVENT_COLUMNS} FROM stove_events WHERE kind = ? "
                "ORDER BY at DESC, id DESC LIMIT ?",
                (kind, MATCH_CANDIDATE_LIMIT),
            ).fetchall()
        for row in rows:
            event = self._event_from_row(row)
            if self._contains(event.detail, match):
                return event
        return None

    def newest_command_event(
        self,
        *,
        command: str,
        results: Sequence[str],
        source: str | None = None,
        after: datetime | None = None,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> StoveEvent | None:
        """Return the newest command event matching the filters, or ``None``.

        One query with ``LIMIT 1``. ``after`` is exclusive; ``start`` and ``end`` are inclusive.

        :param command: ``detail.command`` value.
        :param results: Accepted ``detail.result`` values.
        :param source: Event source value, or ``None`` for any source.
        :param after: Exclusive lower bound.
        :param start: Inclusive lower bound.
        :param end: Inclusive upper bound.
        :return: The matching event, or ``None``.
        """
        clauses = ["kind = ?", "json_extract(detail, '$.command') = ?"]
        params: list[Any] = ["command", command]
        marks = ", ".join("?" for _ in results)
        clauses.append(f"json_extract(detail, '$.result') IN ({marks})")
        params.extend(results)
        if source is not None:
            clauses.append("source = ?")
            params.append(source)
        if after is not None:
            clauses.append("at > ?")
            params.append(self._encode_at(after))
        if start is not None:
            clauses.append("at >= ?")
            params.append(self._encode_at(start))
        if end is not None:
            clauses.append("at <= ?")
            params.append(self._encode_at(end))
        with self._db.connection() as conn:
            row = conn.execute(
                f"SELECT {_EVENT_COLUMNS} FROM stove_events WHERE {' AND '.join(clauses)} "
                "ORDER BY at DESC, id DESC LIMIT 1",
                params,
            ).fetchone()
        found = 0 if row is None else 1
        _LOGGER.debug("Command lookup matched %d event", found)
        return None if row is None else self._event_from_row(row)

    def active_alert_codes(self) -> set[str]:
        """Return codes whose persisted state is ``active``.

        :return: Codes shown on the last successful poll.
        """
        with self._db.connection() as conn:
            rows = conn.execute(
                "SELECT code FROM display_alerts WHERE state = 'active'"
            ).fetchall()
        return {str(row["code"]) for row in rows}

    def apply_alert_changes(self, changes: Sequence[AlertChange], at: datetime) -> None:
        """Apply one poll's alert writes in a single transaction.

        :param changes: Appearance, refresh and clear operations.
        :param at: Timezone-aware instant of the poll.
        """
        if not changes:
            return
        text = self._encode_at(at)
        with self._db.connection() as conn:
            for change in changes:
                self._apply_alert_change(conn, change, text)

    def alerts(self, limit: int) -> list[DisplayAlert]:
        """Return stored alerts, most recently seen first.

        :param limit: Maximum number of rows.
        :return: Alerts ordered by ``last_seen_at`` descending.
        """
        with self._db.connection() as conn:
            rows = conn.execute(
                f"SELECT {_ALERT_COLUMNS} FROM display_alerts "
                "ORDER BY last_seen_at DESC, code ASC LIMIT ?",
                (limit,),
            ).fetchall()
        return [self._alert_from_row(row) for row in rows]

    def purge_before(self, cutoff: datetime) -> tuple[int, int, int]:
        """Delete samples, events and cleared alerts older than the cutoff.

        Active alerts are kept even when their last appearance is older than the cutoff.

        :param cutoff: Rows strictly older than this timezone-aware instant are deleted.
        :return: Deleted samples, deleted events and deleted alerts.
        """
        text = self._encode_at(cutoff)
        with self._db.connection() as conn:
            samples = conn.execute("DELETE FROM temperature_samples WHERE at < ?", (text,))
            events = conn.execute("DELETE FROM stove_events WHERE at < ?", (text,))
            alerts = conn.execute(
                "DELETE FROM display_alerts WHERE last_seen_at < ? AND state = 'cleared'",
                (text,),
            )
            counts = (samples.rowcount, events.rowcount, alerts.rowcount)
        _LOGGER.debug("Purged %d samples, %d events and %d alerts", *counts)
        return counts

    def _range_clauses(
        self, start: datetime | None, end: datetime | None
    ) -> tuple[list[str], list[Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        if start is not None:
            clauses.append("at >= ?")
            params.append(self._encode_at(start))
        if end is not None:
            clauses.append("at <= ?")
            params.append(self._encode_at(end))
        return clauses, params

    @staticmethod
    def _where(clauses: list[str]) -> str:
        return f"WHERE {' AND '.join(clauses)} " if clauses else ""

    def _count_events(
        self, conn: sqlite3.Connection, clauses: list[str], params: list[Any]
    ) -> int:
        row = conn.execute(
            f"SELECT COUNT(*) AS n FROM stove_events {self._where(clauses)}", params
        ).fetchone()
        return int(row["n"])

    def _count_at_or_after(
        self,
        conn: sqlite3.Connection,
        clauses: list[str],
        params: list[Any],
        at: datetime,
        event_id: int,
    ) -> int:
        # Reason: the cursor row is the last event of the previous page, so the page
        # number includes it. ``at`` is a range scan of ix_stove_events_at.
        counted = [*clauses, "(at, id) >= (?, ?)"]
        bound = [*params, self._encode_at(at), event_id]
        return self._count_events(conn, counted, bound)

    def _select_events(
        self, conn: sqlite3.Connection, clauses: list[str], params: list[Any], limit: int
    ) -> list[sqlite3.Row]:
        return conn.execute(
            f"SELECT {_EVENT_COLUMNS} FROM stove_events {self._where(clauses)}"
            "ORDER BY at DESC, id DESC LIMIT ?",
            (*params, limit),
        ).fetchall()

    def _event_at_offset(
        self, conn: sqlite3.Connection, offset: int, start: datetime | None, end: datetime | None
    ) -> StoveEvent | None:
        clauses, params = self._range_clauses(start, end)
        row = conn.execute(
            f"SELECT {_EVENT_COLUMNS} FROM stove_events {self._where(clauses)}"
            "ORDER BY at DESC, id DESC LIMIT 1 OFFSET ?",
            (*params, offset),
        ).fetchone()
        return None if row is None else self._event_from_row(row)

    def _page_label(
        self,
        conn: sqlite3.Connection,
        total: int,
        newer: int | None,
        page_size: int | None,
        start: datetime | None,
        end: datetime | None,
    ) -> tuple[int | None, int | None, datetime | None, int | None]:
        if page_size is None:
            return None, None, None, None
        page, pages = page_numbers(total, newer, page_size)
        offset = previous_page_offset(page, page_size)
        if offset is None:
            return page, pages, None, None
        boundary = self._event_at_offset(conn, offset, start, end)
        if boundary is None or boundary.id is None:
            return page, pages, None, None
        return page, pages, boundary.at, boundary.id

    @staticmethod
    def _apply_alert_change(conn: sqlite3.Connection, change: AlertChange, at: str) -> None:
        if change.clear:
            conn.execute(_ALERT_CLEAR, (at, change.code))
            return
        if change.appear:
            conn.execute(_ALERT_INSERT, (change.code, change.title, change.severity, at, at))
            return
        conn.execute(_ALERT_REFRESH, (change.title, change.severity, at, change.code))

    @staticmethod
    def _alert_state(value: object) -> Literal["active", "cleared"]:
        if value == "active":
            return "active"
        if value == "cleared":
            return "cleared"
        raise ValueError("Unknown alert state")

    @classmethod
    def _alert_from_row(cls, row: sqlite3.Row) -> DisplayAlert:
        cleared = row["cleared_at"]
        return DisplayAlert(
            code=row["code"],
            title=row["title"],
            severity=row["severity"],
            occurrences=int(row["occurrences"]),
            first_seen_at=datetime.fromisoformat(row["first_seen_at"]),
            last_seen_at=datetime.fromisoformat(row["last_seen_at"]),
            state=cls._alert_state(row["state"]),
            cleared_at=None if cleared is None else datetime.fromisoformat(cleared),
        )

    @staticmethod
    def _encode_at(at: datetime) -> str:
        if at.tzinfo is None or at.utcoffset() is None:
            raise ValueError("History timestamps must be timezone-aware")
        return at.astimezone(UTC).isoformat(timespec="microseconds")

    @staticmethod
    def _contains(detail: dict[str, Any], match: dict[str, Any]) -> bool:
        return all(key in detail and detail[key] == value for key, value in match.items())

    @staticmethod
    def _sample_from_row(row: sqlite3.Row) -> TemperatureSample:
        return TemperatureSample(
            at=datetime.fromisoformat(row["at"]),
            ambient_c=row["ambient_c"],
            fumes_c=row["fumes_c"],
            state=StoveState(row["state"]),
            power_level=row["power_level"],
            setpoint_c=row["setpoint_c"],
        )

    @staticmethod
    def _event_from_row(row: sqlite3.Row) -> StoveEvent:
        return StoveEvent(
            at=datetime.fromisoformat(row["at"]),
            kind=row["kind"],
            source=EventSource(row["source"]),
            detail=json.loads(row["detail"]),
            id=row["id"],
        )
