"""History facade used by the service layer: validation, bucketing and retention."""

import logging
import sqlite3
from collections.abc import Callable, Sequence
from datetime import datetime, timedelta
from itertools import groupby
from statistics import fmean
from typing import Any, Final

from stove_pilot.common.exceptions import ValidationFailedError
from stove_pilot.core.history.alerts import AlertSighting, plan_alert_changes
from stove_pilot.core.history.models import DisplayAlert, EventPage, StoveEvent, TemperatureSample
from stove_pilot.core.history.service import HistoryStore
from stove_pilot.core.history.short_cycle import (
    SHORT_CYCLE_MAX_GAP,
    CycleSample,
    evaluate_short_cycle,
)
from stove_pilot.core.stove.messages import SHORT_CYCLE_MESSAGE, DisplayMessage
from stove_pilot.core.stove.models import EventSource, StoveStatus

_LOGGER = logging.getLogger(__name__)

IGNITION_KIND: Final = "ignition"


class HistoryRepository:
    """Record and query the temperature history, display alerts and the event log."""

    def __init__(
        self,
        store: HistoryStore,
        clock: Callable[[], datetime],
        *,
        short_cycle_min_interval_min: int = 90,
    ) -> None:
        """Bind the store and the clock and load the alerts still shown.

        :param store: History SQL adapter.
        :param clock: Returns the current timezone-aware datetime.
        :param short_cycle_min_interval_min: Minimum compliant minutes between starts (at least 1).
        """
        if short_cycle_min_interval_min < 1:
            raise ValueError("short_cycle_min_interval_min must be at least 1")
        self._store = store
        self._clock = clock
        self._short_cycle_min_interval_min = short_cycle_min_interval_min
        self._short_cycle_read_failed = False
        # Reason: a restart must not count a still-shown code as a new appearance.
        # Reason: this set is only rebound, never mutated in place, so lock-free reads from request threads are safe.
        self._active_alerts = set(self._store.active_alert_codes())

    def record_sample(self, status: StoveStatus) -> bool:
        """Store a sample at ``status.polled_at``; a duplicate instant is skipped silently.

        :param status: Stove status snapshot.
        :return: ``True`` when a new row was stored.
        """
        sample = TemperatureSample(
            at=status.polled_at,
            ambient_c=status.ambient_temp_c,
            fumes_c=status.fumes_temp_c,
            state=status.state,
            power_level=status.power_level,
            setpoint_c=status.setpoint_c,
        )
        return self._store.insert_sample(sample)

    def record_alerts(self, messages: Sequence[DisplayMessage], *, at: datetime) -> None:
        """Record the display messages of one poll, or their absence.

        A code counts as a new appearance only when it was not active on the previous poll.
        ``SHORT_CYCLE`` is sighted on the same write while the sample rule says it is raised,
        so a panel code that disappeared still clears and this derived alert does not.
        A database error is logged and swallowed so the caller (the status poll) continues.
        The in-memory active set is then reloaded from the persisted active codes.

        :param messages: Messages decoded for this poll (empty when none is shown).
        :param at: Timezone-aware instant of the poll.
        """
        if at.tzinfo is None or at.utcoffset() is None:
            raise ValueError("Alert timestamps must be timezone-aware")
        sightings = tuple(
            AlertSighting(message.code, message.title_fr, message.level)
            for message in messages
            if message.code
        )
        # Reason: plan_alert_changes clears any active code missing from this poll.
        derived = self._short_cycle_sighting(at)
        if derived is not None:
            sightings = (*sightings, derived)
        changes, active = plan_alert_changes(self._active_alerts, sightings)
        if not changes:
            return
        codes = ",".join(change.code for change in changes)
        try:
            self._store.apply_alert_changes(changes, at)
        except sqlite3.Error as exc:
            _LOGGER.warning(
                "Alert history write failed (codes=%s, error=%s)", codes, type(exc).__name__
            )
            # Reason: resync from persisted active codes; a failed reload keeps the previous set, so a rolled-back clear stays one occurrence.
            self._reload_active_alerts()
            return
        self._active_alerts = set(active)
        _LOGGER.debug("Recorded display alerts (codes=%s)", codes)

    def record_event(self, kind: str, source: EventSource, **detail: Any) -> None:
        """Store an event at the current clock time.

        :param kind: Event kind (for example ``ignition``).
        :param source: Origin of the event.
        :param detail: JSON-serializable details (non-serializable values are stringified).
        """
        self._store.insert_event(StoveEvent(self._now(), kind, source, dict(detail)))
        _LOGGER.debug("Recorded event %s from %s", kind, source.value)

    def samples(self, start: datetime, end: datetime) -> list[TemperatureSample]:
        """Return the raw samples of a range, oldest first.

        :param start: Range start (inclusive, timezone-aware).
        :param end: Range end (inclusive, timezone-aware).
        :return: Samples ordered by time (UTC datetimes).
        """
        self._validate_range(start, end)
        return self._store.samples(start, end)

    def temperatures(
        self, start: datetime, end: datetime, bucket_min: int
    ) -> list[TemperatureSample]:
        """Return the samples averaged per fixed-width time bucket.

        Buckets are aligned on the Unix epoch, so a sliding window keeps stable bucket edges.
        The state of a bucket is the state of its last sample; temperatures average the
        non-null readings.

        :param start: Range start (inclusive, timezone-aware); its timezone is used for the output.
        :param end: Range end (inclusive, timezone-aware).
        :param bucket_min: Bucket width in minutes (at least 1).
        :return: One sample per non-empty bucket, at the bucket start, oldest first.
        """
        self._validate_range(start, end)
        if bucket_min < 1:
            raise ValidationFailedError(["bucket_min must be at least 1"])
        width_s = bucket_min * 60
        samples = self._store.samples(start, end)
        buckets = groupby(samples, key=lambda sample: int(sample.at.timestamp()) // width_s)
        return [
            self._average(index * width_s, list(group), start) for index, group in buckets
        ]

    def events_page(
        self,
        limit: int,
        *,
        start: datetime | None = None,
        end: datetime | None = None,
        before_at: datetime | None = None,
        before_id: int | None = None,
    ) -> list[StoveEvent]:
        """Return a page of events, newest first, ordered by ``at`` then ``id`` descending.

        :param limit: Maximum number of events (at least 1).
        :param start: Oldest ``at`` to include (inclusive, timezone-aware).
        :param end: Newest ``at`` to include (inclusive, timezone-aware).
        :param before_at: Keyset cursor instant (timezone-aware); needs ``before_id``.
        :param before_id: Keyset cursor id (at least 0); needs ``before_at``.
        :return: Events strictly older than the cursor when given (UTC datetimes).
        """
        self._check_event_query(limit, start, end, before_at, before_id)
        return self._store.events(
            limit, start=start, end=end, before_at=before_at, before_id=before_id
        )

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
        """Return one event page with the range total and, with a cursor, the newer count.

        :param limit: Maximum number of events (at least 1).
        :param start: Oldest ``at`` to include (inclusive, timezone-aware).
        :param end: Newest ``at`` to include (inclusive, timezone-aware).
        :param before_at: Keyset cursor instant (timezone-aware); needs ``before_id``.
        :param before_id: Keyset cursor id (at least 0); needs ``before_at``.
        :param page_size: Events per page (at least 1), or ``None`` to skip page numbers.
        :return: The page and the counts (UTC datetimes).
        """
        self._check_event_query(limit, start, end, before_at, before_id)
        if page_size is not None and page_size < 1:
            raise ValidationFailedError(["page_size must be at least 1"])
        return self._store.events_window(
            limit,
            start=start,
            end=end,
            before_at=before_at,
            before_id=before_id,
            page_size=page_size,
        )

    def last_event(self, kind: str, **match: Any) -> StoveEvent | None:
        """Return the newest event of a kind whose detail contains every ``match`` pair.

        :param kind: Event kind.
        :param match: Detail key/value pairs that must all be present.
        :return: The matching event, or ``None``.
        """
        return self._store.last_event(kind, **match)

    def newest_command_event(
        self,
        *,
        command: str,
        results: Sequence[str],
        source: EventSource | None = None,
        after: datetime | None = None,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> StoveEvent | None:
        """Return the newest ``command`` event matching the detail filters.

        ``last_event`` cannot answer this: it ignores the source, it has no time bound,
        and it only inspects the newest candidates of one kind. Paging every event would
        be exact but not one bounded query.

        :param command: Command name stored in the detail.
        :param results: Accepted ``result`` values (at least one).
        :param source: Required source, or ``None`` for any source.
        :param after: Exclusive lower bound on ``at``.
        :param start: Inclusive lower bound on ``at``.
        :param end: Inclusive upper bound on ``at``.
        :return: The matching event, or ``None``.
        :raises ValidationFailedError: A bound is naive or ``results`` is empty.
        """
        issues: list[str] = []
        if not results:
            issues.append("results must not be empty")
        for name, value in (("after", after), ("start", start), ("end", end)):
            if value is not None and (value.tzinfo is None or value.utcoffset() is None):
                issues.append(f"{name} must be timezone-aware")
        if issues:
            raise ValidationFailedError(issues)
        return self._store.newest_command_event(
            command=command,
            results=results,
            source=None if source is None else source.value,
            after=after,
            start=start,
            end=end,
        )

    def last_ignition_at(self) -> datetime | None:
        """Return the time of the last ``ignition`` event.

        :return: Timezone-aware UTC datetime, or ``None`` when there is none.
        """
        event = self._store.last_event(IGNITION_KIND)
        return None if event is None else event.at

    def active_short_cycle(self) -> DisplayMessage | None:
        """Return the short-cycle advice while that alert is active.

        The flag is the stored ``SHORT_CYCLE`` row, loaded at startup and updated
        after each successful alert write, so a restart keeps it without a new sample.

        :return: The message, or ``None`` when the alert is cleared or absent.
        """
        if SHORT_CYCLE_MESSAGE.code not in self._active_alerts:
            return None
        return SHORT_CYCLE_MESSAGE

    def alerts(self, limit: int) -> list[DisplayAlert]:
        """Return the alert catalog, most recently seen first.

        :param limit: Maximum number of rows (at least 1).
        :return: Stored alerts.
        :raises ValidationFailedError: ``limit`` is below 1.
        """
        if limit < 1:
            raise ValidationFailedError(["limit must be at least 1"])
        return self._store.alerts(limit)

    def purge(self, retention_days: int) -> int:
        """Delete samples, events and cleared alerts older than the retention window.

        An alert still shown on the stove (state ``active``) is never deleted.

        :param retention_days: Days to keep, counted back from the clock; 0 keeps everything.
        :return: Total number of deleted rows.
        """
        if retention_days < 0:
            raise ValidationFailedError(["retention_days must be 0 (keep forever) or more"])
        if retention_days == 0:
            return 0
        cutoff = self._now() - timedelta(days=retention_days)
        samples, events, alerts = self._store.purge_before(cutoff)
        removed = samples + events + alerts
        _LOGGER.info("History purge removed %d rows", removed)
        return removed

    @staticmethod
    def _check_event_query(
        limit: int,
        start: datetime | None,
        end: datetime | None,
        before_at: datetime | None,
        before_id: int | None,
    ) -> None:
        issues: list[str] = []
        if limit < 1:
            issues.append("limit must be at least 1")
        if (before_at is None) != (before_id is None):
            issues.append("before_at and before_id must be given together")
        if before_id is not None and before_id < 0:
            issues.append("before_id must be at least 0")
        for name, value in (("start", start), ("end", end), ("before_at", before_at)):
            if value is not None and (value.tzinfo is None or value.utcoffset() is None):
                issues.append(f"{name} must be timezone-aware")
        if start is not None and end is not None and start > end:
            issues.append("start must not be after end")
        if issues:
            raise ValidationFailedError(issues)

    def _reload_active_alerts(self) -> None:
        """Rebuild the in-memory active codes from storage after a failed write.

        :return: None. A reload error is logged and the previous set is kept.
        """
        try:
            self._active_alerts = set(self._store.active_alert_codes())
        except Exception as exc:
            # Reason: best effort only; the caller must not see a second failure.
            _LOGGER.warning("Alert active-set reload failed (error=%s)", type(exc).__name__)

    def _short_cycle_sighting(self, at: datetime) -> AlertSighting | None:
        """Return the short-cycle sighting when the rule says it is raised.

        :param at: Timezone-aware instant of the poll.
        :return: The sighting, or ``None`` when the alert is clear.
        """
        if not self._short_cycle_raised(at):
            return None
        message = SHORT_CYCLE_MESSAGE
        return AlertSighting(message.code, message.title_fr, message.level)

    def _short_cycle_raised(self, at: datetime) -> bool:
        """Evaluate SHORT_CYCLE; a failed sample read or conversion keeps the persisted flag.

        :param at: Timezone-aware instant of the poll.
        :return: Whether the alert should be sighted on this poll.
        """
        held = SHORT_CYCLE_MESSAGE.code in self._active_alerts
        try:
            samples = self._short_cycle_samples(at)
        except (sqlite3.Error, ValueError) as exc:
            # Reason: the store converts every row before returning, so one bad row cannot be skipped here.
            if not self._short_cycle_read_failed:
                self._short_cycle_read_failed = True
                _LOGGER.warning("Short-cycle sample read failed (error=%s)", type(exc).__name__)
            return held
        self._short_cycle_read_failed = False
        decision = evaluate_short_cycle(
            samples,
            min_interval=timedelta(minutes=self._short_cycle_min_interval_min),
            max_gap=SHORT_CYCLE_MAX_GAP,
            held=held,
        )
        return decision.raised

    def _short_cycle_samples(self, at: datetime) -> tuple[CycleSample, ...]:
        """Load the lookback window the short-cycle rule needs.

        :param at: Window end (inclusive, timezone-aware).
        :return: Samples from ``at - min_interval - max_gap`` through ``at``.
        """
        start = at - timedelta(minutes=self._short_cycle_min_interval_min) - SHORT_CYCLE_MAX_GAP
        rows = self.samples(start, at)
        return tuple(CycleSample(row.at, row.state) for row in rows)

    def _now(self) -> datetime:
        now = self._clock()
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("The history clock must return timezone-aware datetimes")
        return now

    @staticmethod
    def _validate_range(start: datetime, end: datetime) -> None:
        issues: list[str] = []
        if start.tzinfo is None or end.tzinfo is None:
            issues.append("start and end must be timezone-aware")
        elif start > end:
            issues.append("start must not be after end")
        if issues:
            raise ValidationFailedError(issues)

    @staticmethod
    def _mean(values: list[float | None]) -> float | None:
        present = [value for value in values if value is not None]
        return fmean(present) if present else None

    @staticmethod
    def _last_present(values: list[float | None]) -> float | None:
        """Return the last value that is not ``None`` (setpoint is a step signal).

        :param values: Readings of one bucket, oldest first.
        :return: The last present value, or ``None``.
        """
        for value in reversed(values):
            if value is not None:
                return value
        return None

    @classmethod
    def _average(
        cls, bucket_start_s: int, group: list[TemperatureSample], start: datetime
    ) -> TemperatureSample:
        return TemperatureSample(
            at=datetime.fromtimestamp(bucket_start_s, tz=start.tzinfo),
            ambient_c=cls._mean([sample.ambient_c for sample in group]),
            fumes_c=cls._mean([sample.fumes_c for sample in group]),
            state=group[-1].state,
            setpoint_c=cls._last_present([sample.setpoint_c for sample in group]),
        )
