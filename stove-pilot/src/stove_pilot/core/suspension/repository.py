"""Application facade of the suspension: atomic suspend, resume, auto-resume and override check."""

import logging
import threading
from collections.abc import Callable, Sequence
from dataclasses import replace
from datetime import datetime
from functools import partial
from typing import Any, Final

from stove_pilot.common.exceptions import (
    CommandRejectedError,
    FeatureConflictError,
    GatewayError,
    UnverifiedRegisterError,
    ValidationFailedError,
)
from stove_pilot.core.stove.events import StoveEventRecorder
from stove_pilot.core.stove.models import CommandOutcome, EventSource, StoveState, StoveStatus
from stove_pilot.core.stove.ports import HistoryRecorder
from stove_pilot.core.stove.repository import StoveRepository
from stove_pilot.core.suspension.models import (
    KIND_VACATION,
    PHASE_ACTIVATING,
    PHASE_ACTIVE,
    PHASE_RESTORING,
    WARNING_UNMAPPED,
    SuspendOutcome,
    SuspensionState,
)
from stove_pilot.core.suspension.participants import ParticipantGroup
from stove_pilot.core.suspension.ports import Suspendable
from stove_pilot.core.suspension.service import SuspensionStore
from stove_pilot.core.suspension.vacation import VacationMode, VacationOverride

_LOGGER = logging.getLogger(__name__)

ALREADY_SUSPENDED: Final = "already_suspended"
NOT_SUSPENDED: Final = "not_suspended"
VACATION_ACTIVE: Final = "vacation_active"
WARNING_STOP_OFF: Final = "energy_saving_stop_off"
WARNING_START_REJECTED: Final = "thermostat_start_rejected"
WARNING_START_FAILED: Final = "thermostat_start_failed"
WARNING_VACATION_NOT_STARTED: Final = "vacation_stove_not_started"
# Reason: mirror the start guard (core/stove/rules.py): OFF starts, SHUTDOWN is deferred, these run.
_VACATION_START_STATES: Final = frozenset({StoveState.OFF, StoveState.SHUTDOWN})
_RUNNING_STATES: Final = frozenset(
    {StoveState.IGNITION, StoveState.RUNNING, StoveState.CLEANING, StoveState.STANDBY}
)
_REASON_UNTIL: Final = "until"
_REASON_RECOVERED: Final = "recovered"
_REASON_INTERRUPTED: Final = "interrupted_activation"


class SuspensionRepository:
    """Single entry point to suspend and resume every programming participant.

    The state lives in memory and in SQLite. ``get`` and ``is_suspended`` never take the lock,
    because a participant calls ``is_suspended`` while holding its own lock.
    """

    def __init__(
        self,
        store: SuspensionStore,
        participants: Sequence[Suspendable],
        stove: StoveRepository,
        history: HistoryRecorder,
        clock: Callable[[], datetime],
        vacation_setpoint_c: float,
    ) -> None:
        """Bind the collaborators and load the persisted state.

        :param store: Suspension SQL adapter.
        :param participants: Programming features that can be suspended.
        :param stove: Stove repository (thermostat start and ENERGY SAVING check).
        :param history: History recorder (events).
        :param clock: Returns the aware server time.
        :param vacation_setpoint_c: Frost setpoint applied by the vacation mode.
        """
        self._store = store
        self._group = ParticipantGroup(participants)
        self._stove = stove
        self._events = StoveEventRecorder(history)
        self._vacation = VacationMode(stove, self._group, self._events, vacation_setpoint_c)
        self._clock = clock
        self._lock = threading.RLock()
        self._state = store.load()

    def get(self) -> SuspensionState:
        """Return the current state (no I/O).

        :return: The suspension state.
        """
        return self._state

    def is_suspended(self) -> bool:
        """Tell whether programming is suspended (no I/O, never blocks).

        :return: ``True`` while suspended.
        """
        return self._state.suspended

    def suspend(self, *, until: datetime | None, thermostat_mode: bool) -> SuspendOutcome:
        """Switch every active participant off and optionally start the stove as a thermostat.

        Journaled: snapshots are saved as ``activating`` before any write, then ``active``.

        :param until: Aware time of the automatic resume, or ``None`` for manual resume only.
        :param thermostat_mode: Start the stove when OFF so it regulates through ENERGY SAVING.
        :return: New state, thermostat start outcome (if any) and warnings.
        :raises FeatureConflictError: Already suspended.
        :raises ValidationFailedError: ``until`` is naive or not in the future.
        :raises GatewayError: A read or write failed (what was changed is restored).
        """
        with self._lock:
            if self._state.suspended:
                raise FeatureConflictError(ALREADY_SUSPENDED)
            now = self._clock()
            self._check_until(until, now)
            status = self._stove.get_status(max_age_s=0) if thermostat_mode else None
            snapshots = self._group.snapshot_active()
            activating = SuspensionState(
                True, now, until, thermostat_mode, snapshots, phase=PHASE_ACTIVATING
            )
            state = self._activate(activating, partial(self._group.suspend, snapshots))
            self._events.record(
                "programming_suspended",
                EventSource.USER,
                until=None if until is None else until.isoformat(),
                thermostat_mode=thermostat_mode,
                participants=sorted(snapshots),
            )
            warnings, outcome = self._thermostat(status)
            return SuspendOutcome(state, outcome, tuple(warnings))

    def start_vacation(self, *, until: datetime | None) -> SuspendOutcome:
        """Suspend programming, set the frost setpoint and STOP, then start the stove if it is OFF.

        :param until: Aware time of the automatic end, or ``None`` for a manual end only.
        :return: New state, stove start outcome (if any) and warnings.
        :raises FeatureConflictError: Already suspended, or ENERGY SAVING STOP not usable.
        :raises ValidationFailedError: ``until`` is naive or not in the future.
        :raises GatewayError: A stove read or write failed (everything was rolled back).
        """
        with self._lock:
            if self._state.suspended:
                raise FeatureConflictError(ALREADY_SUSPENDED)
            now = self._clock()
            self._check_until(until, now)
            captured = self._vacation.capture(until=until, now=now)
            state = self._activate(captured.state, partial(self._vacation.apply, captured.state))
            self._vacation.record_started(state)
            warnings, outcome = self._start_for_vacation(captured.status)
            return SuspendOutcome(state, outcome, tuple(warnings))

    def resume(self) -> SuspensionState:
        """Restore what was active before the suspension (never starts or stops the stove).

        :return: The cleared state.
        :raises FeatureConflictError: Not suspended.
        """
        with self._lock:
            if not self._state.suspended:
                raise FeatureConflictError(NOT_SUSPENDED)
            return self._resume(EventSource.USER, reason=None)

    def check(self) -> None:
        """Recover an interrupted journal phase, else auto-resume at ``until`` or detect overrides.

        ``activating`` is rolled back, ``restoring`` is finished; overrides are checked only in
        ``active`` (a vacation also ends early when the panel setpoint changed).

        :raises GatewayError: A read or restore failed (the state is kept, retried next call).
        """
        with self._lock:
            state = self._state
            if not state.suspended:
                return
            if state.phase == PHASE_ACTIVATING:
                self._roll_back_interrupted(state)
                return
            if state.phase == PHASE_RESTORING:
                self._resume(EventSource.SYSTEM, reason=_REASON_RECOVERED)
                return
            if state.until is not None and self._clock() >= state.until:
                self._resume(EventSource.SYSTEM, reason=_REASON_UNTIL)
                return
            if state.kind == KIND_VACATION:
                override = self._vacation.detect_override(state)
                if override is not None:
                    self._override_vacation(state, override)
                return
            active = self._group.active_names()
            if active:
                self._override(active)

    # -- internals ---------------------------------------------------------------------------

    @staticmethod
    def _check_until(until: datetime | None, now: datetime) -> None:
        """Refuse an ``until`` that is naive or not in the future.

        :param until: Requested resume time.
        :param now: Aware server time.
        :raises ValidationFailedError: ``until`` is invalid.
        """
        if until is None:
            return
        if until.tzinfo is None or until.utcoffset() is None:
            raise ValidationFailedError(["until must be timezone-aware"])
        if until <= now:
            raise ValidationFailedError(["until must be in the future"])

    def _publish(self, state: SuspensionState) -> None:
        """Persist a new state, then make it current.

        :param state: State to store.
        """
        self._store.save(state)
        self._state = state

    def _resume(self, source: EventSource, *, reason: str | None) -> SuspensionState:
        """Journal ``restoring``, restore the snapshots, then clear and persist the state.

        :param source: Origin of the event.
        :param reason: Why it happens (``until`` or ``recovered``).
        :return: The cleared state.
        """
        state = self._state
        detail: dict[str, Any] = {} if reason is None else {"reason": reason}
        if state.phase != PHASE_RESTORING:
            # Reason: journal the restore before its first write so a crash can resume it.
            state = replace(state, phase=PHASE_RESTORING)
            self._publish(state)
        self._restore(state, source)
        kind = "vacation_ended" if state.kind == KIND_VACATION else "programming_resumed"
        self._clear()
        self._events.record(kind, source, participants=sorted(state.snapshots), **detail)
        return self._state

    def _activate(
        self, activating: SuspensionState, apply: Callable[[], None]
    ) -> SuspensionState:
        """Journal the ``activating`` state, apply it, then mark it ``active``.

        A failure with the process alive restores the persisted snapshots and clears the state;
        if that fails too, the state stays ``activating`` for ``check`` to roll back.

        :param activating: State with the snapshots captured before any write.
        :param apply: Performs the writes of the activation.
        :return: The ``active`` state.
        :raises Exception: The failure of the save or of the apply, after the compensation.
        """
        self._publish(activating)
        try:
            apply()
            active = replace(activating, phase=PHASE_ACTIVE)
            self._publish(active)
        except Exception:  # Reason: compensate from the journal, then re-raise the original error.
            try:
                self._restore(activating, EventSource.USER)
                self._clear()
            except Exception:  # Reason: the journal stays ``activating`` and ``check`` retries.
                _LOGGER.error("Could not undo the failed activation", exc_info=True)
            raise
        return active

    def _restore(self, state: SuspensionState, source: EventSource) -> None:
        """Restore the values and participants captured by a state (idempotent).

        :param state: State holding the snapshots.
        :param source: Origin of the restore writes.
        :raises GatewayError: A write failed (the caller keeps the state and retries).
        """
        if state.kind == KIND_VACATION:
            self._vacation.restore(state, source=source, setpoint=True)
        else:
            self._group.restore(state.snapshots)

    def _roll_back_interrupted(self, state: SuspensionState) -> None:
        """Undo an activation that a crash interrupted, then clear the state.

        :param state: The ``activating`` state.
        :raises GatewayError: A restore write failed (the state is kept and retried).
        """
        self._restore(state, EventSource.SYSTEM)
        self._clear()
        _LOGGER.warning("Rolled back an interrupted %s activation", state.kind)
        self._events.record(
            "suspension_rolled_back",
            EventSource.SYSTEM,
            suspension_kind=state.kind,
            reason=_REASON_INTERRUPTED,
            participants=sorted(state.snapshots),
        )

    def _override(self, active: list[str]) -> None:
        """Clear the suspension because a participant was re-enabled from outside.

        :param active: Names of the participants that are active.
        """
        self._clear()
        _LOGGER.warning("Suspension overridden by active participants: %s", ", ".join(active))
        self._events.record("suspension_overridden", EventSource.SYSTEM, participants=active)

    def _override_vacation(self, state: SuspensionState, override: VacationOverride) -> None:
        """End the vacation early: restore what the panel did not change, then clear the state.

        :param state: The vacation state.
        :param override: What triggered the early return.
        """
        self._vacation.restore(
            state, source=EventSource.SYSTEM, setpoint=not override.keeps_setpoint
        )
        self._clear()
        _LOGGER.warning("Vacation overridden from the stove (trigger %s)", override.trigger)
        self._events.record(
            "vacation_overridden", EventSource.SYSTEM, trigger=override.trigger, **override.detail
        )

    def _clear(self) -> None:
        """Persist and publish the not-suspended state."""
        cleared = SuspensionState()
        self._store.save(cleared)
        self._state = cleared

    def _thermostat(
        self, status: StoveStatus | None
    ) -> tuple[list[str], CommandOutcome | None]:
        """Warn about ENERGY SAVING and start the stove when it is OFF (all guards apply).

        :param status: Stove status read before the suspension, or ``None`` without thermostat.
        :return: Warnings and the start outcome (``None`` when no start was attempted or it failed).
        """
        if status is None:
            return [], None
        warnings, outcome = self._start_if_off(status)
        return [*self._energy_warnings(status), *warnings], outcome

    def _start_if_off(self, status: StoveStatus) -> tuple[list[str], CommandOutcome | None]:
        """Start the stove when it is OFF (all guards apply).

        :param status: Stove status read before the suspension.
        :return: Start warnings and the start outcome (``None`` when not attempted or failed).
        """
        if status.state is not StoveState.OFF:
            return [], None
        return self._try_start()

    def _start_for_vacation(self, status: StoveStatus) -> tuple[list[str], CommandOutcome | None]:
        """Start the stove for the frost guard: OFF or cooling down (deferred), else warn if idle.

        :param status: Stove status read before the vacation.
        :return: Warnings and the start outcome (``None`` when not attempted or failed).
        """
        if status.state in _VACATION_START_STATES:
            return self._try_start()
        if status.state in _RUNNING_STATES:
            return [], None
        return [f"{WARNING_VACATION_NOT_STARTED}:{status.state.value.lower()}"], None

    def _try_start(self) -> tuple[list[str], CommandOutcome | None]:
        """Send the guarded start; a rejection or a gateway failure becomes a warning.

        :return: Start warnings and the start outcome (``None`` when it failed).
        """
        try:
            return [], self._stove.start(source=EventSource.USER)
        except CommandRejectedError as exc:
            _LOGGER.warning("Thermostat start rejected: %s", exc.reason)
            return [f"{WARNING_START_REJECTED}:{exc.reason}"], None
        except (UnverifiedRegisterError, GatewayError) as exc:
            _LOGGER.warning("Thermostat start failed (%s)", type(exc).__name__)
            return [WARNING_START_FAILED], None

    @staticmethod
    def _energy_warnings(status: StoveStatus) -> list[str]:
        """List the ENERGY SAVING warnings of a status.

        :param status: Stove status.
        :return: Warning codes (empty when the STOP offset is set).
        """
        if status.energy_saving is None:
            return [WARNING_UNMAPPED]
        if status.energy_saving.stop_offset_c is None:
            return [WARNING_STOP_OFF]
        return []
