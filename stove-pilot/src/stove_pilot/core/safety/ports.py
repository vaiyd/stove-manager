"""Ports the safety facade adapts over the stove, chrono, and history."""

from collections.abc import Callable
from datetime import datetime
from typing import Protocol

from stove_pilot.core.stove.models import CommandOutcome, EventSource, PendingCommand, StoveStatus
from stove_pilot.core.stove.rules import GuardResult

# True only when the active suspension is a vacation. A programming suspension stays false.
VacationCheck = Callable[[], bool]

# Stove-local instants of the previous and current poll. None means the match cannot be known.
ChronoStopCheck = Callable[[datetime, datetime], bool | None]


class SafetyStovePort(Protocol):
    """Guarded stove commands the safety facade is allowed to use."""

    def start(self, *, source: EventSource) -> CommandOutcome:
        """Start the stove through the existing repository guards.

        :param source: Command origin. Safety always passes ``SYSTEM``.
        :return: Outcome of the guarded start.
        """
        ...

    def pending_start(self) -> PendingCommand | None:
        """Return the deferred start still waiting, if any.

        :return: The pending start, or ``None``.
        """
        ...

    def is_stale(self) -> bool:
        """Tell whether the cached status is missing or too old.

        :return: ``True`` when the safety view should report ``stale``.
        """
        ...

    def get_status(self, max_age_s: float | None = None) -> StoveStatus:
        """Return the cached status. ``None`` accepts any age and does not live-read.

        :param max_age_s: Maximum accepted cache age in seconds.
        :return: The cached status when one exists.
        """
        ...

    def guard_start(self, status: StoveStatus) -> GuardResult:
        """Return the guard decision for an already-read status. No stove read.

        :param status: Status from the poll.
        :return: Guard decision (apply, defer, no-op or reject).
        """
        ...

    def preview_start(self) -> GuardResult:
        """Return the guard decision a start would get now, without writing.

        A ``defer`` decision must not call ``start``.

        :return: Guard decision (apply, defer, no-op or reject).
        """
        ...


class StopCommandLookup(Protocol):
    """History queries used to attribute a stop before the rules run."""

    def newest_user_stop_since(self, since: datetime | None) -> datetime | None:
        """Return the newest processed user stop strictly after ``since``.

        A matching event has ``command=stop``, ``source=USER`` and result ``applied``,
        ``unknown`` or ``no_op``.

        :param since: Exclusive lower bound; ``None`` means the whole history.
        :return: Event time, or ``None`` when no such stop exists.
        """
        ...

    def server_stop_within(
        self, now: datetime, minutes: int, *, not_before: datetime | None = None
    ) -> tuple[str, datetime] | None:
        """Return the newest stop that can explain a transition, with its source.

        A stop at or before ``not_before`` cannot explain a later transition.
        Only results ``applied`` and ``unknown`` count.

        :param now: End of the lookback window.
        :param minutes: Window length.
        :param not_before: Exclusive lower bound, usually the previous poll.
        :return: ``(source, at)``, or ``None``.
        """
        ...


class SafetyEventSink(Protocol):
    """History sink for safety events."""

    def record(self, kind: str, source: EventSource, **detail: object) -> None:
        """Append one safety event.

        :param kind: Event kind.
        :param source: Origin. Safety events use ``SYSTEM``.
        :param detail: JSON-serializable details (attempt, threshold, reason).
        """
        ...
