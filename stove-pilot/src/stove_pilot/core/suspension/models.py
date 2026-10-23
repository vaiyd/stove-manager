"""Suspension models: the persisted state and the outcome of a suspend call."""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Final, Literal

from stove_pilot.core.stove.models import CommandOutcome

KIND_VACATION: Final = "vacation"
PHASE_ACTIVATING: Final = "activating"
PHASE_ACTIVE: Final = "active"
PHASE_RESTORING: Final = "restoring"
WARNING_UNMAPPED: Final = "energy_saving_unmapped"


@dataclass(frozen=True, slots=True)
class SuspensionState:
    """Whether programming is suspended; ``snapshots`` maps a participant name to its snapshot.

    ``kind="vacation"`` also carries the frost setpoint and the stove values to restore.
    ``phase`` is the write-ahead journal position: ``activating`` (rollback pending), ``active``
    (steady state) or ``restoring`` (restore under way).
    """

    suspended: bool = False
    since: datetime | None = None
    until: datetime | None = None
    thermostat_mode: bool = True
    snapshots: dict[str, dict[str, Any]] = field(default_factory=dict)
    kind: Literal["programming", "vacation"] = "programming"
    frost_setpoint_c: float | None = None
    stove_snapshot: dict[str, Any] = field(default_factory=dict)
    phase: Literal["activating", "active", "restoring"] = PHASE_ACTIVE


@dataclass(frozen=True, slots=True)
class SuspendOutcome:
    """Result of a suspend call: the new state, the thermostat start (if any) and warnings."""

    state: SuspensionState
    start_outcome: CommandOutcome | None
    warnings: tuple[str, ...]
