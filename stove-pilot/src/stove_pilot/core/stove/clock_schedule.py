"""Minute-boundary clock write: target selection and the timed two-phase write."""

from collections.abc import Callable
from contextlib import AbstractContextManager
from datetime import UTC, datetime, timedelta
from typing import Any, Final

from stove_pilot.core.stove.models import ClockWrite
from stove_pilot.core.stove.service import StoveGateway

# Reason: the target must be far enough away to read, plan and write the other bytes first.
MIN_LEAD_S: Final = 3.0
# Reason: the module needs about 0.12 s + 0.05 s per item, so read plus a few writes fit in 2 s.
EARLY_LEAD_S: Final = 2.0
# Reason: conservative round trip of one single-byte module write until one has been observed.
DEFAULT_WRITE_LATENCY_S: Final = 0.15
MAX_WRITE_LATENCY_S: Final = 1.0
# Reason: a cancellation is noticed within one slice while waiting.
WAIT_SLICE_S: Final = 0.25
MINUTE_FIELD: Final = "minute"
_MINUTE: Final = timedelta(minutes=1)


def next_sync_target(now: datetime, *, min_lead_s: float = MIN_LEAD_S) -> datetime:
    """Pick the minute boundary at which the stove minute is set.

    It is the next boundary at least ``min_lead_s`` away, moved one more minute when its minute
    is 00, so that no hour or date byte changes exactly at the rollover.

    :param now: Aware server time.
    :param min_lead_s: Smallest accepted distance to the boundary in seconds.
    :return: The aware boundary in the timezone of ``now``.
    """
    moment = now.astimezone(UTC)
    boundary = moment.replace(second=0, microsecond=0) + _MINUTE
    if boundary - moment < timedelta(seconds=min_lead_s):
        boundary += _MINUTE
    if boundary.astimezone(now.tzinfo).minute == 0:
        boundary += _MINUTE
    return boundary.astimezone(now.tzinfo)


class BoundaryClockWriter:
    """Writes the stove clock so that its minute is set when the server clock rolls over.

    About ``EARLY_LEAD_S`` before the target every byte but the minutes is written, computed for
    the target. The minutes byte is then sent so that its request ends at the target. The lock
    is held only while writing, never while waiting.
    """

    def __init__(
        self,
        gateway: StoveGateway,
        lock: AbstractContextManager[Any],
        clock: Callable[[], datetime],
        sleep: Callable[[float], None],
    ) -> None:
        """Bind the collaborators.

        :param gateway: Stove gateway (clock plan and write).
        :param lock: Gateway lock taken for the writes.
        :param clock: Returns the aware server time.
        :param sleep: Sleeps for a number of seconds.
        """
        self._gateway = gateway
        self._lock = lock
        self._clock = clock
        self._sleep = sleep
        self._latency_s = DEFAULT_WRITE_LATENCY_S

    @property
    def latency_s(self) -> float:
        """Latest observed (or default) duration of one single-byte write in seconds."""
        return self._latency_s

    def run(self, target: datetime, cancelled: Callable[[], bool]) -> list[str] | None:
        """Wait, write the early bytes, wait again and write the minutes byte.

        A cancellation before any byte is written aborts without a write. Once a byte is
        written, the minutes byte is still written (at once) so the clock is never left half set.

        :param target: Minute boundary from ``next_sync_target``.
        :param cancelled: Tells whether the sync was cancelled (disabled, replaced or shutdown).
        :return: Names of the written fields, or ``None`` when cancelled before any write.
        :raises GatewayError: A plan, write or read-back failed (never retried here).
        """
        target_utc = target.astimezone(UTC)
        if not self._wait_until(target_utc - timedelta(seconds=EARLY_LEAD_S), cancelled):
            return None
        with self._lock:
            plan = self._gateway.plan_clock_write(target)
            early = [write for write in plan if write.name != MINUTE_FIELD]
            minute = [write for write in plan if write.name == MINUTE_FIELD]
            written = self._write_timed(early)
        send_at = target_utc - timedelta(seconds=self._latency_s)
        if not self._wait_until(send_at, cancelled) and not written:
            return None
        with self._lock:
            return written + self._write_timed(minute)

    def _wait_until(self, deadline: datetime, cancelled: Callable[[], bool]) -> bool:
        """Sleep in short slices until a deadline.

        :param deadline: Aware time to reach.
        :param cancelled: Tells whether to stop waiting.
        :return: ``True`` when the deadline was reached, ``False`` when cancelled first.
        """
        while not cancelled():
            remaining = (deadline - self._clock().astimezone(UTC)).total_seconds()
            if remaining <= 0:
                return True
            self._sleep(min(remaining, WAIT_SLICE_S))
        return False

    def _write_timed(self, writes: list[ClockWrite]) -> list[str]:
        """Write bytes and keep the observed per-write duration as the next latency estimate.

        :param writes: Planned writes.
        :return: Names of the written fields.
        """
        if not writes:
            return []
        started = self._clock()
        names = self._gateway.apply_clock_writes(writes, max_rewrites=0)
        elapsed = (self._clock() - started).total_seconds() / len(writes)
        self._latency_s = min(max(elapsed, 0.0), MAX_WRITE_LATENCY_S)
        return names
