"""Application facade of the stove: cached status, guarded commands, settings and clock."""

import logging
import threading
from collections.abc import Callable
from datetime import datetime, timedelta, tzinfo
from typing import Final

from stove_pilot.common.exceptions import (
    CommandRejectedError,
    FeatureConflictError,
    GatewayError,
    WriteOutcomeUnknownError,
)
from stove_pilot.core.stove.clock_sync import ClockSynchronizer
from stove_pilot.core.stove.events import StoveEventRecorder
from stove_pilot.core.stove.models import (
    ClockStatus,
    CommandOutcome,
    CommandResult,
    EventSource,
    MaintenanceInfo,
    PendingCommand,
    StoveCapabilities,
    StoveSettings,
    StoveState,
    StoveStatus,
)
from stove_pilot.core.stove.pending import PendingCommands
from stove_pilot.core.stove.ports import HistoryRecorder, PreferenceStore
from stove_pilot.core.stove.rules import (
    Decision,
    GuardPolicy,
    GuardResult,
    decide_start,
    decide_stop,
)
from stove_pilot.core.stove.service import StoveGateway
from stove_pilot.core.stove.setting_rules import validate_energy_saving, validate_energy_stop

_LOGGER = logging.getLogger(__name__)

_START: Final = "start"
_STOP: Final = "stop"
_STARTED_STATES: Final = {StoveState.IGNITION, StoveState.RUNNING}


class StoveRepository:
    """Single entry point to read the stove and to send guarded commands.

    All stove I/O is serialized by one reentrant lock. Time comes from the injected clock.
    """

    def __init__(
        self,
        gateway: StoveGateway,
        history: HistoryRecorder,
        policy: GuardPolicy,
        clock: Callable[[], datetime],
        tz: tzinfo,
        *,
        stale_after: timedelta,
        deferred_expiry: timedelta,
        deferred_stop_expiry: timedelta,
        clock_tolerance_s: float,
        clock_sync_min_interval: timedelta,
        clock_auto_sync: bool,
        live_read_min_interval: timedelta,
        settings_lock: Callable[[], str | None] = lambda: None,
        preferences: PreferenceStore | None = None,
    ) -> None:
        """Bind the collaborators and limits.

        :param gateway: Stove gateway.
        :param history: History recorder (events and samples).
        :param policy: Command safety limits.
        :param clock: Returns the aware server time.
        :param tz: Local timezone used to present the server time.
        :param stale_after: Age after which the cached status counts as stale.
        :param deferred_expiry: Lifetime of a deferred start.
        :param deferred_stop_expiry: Lifetime of a deferred stop.
        :param clock_tolerance_s: Maximum accepted absolute clock drift.
        :param clock_sync_min_interval: Minimum time between two automatic clock syncs.
        :param clock_auto_sync: Whether the poller may sync the stove clock.
        :param live_read_min_interval: Minimum time between two throttled live reads.
        :param settings_lock: Returns the reason the setpoint and ENERGY SAVING are locked, if any.
        :param preferences: Store of the runtime clock auto-sync toggle.
        """
        self._gateway = gateway
        self._history = history
        self._events = StoveEventRecorder(history)
        self._policy = policy
        self._clock = clock
        self._stale_after = stale_after
        self._live_read_min_interval = live_read_min_interval
        self._settings_lock = settings_lock
        self._lock = threading.RLock()
        self._status: StoveStatus | None = None
        self._stale = False
        self._last_live_at: datetime | None = None
        self._pending = PendingCommands(
            self._events, {_START: deferred_expiry, _STOP: deferred_stop_expiry}
        )
        self._clock_sync = ClockSynchronizer(
            gateway,
            self._events,
            clock,
            tz,
            self.refresh,
            tolerance_s=clock_tolerance_s,
            min_interval=clock_sync_min_interval,
            auto_sync=clock_auto_sync,
            lock=self._lock,
            preferences=preferences,
        )

    # -- status ------------------------------------------------------------------------------

    def refresh(self, *, full: bool = False) -> StoveStatus:
        """Read the stove now, record state changes and cache the result.

        :param full: Also read the slow tier (ENERGY SAVING) now instead of its cache.
        :return: The fresh status.
        :raises GatewayError: The read failed (the cache is then flagged stale).
        """
        with self._lock:
            try:
                status = self._gateway.read_status(fresh_slow=full)
            except GatewayError:
                self._stale = True
                self._last_live_at = self._clock()
                raise
            self._last_live_at = self._clock()
            self._events.state_change(self._status, status)
            self._status = status
            self._stale = self._gateway.status_degraded()
            return status

    def get_status(self, max_age_s: float | None = None) -> StoveStatus:
        """Return the cached status, or do one throttled live read when it is too old.

        :param max_age_s: Maximum accepted cache age in seconds (``None`` accepts any age).
        :return: The status; the cached one, flagged stale, when the live read fails.
        :raises GatewayError: The first read failed and nothing is cached.
        """
        with self._lock:
            cached = self._status
            if cached is not None and not self._needs_live_read(cached, max_age_s):
                return cached
            try:
                return self.refresh()
            except GatewayError as exc:
                if cached is None:
                    raise
                _LOGGER.warning("Live read failed (%s); serving the stale cache", type(exc).__name__)
                return cached

    def is_stale(self) -> bool:
        """Tell whether the cached status is missing, old or from a failed refresh.

        :return: ``True`` when the status should be flagged stale.
        """
        with self._lock:
            if self._status is None:
                return True
            return self._stale or self._clock() - self._status.polled_at > self._stale_after

    # -- commands ----------------------------------------------------------------------------

    def start(self, *, source: EventSource) -> CommandOutcome:
        """Start the stove (never retried; deferred while cooling or too soon after an ignition).

        :param source: Who asks for the start.
        :return: Applied, no-op or deferred outcome.
        :raises CommandRejectedError: The guard refuses the start.
        """
        with self._lock:
            return self._submit(_START, source, confirm=False)

    def stop(self, *, source: EventSource, confirm_ignition_stop: bool = False) -> CommandOutcome:
        """Stop the stove (a stop during ignition needs a user confirmation).

        :param source: Who asks for the stop.
        :param confirm_ignition_stop: User confirmed a stop during ignition.
        :return: Applied, no-op or deferred outcome.
        :raises CommandRejectedError: The guard refuses the stop.
        """
        with self._lock:
            return self._submit(_STOP, source, confirm=confirm_ignition_stop)

    def pending_start(self) -> PendingCommand | None:
        """Return the deferred start, if any.

        :return: The pending command or ``None``.
        """
        with self._lock:
            return self._pending.get(_START)

    def pending_stop(self) -> PendingCommand | None:
        """Return the deferred stop, if any.

        :return: The pending command or ``None``.
        """
        with self._lock:
            return self._pending.get(_STOP)

    def process_pending(self) -> list[CommandOutcome]:
        """Drop expired deferred commands and apply those the stove now allows.

        A deferred command is removed before it is sent, so a failure never resends it.

        :return: The outcomes applied by this call.
        :raises GatewayError: The status read failed (pending commands are kept).
        """
        with self._lock:
            self._pending.expire(self._clock())
            if not self._pending.has_any():
                return []
            status = self.refresh()
            outcomes: list[CommandOutcome] = []
            for command in (_STOP, _START):
                outcome = self._process_one(command, status)
                if outcome is not None:
                    outcomes.append(outcome)
            return outcomes

    # -- settings ----------------------------------------------------------------------------

    def set_setpoint(
        self, value_c: float, *, source: EventSource, bypass_lock: bool = False
    ) -> bool:
        """Write the setpoint (read-before-write).

        :param value_c: Target temperature in degrees Celsius.
        :param source: Who asks for the change.
        :param bypass_lock: Ignore the settings lock (suspension feature only).
        :return: ``True`` when the register changed.
        :raises FeatureConflictError: The settings are locked.
        """
        self._check_settings_lock(bypass_lock)
        return self._write_settings([("setpoint", value_c)], source)

    def set_power(self, level: int, *, source: EventSource) -> bool:
        """Write the power level (read-before-write).

        :param level: Power level.
        :param source: Who asks for the change.
        :return: ``True`` when the register changed.
        """
        return self._write_settings([("power_level", level)], source)

    def set_energy_saving(
        self,
        *,
        stop_offset_c: int | None,
        start_offset_c: int | None,
        source: EventSource,
        bypass_lock: bool = False,
    ) -> bool:
        """Write the ENERGY SAVING offsets (1..3, STOP ``None`` = OFF; START OFF is unsupported).

        :param stop_offset_c: STOP offset above the setpoint, or ``None`` for OFF.
        :param start_offset_c: START offset below the setpoint.
        :param source: Who asks for the change.
        :param bypass_lock: Ignore the settings lock (suspension feature only).
        :return: ``True`` when at least one register changed.
        :raises FeatureConflictError: The settings are locked.
        :raises ValidationFailedError: Out of range or START OFF requested.
        """
        self._check_settings_lock(bypass_lock)
        validate_energy_saving(stop_offset_c, start_offset_c)
        # Reason: START first, so an unverified START register refuses before STOP is touched.
        return self._write_settings(
            [("energy_start_offset", start_offset_c), ("energy_stop_offset", stop_offset_c)],
            source,
        )

    def set_energy_stop(
        self, offset_c: int | None, *, source: EventSource, bypass_lock: bool = False
    ) -> bool:
        """Write only the ENERGY SAVING STOP offset (1..3, ``None`` = OFF); START is never touched.

        :param offset_c: STOP offset above the setpoint, or ``None`` for OFF.
        :param source: Who asks for the change.
        :param bypass_lock: Ignore the settings lock (suspension feature only).
        :return: ``True`` when the register changed.
        :raises FeatureConflictError: The settings are locked.
        :raises ValidationFailedError: Out of range.
        """
        self._check_settings_lock(bypass_lock)
        validate_energy_stop(offset_c)
        return self._write_settings([("energy_stop_offset", offset_c)], source)

    def set_display_eco(self, enabled: bool, *, source: EventSource) -> bool:
        """Write the DISPLAY ECO setting (read-before-write).

        :param enabled: Whether the panel dims when idle.
        :param source: Who asks for the change.
        :return: ``True`` when the register changed.
        """
        return self._write_settings([("display_eco", 1 if enabled else 0)], source)

    def settings(self) -> StoveSettings:
        """Read the adjustable settings with their writable flags.

        :return: ENERGY SAVING and DISPLAY ECO values (``None`` when unmapped).
        """
        with self._lock:
            return self._gateway.read_settings(self.get_status(max_age_s=0).energy_saving)

    def capabilities(self) -> StoveCapabilities:
        """Tell which writes are allowed now (no stove I/O).

        :return: Capability flags derived from the profile and the verification gates.
        """
        return self._gateway.capabilities()

    def maintenance(self) -> MaintenanceInfo | None:
        """Read the maintenance counters on demand (never polled).

        :return: The counters, or ``None`` when the profile maps none.
        """
        with self._lock:
            return self._gateway.read_counters()

    # -- clock -------------------------------------------------------------------------------

    def clock_status(self) -> ClockStatus:
        """Compare the stove clock with the server clock (uses the throttled live read).

        :return: Clock status; ``stove_time`` is naive local time as read from the stove.
        :raises GatewayProtocolError: The stove clock is not mapped or its bytes are invalid.
        """
        with self._lock:
            return self._clock_sync.status(self.get_status(max_age_s=0))

    def sync_clock(
        self, *, force: bool = False, source: EventSource = EventSource.SYSTEM
    ) -> ClockStatus:
        """Set the stove clock when forced or when the drift exceeds the tolerance.

        :param force: Write even when the drift is within the tolerance.
        :param source: Who asks for the sync.
        :return: The clock status after the call.
        """
        with self._lock:
            return self._clock_sync.sync(force=force, source=source)

    def auto_sync_clock(self) -> ClockStatus | None:
        """Schedule a minute-boundary clock sync when auto-sync is on and the drift is too large.

        A failed attempt is not retried before the minimum interval has elapsed again.

        :return: ``None``: the write happens later on the clock-sync thread.
        """
        with self._lock:
            return self._clock_sync.auto_sync(self._status)

    def set_clock_auto_sync(self, enabled: bool, *, source: EventSource) -> ClockStatus:
        """Persist the clock auto-sync toggle and return the clock status.

        :param enabled: New value.
        :param source: Who asks for the change.
        """
        self._clock_sync.set_auto_sync(enabled, source=source)
        return self.clock_status()

    def close(self) -> None:
        """Stop the pending automatic clock sync, if any."""
        self._clock_sync.stop()

    # -- command internals -------------------------------------------------------------------

    def _submit(self, command: str, source: EventSource, *, confirm: bool) -> CommandOutcome:
        status = self.refresh()
        guard = self._decide(command, status, source, confirm=confirm)
        if guard.decision is Decision.REJECT:
            self._events.command(command, source, "rejected", guard.reason)
            raise CommandRejectedError(guard.reason or "rejected")
        # Reason: a start cancels a pending stop and a stop cancels a pending start.
        self._pending.clear(_STOP if command == _START else _START)
        self._pending.clear(command)
        if guard.decision is Decision.DEFER:
            self._pending.defer(command, source, self._clock())
            self._events.command(command, source, "deferred", guard.reason)
            return CommandOutcome(CommandResult.DEFERRED, guard.reason, status)
        if guard.decision is Decision.NO_OP:
            self._events.command(command, source, "no_op", guard.reason)
            return CommandOutcome(CommandResult.NO_OP, guard.reason, status)
        return self._apply(command, source, deferred=False)

    def _decide(
        self, command: str, status: StoveStatus, source: EventSource, *, confirm: bool
    ) -> GuardResult:
        if command == _START:
            return decide_start(
                status,
                last_ignition_at=self._history.last_ignition_at(),
                now=self._clock(),
                policy=self._policy,
            )
        return decide_stop(status, source=source, confirm_ignition_stop=confirm)

    def _apply(self, command: str, source: EventSource, *, deferred: bool) -> CommandOutcome:
        """Send a command once, refresh and record it (an ignition is never retried).

        :param command: ``start`` or ``stop``.
        :param source: Who asked for the command.
        :param deferred: Whether it comes from a pending command.
        :return: The applied outcome.
        """
        try:
            self._gateway.execute(command)
        except GatewayError as exc:
            unknown = isinstance(exc, WriteOutcomeUnknownError)
            status = self._refresh_quietly()
            self._events.command(
                command,
                source,
                "unknown" if unknown else "failed",
                type(exc).__name__,
                deferred=deferred,
            )
            # Reason: last_ignition_at drives the restart guard, so a confirmed start is recorded.
            if unknown and command == _START and status and status.state in _STARTED_STATES:
                self._events.record("ignition", source, deferred=deferred, confirmed_by_read=True)
            raise
        status = self._refresh_quietly()
        self._events.command(command, source, "applied", None, deferred=deferred)
        if command == _START:
            self._events.record("ignition", source, deferred=deferred)
        return CommandOutcome(CommandResult.APPLIED, None, status)

    def _process_one(self, command: str, status: StoveStatus) -> CommandOutcome | None:
        pending = self._pending.get(command)
        if pending is None:
            return None
        guard = self._decide(command, status, pending.source, confirm=False)
        if guard.decision is Decision.DEFER:
            return None
        # Reason: removed before sending, so a failed send never resends the command.
        self._pending.clear(command)
        if guard.decision is not Decision.APPLY:
            result = "no_op" if guard.decision is Decision.NO_OP else "rejected"
            self._events.command(command, pending.source, result, guard.reason, deferred=True)
            return None
        self._pending.clear(_STOP if command == _START else _START)
        return self._apply(command, pending.source, deferred=True)

    # -- other internals ---------------------------------------------------------------------

    def _needs_live_read(self, cached: StoveStatus, max_age_s: float | None) -> bool:
        now = self._clock()
        if max_age_s is None or (now - cached.polled_at).total_seconds() < max_age_s:
            return False
        recent = self._last_live_at is not None and (
            now - self._last_live_at < self._live_read_min_interval
        )
        return not recent

    def _check_settings_lock(self, bypass_lock: bool) -> None:
        """Refuse a locked settings write before any stove I/O."""
        reason = None if bypass_lock else self._settings_lock()
        if reason is not None:
            raise FeatureConflictError(reason)

    def _write_settings(self, writes: list[tuple[str, float | None]], source: EventSource) -> bool:
        """Write registers read-before-write, one ``setting_changed`` event per changed one.

        :param writes: ``(register name, value)`` pairs, written in order.
        :param source: Who asks for the change.
        :return: ``True`` when at least one register changed.
        """
        changed = False
        with self._lock:
            try:
                for name, value in writes:
                    if self._gateway.write_register(name, value):
                        changed = True
                        self._events.record("setting_changed", source, setting=name, value=value)
                    else:
                        _LOGGER.debug("Setting %s already holds the requested value", name)
            finally:
                if changed:
                    self._refresh_quietly()
        return changed

    def _refresh_quietly(self) -> StoveStatus | None:
        """Refresh after a write; a read failure keeps the stale cache.

        :return: The fresh status, or the cached one when the read failed.
        """
        try:
            return self.refresh()
        except GatewayError as exc:
            _LOGGER.warning("Post-write read failed (%s)", type(exc).__name__)
            return self._status
