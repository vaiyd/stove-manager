"""The set of suspendable participants: shared snapshot, suspend and restore logic."""

import logging
from collections.abc import Sequence
from typing import Any

from stove_pilot.core.suspension.ports import Suspendable

_LOGGER = logging.getLogger(__name__)


class ParticipantGroup:
    """Snapshots, suspends and restores the programming features as one unit."""

    def __init__(self, participants: Sequence[Suspendable]) -> None:
        """Bind the participants.

        :param participants: Programming features that can be suspended.
        """
        self._participants = tuple(participants)

    def active_names(self) -> list[str]:
        """List the participants that currently drive the stove.

        :return: Names of the active participants.
        """
        return [p.name for p in self._participants if p.is_active()]

    def snapshot_active(self) -> dict[str, dict[str, Any]]:
        """Read what each active participant would switch off (no write).

        :return: Snapshots keyed by participant name.
        """
        return {p.name: p.snapshot() for p in self._participants if p.is_active()}

    def suspend(self, snapshots: dict[str, dict[str, Any]]) -> None:
        """Switch off exactly what the snapshots list; the first failure is raised.

        :param snapshots: Snapshots keyed by participant name.
        """
        for participant in self._participants:
            snapshot = snapshots.get(participant.name)
            if snapshot is not None:
                participant.suspend(snapshot)

    def restore(self, snapshots: dict[str, dict[str, Any]]) -> None:
        """Resume the snapshots; the first failure is raised.

        :param snapshots: Snapshots keyed by participant name.
        """
        by_name = {p.name: p for p in self._participants}
        for name, snapshot in snapshots.items():
            participant = by_name.get(name)
            if participant is None:
                _LOGGER.warning("Participant %s is gone; its snapshot is dropped", name)
                continue
            participant.resume(snapshot)
