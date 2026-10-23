"""Safety view of the stove: guarded commands plus a start preview.

The preview calls :func:`decide_start`. ``StoveRepository`` is not extended.
"""

from collections.abc import Callable
from datetime import datetime, timedelta

from stove_pilot.core.history.repository import HistoryRepository
from stove_pilot.core.stove.models import CommandOutcome, EventSource, PendingCommand, StoveStatus
from stove_pilot.core.stove.repository import StoveRepository
from stove_pilot.core.stove.rules import GuardPolicy, GuardResult, decide_start


class SafetyStoveAdapter:
    """Stove port that can say whether a start would be sent now."""

    def __init__(
        self,
        stove: StoveRepository,
        history: HistoryRepository,
        min_restart_interval: timedelta,
        clock: Callable[[], datetime],
    ) -> None:
        """Bind the stove, the ignition history and the restart floor.

        :param stove: Guarded stove repository.
        :param history: History used for the last ignition time.
        :param min_restart_interval: Same floor the stove guard uses.
        :param clock: Returns the aware server time.
        """
        self._stove = stove
        self._history = history
        self._policy = GuardPolicy(min_restart_interval=min_restart_interval)
        self._clock = clock

    def guard_start(self, status: StoveStatus) -> GuardResult:
        """Return the guard decision for a status that was already read.

        :param status: Poll status. This method does not read the stove.
        :return: Apply, defer, no-op or reject. Nothing is written.
        """
        return self._decide(status)

    def preview_start(self) -> GuardResult:
        """Return the guard decision a start would get right now.

        The read is unthrottled so a degraded display is visible before the start.

        :return: Apply, defer, no-op or reject. Nothing is written.
        """
        return self._decide(self._stove.refresh())

    def _decide(self, status: StoveStatus) -> GuardResult:
        return decide_start(
            status,
            last_ignition_at=self._history.last_ignition_at(),
            now=self._clock(),
            policy=self._policy,
        )

    def start(self, *, source: EventSource) -> CommandOutcome:
        """Start through the stove repository.

        :param source: Command origin.
        :return: The guarded outcome.
        """
        return self._stove.start(source=source)

    def pending_start(self) -> PendingCommand | None:
        """Return the deferred start, if any.

        :return: The pending start, or ``None``.
        """
        return self._stove.pending_start()

    def is_stale(self) -> bool:
        """Tell whether the cached status is missing, old or degraded.

        :return: ``True`` when the safety check must do nothing.
        """
        return self._stove.is_stale()

    def get_status(self, max_age_s: float | None = None) -> StoveStatus:
        """Return the cached status, or one throttled live read.

        :param max_age_s: Maximum accepted cache age in seconds.
        :return: The status.
        """
        return self._stove.get_status(max_age_s)
