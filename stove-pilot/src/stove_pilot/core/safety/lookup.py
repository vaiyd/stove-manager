"""Exact stop-command lookups over the history repository."""

from datetime import datetime, timedelta

from stove_pilot.core.history.repository import HistoryRepository
from stove_pilot.core.safety.detection import is_user_app_stop
from stove_pilot.core.stove.models import EventSource

_STOP = "stop"
_USER_RESULTS = ("applied", "unknown", "no_op")
_EXPLAIN_RESULTS = ("applied", "unknown")


class HistoryStopLookup:
    """History queries that attribute a stop before the safety rules run.

    Both lookups are one bounded query. ``last_event`` is not exact here: it cannot
    filter on source, it has no time bound, and it only sees the newest candidates.
    """

    def __init__(self, history: HistoryRepository) -> None:
        """Bind the history repository.

        :param history: History facade.
        """
        self._history = history

    def newest_user_stop_since(self, since: datetime | None) -> datetime | None:
        """Return the newest user stop strictly after ``since``.

        The row is kept only when :func:`is_user_app_stop` accepts it, so an older
        user stop is not hidden behind a newer stop from another source.

        :param since: Exclusive lower bound; ``None`` means the whole history.
        :return: Event time, or ``None``.
        """
        event = self._history.newest_command_event(
            command=_STOP,
            results=_USER_RESULTS,
            source=EventSource.USER,
            after=since,
        )
        if event is None or not self._is_user_stop(event.detail, event.source):
            return None
        return event.at

    def server_stop_within(
        self, now: datetime, minutes: int, *, not_before: datetime | None = None
    ) -> tuple[str, datetime] | None:
        """Return the source and time of the newest stop that can explain a transition.

        The window is the last ``minutes`` ending at ``now``. ``not_before`` is
        exclusive, so a stop at or before the previous poll cannot explain a
        later transition. Only results ``applied`` and ``unknown`` count.
        ``no_op`` does not explain a panel stop.

        :param now: End of the lookback window.
        :param minutes: Window length.
        :param not_before: Exclusive lower bound, usually the previous poll.
        :return: ``(source, at)``, or ``None``.
        """
        start = now - timedelta(minutes=minutes)
        event = self._history.newest_command_event(
            command=_STOP,
            results=_EXPLAIN_RESULTS,
            after=not_before,
            start=start,
            end=now,
        )
        if event is None or not self._is_counted_stop(event.detail):
            return None
        return str(event.source), event.at

    @staticmethod
    def _is_user_stop(detail: dict[str, object], source: object) -> bool:
        command = detail.get("command")
        result = detail.get("result")
        if not isinstance(command, str) or not isinstance(result, str):
            return False
        return is_user_app_stop(command, str(source), result)

    @staticmethod
    def _is_counted_stop(detail: dict[str, object]) -> bool:
        return detail.get("command") == _STOP and detail.get("result") in _EXPLAIN_RESULTS
