"""Outbound ports of the stove feature: raw register access and the history recorder."""

from collections.abc import Sequence
from datetime import datetime
from typing import Any, Protocol

from stove_pilot.core.stove.messages import DisplayMessage
from stove_pilot.core.stove.models import EventSource, StoveStatus, WriteOp
from stove_pilot.lib.navel.rwms import Memory


class RegisterIO(Protocol):
    """Read and write single-byte registers of the stove board."""

    def read(self, targets: Sequence[tuple[Memory, int]]) -> list[int]:
        """Read registers.

        :param targets: Memory area and address of each register.
        :return: One byte value per target, in order.
        """
        ...

    def write(self, ops: Sequence[WriteOp]) -> None:
        """Write registers once, never retrying.

        :param ops: Single-byte writes, applied in order.
        :raises WriteOutcomeUnknownError: When the outcome cannot be determined.
        """
        ...


class PreferenceStore(Protocol):
    """Persistent runtime preferences (small key/value flags that survive a restart)."""

    def get_bool(self, key: str) -> bool | None:
        """Read a flag.

        :param key: Preference name.
        :return: The stored value, or ``None`` when nothing is stored.
        """
        ...

    def set_bool(self, key: str, value: bool) -> None:
        """Store a flag.

        :param key: Preference name.
        :param value: New value.
        """
        ...


class HistoryRecorder(Protocol):
    """History facade used by the stove repository and poller (implemented by ``core/history``)."""

    def record_sample(self, status: StoveStatus) -> bool:
        """Store a temperature sample.

        :param status: Stove status snapshot.
        :return: ``True`` when a new row was stored.
        """
        ...

    def record_alerts(self, messages: Sequence[DisplayMessage], *, at: datetime) -> None:
        """Record the display messages shown on this poll.

        An empty sequence means the display has no decoded message. Several codes are accepted.

        :param messages: Decoded messages (one today).
        :param at: Timezone-aware instant of the poll.
        """
        ...

    def record_event(self, kind: str, source: EventSource, **detail: Any) -> None:
        """Store an event at the current time.

        :param kind: Event kind.
        :param source: Origin of the event.
        :param detail: JSON-serializable details.
        """
        ...

    def last_ignition_at(self) -> datetime | None:
        """Return the time of the last ``ignition`` event.

        :return: Aware datetime, or ``None``.
        """
        ...

    def purge(self, retention_days: int) -> int:
        """Delete rows older than the retention window.

        :param retention_days: Days to keep; 0 keeps everything.
        :return: Number of deleted rows.
        """
        ...
