"""Vacation use case: frost setpoint, ENERGY SAVING STOP and the restore of what was changed."""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Final

from stove_pilot.common.exceptions import FeatureConflictError, GatewayUnavailableError
from stove_pilot.core.stove.events import StoveEventRecorder
from stove_pilot.core.stove.models import EventSource, StoveStatus
from stove_pilot.core.stove.repository import StoveRepository
from stove_pilot.core.suspension.models import (
    KIND_VACATION,
    PHASE_ACTIVATING,
    WARNING_UNMAPPED,
    SuspensionState,
)
from stove_pilot.core.suspension.participants import ParticipantGroup

STOP_OFFSET_C: Final = 2
TRIGGER_SETPOINT: Final = "setpoint"
TRIGGER_PARTICIPANT: Final = "participant"


@dataclass(frozen=True, slots=True)
class VacationActivation:
    """Captured activation: the state to journal and the status read before the changes."""

    state: SuspensionState
    status: StoveStatus


@dataclass(frozen=True, slots=True)
class VacationOverride:
    """An early return: what triggered it and the event details."""

    trigger: str
    detail: dict[str, Any] = field(default_factory=dict)

    @property
    def keeps_setpoint(self) -> bool:
        """Tell whether the setpoint changed on the panel is kept (not restored)."""
        return self.trigger == TRIGGER_SETPOINT


class VacationMode:
    """Applies the frost values on start and restores them on return (called under the lock)."""

    def __init__(
        self,
        stove: StoveRepository,
        group: ParticipantGroup,
        events: StoveEventRecorder,
        frost_setpoint_c: float,
    ) -> None:
        """Bind the collaborators.

        :param stove: Stove repository (settings writes and cached status).
        :param group: Suspendable participants.
        :param events: Event recorder.
        :param frost_setpoint_c: Setpoint applied during the vacation.
        """
        self._stove = stove
        self._group = group
        self._events = events
        self._frost_setpoint_c = frost_setpoint_c

    def capture(self, *, until: datetime | None, now: datetime) -> VacationActivation:
        """Read every "before" value and build the ``activating`` state (no write).

        :param until: Aware time of the automatic end, or ``None``.
        :param now: Aware server time.
        :return: The state to journal and the status read before the changes.
        :raises FeatureConflictError: ENERGY SAVING STOP is unmapped or not writable.
        :raises GatewayError: A read failed (nothing was changed).
        """
        status = self._stove.refresh(full=True)
        if not self._stove.settings().energy_stop_writable:
            raise FeatureConflictError(WARNING_UNMAPPED)
        energy = status.energy_saving
        if energy is None:
            # Reason: STOP is mapped and writable, so a missing value is a failed slow read.
            raise GatewayUnavailableError("ENERGY SAVING could not be read")
        stove_snapshot = {
            "setpoint_c": status.setpoint_c,
            "energy_stop_offset_c": energy.stop_offset_c,
            "energy_stop_changed": energy.stop_offset_c is None,
        }
        state = SuspensionState(
            True,
            now,
            until,
            True,
            self._group.snapshot_active(),
            KIND_VACATION,
            self._frost_setpoint_c,
            stove_snapshot,
            phase=PHASE_ACTIVATING,
        )
        return VacationActivation(state, status)

    def apply(self, state: SuspensionState) -> None:
        """Suspend the participants, write the frost setpoint, then STOP if it was OFF.

        :param state: The journaled ``activating`` state.
        :raises GatewayError: A write failed (the caller compensates from the state).
        """
        self._group.suspend(state.snapshots)
        user = EventSource.USER
        self._stove.set_setpoint(self._frost_setpoint_c, source=user, bypass_lock=True)
        if state.stove_snapshot["energy_stop_changed"]:
            self._stove.set_energy_stop(STOP_OFFSET_C, source=user, bypass_lock=True)

    def record_started(self, state: SuspensionState) -> None:
        """Record the ``vacation_started`` event of an activated state.

        :param state: The ``active`` vacation state.
        """
        until = state.until
        self._events.record(
            "vacation_started",
            EventSource.USER,
            until=None if until is None else until.isoformat(),
            frost_setpoint_c=self._frost_setpoint_c,
            previous_setpoint_c=state.stove_snapshot["setpoint_c"],
            energy_stop_enabled=state.stove_snapshot["energy_stop_changed"],
            participants=sorted(state.snapshots),
        )

    def restore(self, state: SuspensionState, *, source: EventSource, setpoint: bool) -> None:
        """Restore the setpoint (optional), STOP (if changed by the vacation), then participants.

        :param state: The vacation state.
        :param source: Origin of the restore writes.
        :param setpoint: Restore the setpoint too (``False`` keeps the panel value).
        :raises GatewayError: A write failed (the caller keeps the state and retries).
        """
        if setpoint:
            self._restore_setpoint(state.stove_snapshot, source)
        self._restore_stop(state.stove_snapshot, source)
        self._group.restore(state.snapshots)

    def detect_override(self, state: SuspensionState) -> VacationOverride | None:
        """Detect an early return: setpoint changed on the panel, or a participant re-enabled.

        The setpoint check uses the cached status and is skipped while it is stale.

        :param state: The vacation state.
        :return: The override, or ``None`` when the vacation goes on.
        """
        if state.frost_setpoint_c is not None and not self._stove.is_stale():
            current = self._stove.get_status().setpoint_c
            if current != state.frost_setpoint_c:
                return VacationOverride(TRIGGER_SETPOINT, {"setpoint_c": current})
        active = self._group.active_names()
        if active:
            return VacationOverride(TRIGGER_PARTICIPANT, {"participants": active})
        return None

    def _restore_setpoint(self, stove_snapshot: dict[str, Any], source: EventSource) -> None:
        self._stove.set_setpoint(stove_snapshot["setpoint_c"], source=source, bypass_lock=True)

    def _restore_stop(self, stove_snapshot: dict[str, Any], source: EventSource) -> None:
        if stove_snapshot.get("energy_stop_changed"):
            self._stove.set_energy_stop(
                stove_snapshot["energy_stop_offset_c"], source=source, bypass_lock=True
            )
