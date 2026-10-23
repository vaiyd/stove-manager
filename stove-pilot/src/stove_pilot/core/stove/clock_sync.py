"""Stove clock comparison and synchronization (called by the stove repository under its lock)."""

import logging
import threading
import time
from collections.abc import Callable
from contextlib import AbstractContextManager
from datetime import datetime, timedelta, tzinfo
from functools import partial
from typing import Any, Final

from stove_pilot.common.exceptions import FeatureConflictError, GatewayError, GatewayProtocolError
from stove_pilot.core.stove.clock_schedule import BoundaryClockWriter, next_sync_target
from stove_pilot.core.stove.events import StoveEventRecorder
from stove_pilot.core.stove.models import ClockStatus, EventSource, StoveStatus
from stove_pilot.core.stove.ports import PreferenceStore
from stove_pilot.core.stove.preferences import CLOCK_AUTO_SYNC_KEY
from stove_pilot.core.stove.service import StoveGateway
from stove_pilot.core.stove.setting_rules import clock_sync_due

_LOGGER = logging.getLogger(__name__)

_JOIN_TIMEOUT_S: Final = 5.0


class ClockSynchronizer:
    """Compares the stove clock with the server clock and sets it when it drifts.

    The automatic sync never writes at once: it schedules one write at a minute boundary on a
    dedicated daemon thread (at most one pending), so the poller is never blocked.
    """

    def __init__(
        self,
        gateway: StoveGateway,
        events: StoveEventRecorder,
        clock: Callable[[], datetime],
        tz: tzinfo,
        refresh: Callable[[], StoveStatus],
        *,
        tolerance_s: float,
        min_interval: timedelta,
        auto_sync: bool,
        lock: AbstractContextManager[Any],
        preferences: PreferenceStore | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        """Bind the collaborators and limits.

        :param gateway: Stove gateway (clock write).
        :param events: Event recorder.
        :param clock: Returns the aware server time.
        :param tz: Local timezone used to present the server time.
        :param refresh: Reads the stove now and caches the status.
        :param tolerance_s: Maximum accepted absolute clock drift.
        :param min_interval: Minimum time between two automatic clock syncs.
        :param auto_sync: Initial auto-sync value, used until a stored preference exists.
        :param lock: Gateway lock taken while the scheduled sync writes.
        :param preferences: Store of the runtime auto-sync toggle (``None``: not changeable).
        :param sleep: Sleeps for a number of seconds (injectable for deterministic checks).
        """
        self._gateway = gateway
        self._events = events
        self._clock = clock
        self._tz = tz
        self._refresh = refresh
        self._tolerance_s = tolerance_s
        self._min_interval = min_interval
        self._preferences = preferences
        stored = None if preferences is None else preferences.get_bool(CLOCK_AUTO_SYNC_KEY)
        self._auto_sync = auto_sync if stored is None else stored
        self._writer = BoundaryClockWriter(gateway, lock, clock, sleep)
        self._last_sync_at: datetime | None = None
        self._last_attempt_at: datetime | None = None
        self._state_lock = threading.Lock()
        self._worker: threading.Thread | None = None
        self._cancel = threading.Event()
        self._closed = False

    def status(self, status: StoveStatus | None) -> ClockStatus:
        """Compare a status clock with the server clock.

        :param status: Stove status (``None`` when nothing was read).
        :return: The clock status; ``stove_time`` is naive local time as read from the stove.
        :raises GatewayProtocolError: The stove clock is not mapped or its bytes are invalid.
        """
        if status is None or status.stove_time is None or status.clock_drift_s is None:
            raise GatewayProtocolError("The stove clock is not mapped or unreadable")
        return ClockStatus(
            stove_time=status.stove_time,
            server_time=status.polled_at.astimezone(self._tz),
            drift_s=status.clock_drift_s,
            auto_sync=self._auto_sync,
            last_sync_at=self._last_sync_at,
            drift_tolerance_s=self._tolerance_s,
            auto_sync_writable=self._preferences is not None,
        )

    def set_auto_sync(self, enabled: bool, *, source: EventSource) -> None:
        """Persist the auto-sync toggle; turning it off cancels a pending scheduled sync.

        :param enabled: New value.
        :param source: Who asks for the change.
        :raises FeatureConflictError: No preference store is bound.
        """
        if self._preferences is None:
            raise FeatureConflictError("The auto-sync preference cannot be stored")
        if enabled != self._auto_sync:
            self._preferences.set_bool(CLOCK_AUTO_SYNC_KEY, enabled)
            self._auto_sync = enabled
            self._events.record(
                "setting_changed", source, setting=CLOCK_AUTO_SYNC_KEY, value=enabled
            )
        if not enabled:
            self._cancel.set()

    def sync(self, *, force: bool, source: EventSource) -> ClockStatus:
        """Set the stove clock now when forced or when the drift exceeds the tolerance.

        :param force: Write even when the drift is within the tolerance.
        :param source: Who asks for the sync.
        :return: The clock status after the call.
        """
        current = self._refresh()
        before = current.clock_drift_s
        if not force and (before is None or abs(before) <= self._tolerance_s):
            return self.status(current)
        self._cancel.set()
        fields = self._gateway.write_clock(self._clock())
        after = self._refresh()
        self._last_sync_at = self._clock()
        self._events.record(
            "clock_synced",
            source,
            drift_before_s=before,
            drift_after_s=after.clock_drift_s,
            written_fields=len(fields),
            forced=force,
        )
        return self.status(after)

    def auto_sync(self, cached: StoveStatus | None) -> ClockStatus | None:
        """Schedule a minute-boundary sync when auto-sync is on, the drift is too large and due.

        A failed attempt is not retried before the minimum interval has elapsed again.

        :param cached: Cached stove status, if any.
        :return: Always ``None``: the write happens later on the sync thread.
        """
        if not self._auto_sync or cached is None:
            return None
        now = self._clock()
        if not clock_sync_due(
            cached.clock_drift_s,
            tolerance_s=self._tolerance_s,
            last_attempt_at=self._last_attempt_at,
            now=now,
            min_interval=self._min_interval,
        ):
            return None
        with self._state_lock:
            if self._closed or (self._worker is not None and self._worker.is_alive()):
                return None
            self._last_attempt_at = now
            self._cancel = cancel = threading.Event()
            self._worker = threading.Thread(
                target=self._run_scheduled,
                args=(cached.clock_drift_s, next_sync_target(now), cancel),
                name="clock-sync",
                daemon=True,
            )
            self._worker.start()
        return None

    def stop(self) -> None:
        """Cancel the pending scheduled sync and wait for its thread (idempotent)."""
        with self._state_lock:
            self._closed = True
            self._cancel.set()
            worker = self._worker
        if worker is not None and worker.is_alive():
            worker.join(timeout=_JOIN_TIMEOUT_S)

    def _cancelled(self, cancel: threading.Event) -> bool:
        """Tell whether a scheduled sync must stop (cancelled, shutdown or auto-sync turned off).

        :param cancel: Cancellation flag of the sync.
        :return: ``True`` when it must stop.
        """
        return cancel.is_set() or not self._auto_sync

    def _run_scheduled(
        self, drift_before: float | None, target: datetime, cancel: threading.Event
    ) -> None:
        """Thread body: write at the boundary, read back and record the event.

        :param drift_before: Drift that triggered the sync, in seconds.
        :param target: Minute boundary from ``next_sync_target``.
        :param cancel: Set when this sync is cancelled.
        """
        try:
            written = self._writer.run(target, partial(self._cancelled, cancel))
        except GatewayError as exc:
            _LOGGER.warning("Scheduled clock sync failed (%s); not retried", type(exc).__name__)
            return
        except Exception:
            _LOGGER.error("Scheduled clock sync failed unexpectedly", exc_info=True)
            return
        if written is None:
            _LOGGER.info("Scheduled clock sync cancelled before any write")
            return
        self._last_sync_at = self._clock()
        drift_after = self._read_back_drift()
        self._events.record(
            "clock_synced",
            EventSource.SYSTEM,
            drift_before_s=drift_before,
            drift_after_s=drift_after,
            written_fields=len(written),
            forced=False,
            scheduled_for=target.isoformat(),
            write_latency_s=round(self._writer.latency_s, 3),
        )

    def _read_back_drift(self) -> float | None:
        """Read the stove after the write.

        :return: The resulting drift in seconds, or ``None`` when the read failed.
        """
        try:
            return self._refresh().clock_drift_s
        except GatewayError as exc:
            _LOGGER.warning("Clock read-back after the sync failed (%s)", type(exc).__name__)
            return None
