"""Deferred stove commands: one slot per command, with a lifetime per command."""

from collections.abc import Mapping
from datetime import datetime, timedelta

from stove_pilot.core.stove.events import StoveEventRecorder
from stove_pilot.core.stove.models import EventSource, PendingCommand


class PendingCommands:
    """Holds at most one deferred command per command name and expires them."""

    def __init__(self, events: StoveEventRecorder, lifetimes: Mapping[str, timedelta]) -> None:
        """Bind the event recorder and the lifetime of each command.

        :param events: Stove event recorder.
        :param lifetimes: Lifetime of a deferred command, keyed by command name.
        """
        self._events = events
        self._lifetimes = dict(lifetimes)
        self._pending: dict[str, PendingCommand | None] = dict.fromkeys(self._lifetimes)

    def get(self, command: str) -> PendingCommand | None:
        """Return the deferred command, if any.

        :param command: Command name.
        :return: The pending command or ``None``.
        """
        return self._pending[command]

    def has_any(self) -> bool:
        """Tell whether a command is deferred.

        :return: ``True`` when at least one command is pending.
        """
        return any(self._pending.values())

    def clear(self, command: str) -> None:
        """Drop the deferred command, if any.

        :param command: Command name.
        """
        self._pending[command] = None

    def defer(self, command: str, source: EventSource, now: datetime) -> None:
        """Defer a command for its lifetime.

        :param command: Command name.
        :param source: Who asked for the command.
        :param now: Current time (aware).
        """
        expires_at = now + self._lifetimes[command]
        self._pending[command] = PendingCommand(command, source, now, expires_at)

    def expire(self, now: datetime) -> None:
        """Drop the deferred commands whose lifetime elapsed and record each one.

        :param now: Current time (aware).
        """
        for command, pending in self._pending.items():
            if pending is not None and now >= pending.expires_at:
                self._pending[command] = None
                self._events.record(
                    "pending_expired",
                    pending.source,
                    command=command,
                    requested_at=pending.requested_at.isoformat(),
                )
