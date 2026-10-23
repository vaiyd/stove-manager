"""Pure display-alert rules: when a code is a new appearance and when it clears.

A new appearance is counted only after the code was absent from at least one poll.
Several codes may be shown together; a code missing from the current poll clears.
"""

from collections.abc import Sequence, Set
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class AlertSighting:
    """One display message observed on a poll."""

    code: str
    title: str
    severity: str


@dataclass(frozen=True, slots=True)
class AlertChange:
    """One row write decided for a poll.

    ``appear`` inserts the code or increments its occurrence count. ``clear`` marks it
    absent. A code that stayed visible has both flags false and only refreshes last-seen.
    """

    code: str
    title: str
    severity: str
    appear: bool = False
    clear: bool = False


def plan_alert_changes(
    active: Set[str], sightings: Sequence[AlertSighting]
) -> tuple[tuple[AlertChange, ...], frozenset[str]]:
    """Decide the writes that move ``active`` to the codes shown on this poll.

    :param active: Codes whose persisted state was ``active`` before this poll.
    :param sightings: Messages shown now (empty when the display has none).
    :return: The writes, and the codes that are active after this poll.
    """
    current: dict[str, AlertSighting] = {}
    for sighting in sightings:
        if sighting.code and sighting.code not in current:
            current[sighting.code] = sighting
    changes: list[AlertChange] = []
    for code, sighting in current.items():
        changes.append(
            AlertChange(code, sighting.title, sighting.severity, appear=code not in active)
        )
    for code in sorted(active.difference(current)):
        changes.append(AlertChange(code, "", "", clear=True))
    return tuple(changes), frozenset(current)
