"""Audit events of the stove repository: failure-tolerant recording of commands and changes."""

import logging
from typing import Any

from stove_pilot.core.stove.models import EventSource, StoveStatus
from stove_pilot.core.stove.ports import HistoryRecorder

_LOGGER = logging.getLogger(__name__)


class StoveEventRecorder:
    """Records stove events; a history failure is logged and never undoes a stove action."""

    def __init__(self, history: HistoryRecorder) -> None:
        """Bind the history recorder.

        :param history: History recorder.
        """
        self._history = history

    def record(self, kind: str, source: EventSource, **detail: Any) -> None:
        """Record an event.

        :param kind: Event kind.
        :param source: Origin of the event.
        :param detail: JSON-serializable details.
        """
        try:
            self._history.record_event(kind, source, **detail)
        except Exception:  # Reason: the stove was already commanded; the audit write must not mask it.
            _LOGGER.exception("Could not record the %s event", kind)

    def command(
        self,
        command: str,
        source: EventSource,
        result: str,
        reason: str | None,
        *,
        deferred: bool = False,
    ) -> None:
        """Record a ``command`` event.

        :param command: ``start`` or ``stop``.
        :param source: Who asked for the command.
        :param result: Outcome (applied, no_op, deferred, rejected, failed or unknown).
        :param reason: Machine-readable reason, if any.
        :param deferred: Whether it comes from a pending command.
        """
        self.record(
            "command", source, command=command, result=result, reason=reason, deferred=deferred
        )

    def state_change(self, previous: StoveStatus | None, status: StoveStatus) -> None:
        """Record a ``state_changed`` event when the decoded state or the raw code changed.

        :param previous: Previously cached status, if any.
        :param status: Fresh status.
        """
        if previous is None:
            return
        if previous.state is status.state and previous.raw_state_code == status.raw_state_code:
            return
        self.record(
            "state_changed",
            EventSource.POLLER,
            from_code=previous.raw_state_code,
            to_code=status.raw_state_code,
            from_state=previous.state.value,
            to_state=status.state.value,
        )
