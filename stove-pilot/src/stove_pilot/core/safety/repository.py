"""Safety facade: settings, the manual latch, and the poller check."""

import logging
import threading
from collections.abc import Callable
from dataclasses import replace
from datetime import datetime

from stove_pilot.common.exceptions import (
    CommandRejectedError,
    ConfigurationError,
    GatewayError,
    UnverifiedRegisterError,
    ValidationFailedError,
    WriteOutcomeUnknownError,
)
from stove_pilot.core.safety.alert import failure_message
from stove_pilot.core.safety.attribution import attribute_manual_stop
from stove_pilot.core.safety.detection import SERVER_STOP_LOOKBACK_MIN
from stove_pilot.core.safety.diagnostics import SafetyDiagnostics
from stove_pilot.core.safety.models import (
    MAX_ATTEMPTS_MAX,
    MAX_ATTEMPTS_MIN,
    RETRY_INTERVAL_MAX_MIN,
    THRESHOLD_MAX_C,
    THRESHOLD_MIN_C,
    SafetyConfig,
    SafetyDecision,
    SafetyEvent,
    SafetyObservation,
    SafetyParams,
    SafetyReport,
    SafetyState,
    SafetyStatus,
    StartResult,
)
from stove_pilot.core.safety.ports import (
    ChronoStopCheck,
    SafetyEventSink,
    SafetyStovePort,
    StopCommandLookup,
    VacationCheck,
)
from stove_pilot.core.safety.rules import (
    after_start,
    decide,
    derive_status,
    on_config_changed,
    rearm as rearm_state,
    validate_config,
    warning_for,
)
from stove_pilot.core.safety.service import SafetyStore
from stove_pilot.core.stove.messages import DisplayMessage
from stove_pilot.core.stove.models import CommandResult, EventSource, StoveStatus
from stove_pilot.core.stove.rules import Decision, GuardResult

_LOGGER = logging.getLogger(__name__)

_SETTING_CHANGED = "setting_changed"
_SETTING_NAME = "safety"
_START_RESULTS = {
    CommandResult.APPLIED: StartResult.APPLIED,
    CommandResult.DEFERRED: StartResult.DEFERRED,
    CommandResult.NO_OP: StartResult.NO_OP,
}


class SafetyRepository:
    """Application facade of the safety guard. The poller thread is the only ``check`` caller.

    The lock covers the stored card and the episode. Stove I/O runs outside the lock.
    A decision that does not start is saved in the same lock as ``decide``. A start is
    saved only after the preview, and only when the card and the episode are still the
    ones ``decide`` used. A save that lands during the preview is kept and the start
    is dropped.
    """

    def __init__(
        self,
        *,
        store: SafetyStore,
        stove: SafetyStovePort,
        events: SafetyEventSink,
        lookup: StopCommandLookup,
        vacation_active: VacationCheck,
        chrono_check: ChronoStopCheck | None,
        defaults: SafetyConfig,
        params: SafetyParams,
        chrono_window_min: int,
        min_restart_interval_min: int,
        clock: Callable[[], datetime],
    ) -> None:
        """Bind the collaborators. Nothing is read from the environment here.

        :param store: Safety card and episode store.
        :param stove: Guarded stove port (start, pending start, cached status).
        :param events: Failure-tolerant history sink.
        :param lookup: Stop-command history.
        :param vacation_active: True only while a vacation is suspended.
        :param chrono_check: Panel-stop matcher, or ``None`` when chrono is disabled.
        :param defaults: Env seed used until the user saves a card.
        :param params: Container knobs (confirm polls, hysteresis, retry floor).
        :param chrono_window_min: Minutes after a chrono stop that still match (at least 1).
        :param min_restart_interval_min: Lowest retry interval the stove guard allows.
        :param clock: Returns the aware server time.
        """
        if chrono_window_min < 1:
            raise ValueError("chrono_window_min must be at least 1")
        self._store = store
        self._stove = stove
        self._events = events
        self._lookup = lookup
        self._vacation_active = vacation_active
        self._chrono_check = chrono_check
        self._defaults = defaults
        self._params = params
        self._chrono_window_min = chrono_window_min
        self._min_restart_interval_min = min_restart_interval_min
        self._clock = clock
        self._lock = threading.RLock()
        self._diagnostics = SafetyDiagnostics()

    def view(self) -> SafetyReport:
        """Return the effective card, the episode, and the derived status.

        The stove is not read. A missing cache makes the warning ``None`` and the
        status stale while the feature is enabled and no vacation is active.

        :return: The safety report.
        """
        with self._lock:
            config = self._effective()
            state = self._store.load_state()
        return self._report(config, state)

    def update(self, config: SafetyConfig, *, source: EventSource = EventSource.USER) -> SafetyReport:
        """Validate and store a card. Saving does not rearm and does not clear the latch.

        Only a change from disabled to enabled rearms, and that rule lives in
        ``on_config_changed``. A vacation does not block this call.

        :param config: Card to store. The fields are already whole numbers.
        :param source: Origin of the ``setting_changed`` event.
        :return: The report after the save.
        :raises ValidationFailedError: A value is outside its range.
        """
        issues = validate_config(config, self._min_restart_interval_min)
        if issues:
            raise ValidationFailedError(issues)
        with self._lock:
            now = self._now()
            old = self._effective()
            state = self._store.load_state()
            decision = on_config_changed(old, config, state, now)
            self._store.save_config(config)
            self._store.save_state(decision.state)
            self._events.record(
                _SETTING_CHANGED,
                source,
                setting=_SETTING_NAME,
                enabled=config.enabled,
                threshold_c=config.threshold_c,
                max_attempts=config.max_attempts,
                retry_interval_min=config.retry_interval_min,
            )
            self._record(decision.events, source)
            _LOGGER.info(
                "Safety settings stored (enabled=%s attempts=%s)",
                config.enabled,
                decision.state.attempts,
            )
        return self.view()

    def rearm(self) -> SafetyReport:
        """Rearm from the user: idle, counters cleared, manual latch cleared.

        :return: The report after the rearm.
        """
        ambient = self._ambient()
        with self._lock:
            state = self._store.load_state()
            config = self._effective()
            decision = rearm_state(state, "user", ambient, config.threshold_c)
            self._store.save_state(decision.state)
            self._record(decision.events, EventSource.USER)
            _LOGGER.info("Safety rearmed (source=%s)", EventSource.USER.value)
        return self.view()

    def check(self, status: StoveStatus, now: datetime) -> None:
        """Evaluate one fresh poll. A disabled card returns before any lookup or write.

        A stale or degraded cache returns before the decision, so the phase and
        the confirmation counter are left unchanged. The same holds when the
        read just before a start is the one that flags the display: that poll
        is discarded and nothing is written. A start the guard would defer is
        not sent and is not counted. The live preview runs only when the poll
        status would already apply. Enabled, the manual latch and vacation
        are read again immediately before the start. A save during the preview
        drops the start and leaves that save in place. A change that lands while
        the write is already in flight cannot be cancelled.

        :param status: Fresh successful poll. The caller skips a failed refresh.
        :param now: Aware evaluation instant.
        :raises Exception: An unexpected start failure is logged and re-raised.
        """
        try:
            self._evaluate(status, now)
        except ConfigurationError as exc:
            self.note_unreadable(exc)

    def _evaluate(self, status: StoveStatus, now: datetime) -> None:
        """Run one check. ``ConfigurationError`` propagates to ``check``.

        :param status: Fresh successful poll.
        :param now: Aware evaluation instant.
        """
        self._require_aware(now)
        if self._stove.is_stale():
            return
        with self._lock:
            config = self._effective()
            if not config.enabled:
                return
            episode = self._store.load_state()
            if episode.last_app_stop_at is None:
                episode = replace(episode, last_app_stop_at=now)
                self._store.save_state(episode)
        vacation = self._vacation_active()
        if vacation:
            reason: str | None = None
            app_stop_at: datetime | None = None
        else:
            reason, app_stop_at = attribute_manual_stop(
                episode,
                status,
                now,
                self._lookup,
                self._chrono_check,
                SERVER_STOP_LOOKBACK_MIN,
                self._params.poll_interval_s,
            )
        observation = SafetyObservation(
            polled_at=status.polled_at,
            ambient_c=status.ambient_temp_c,
            stove_state=status.state,
            pending_start=self._stove.pending_start() is not None,
            vacation_active=vacation,
            manual_stop_reason=reason,
            app_stop_at=app_stop_at,
            alarm_code=status.alarm_code,
        )
        with self._lock:
            config = self._effective()
            if not config.enabled:
                return
            baseline = self._store.load_state()
            decision = decide(config, baseline, observation, self._params, now)
            threshold_c = config.threshold_c
            if not decision.start_requested or decision.state.paused_reason is not None:
                self._commit(decision)
                return
        guard = self._start_guard(status)
        if guard is None:
            return
        if not self._keep_start(config, baseline, decision, guard):
            return
        result, reject_reason = self._invoke_start()
        with self._lock:
            config = self._effective()
            outcome = after_start(
                config,
                self._store.load_state(),
                result,
                now,
                reason=reject_reason,
                ambient_c=status.ambient_temp_c,
                threshold_c=threshold_c,
            )
            self._store.save_state(outcome.state)
            self._record(outcome.events, EventSource.SYSTEM)
            _LOGGER.info(
                "Safety start recorded (result=%s attempts=%s)",
                result.value,
                outcome.state.attempts,
            )

    def active_alert(self) -> DisplayMessage | None:
        """Return the failure alert when the derived status is ``failed``.

        Vacation, a stale cache and a manual pause hide it, matching ``view``.
        Returning ``None`` lets the alert store clear the code.

        :return: The display message, or ``None``.
        """
        try:
            with self._lock:
                config = self._effective()
                state = self._store.load_state()
            status = derive_status(
                config,
                state,
                vacation_active=self._vacation_active(),
                stale=self._stove.is_stale(),
            )
        except ConfigurationError as exc:
            self.note_unreadable(exc)
            return None
        if status is not SafetyStatus.FAILED:
            return None
        return failure_message(state.failure_reason, state.attempts)

    def _report(self, config: SafetyConfig, state: SafetyState) -> SafetyReport:
        stale = self._stove.is_stale()
        cached = self._cached_status()
        warning = None if cached is None else warning_for(config, cached.setpoint_c)
        status = derive_status(
            config,
            state,
            vacation_active=self._vacation_active(),
            stale=stale,
        )
        return SafetyReport(
            config=config,
            state=state,
            status=status,
            warning=warning,
            threshold_min_c=THRESHOLD_MIN_C,
            threshold_max_c=THRESHOLD_MAX_C,
            max_attempts_min=MAX_ATTEMPTS_MIN,
            max_attempts_max=MAX_ATTEMPTS_MAX,
            retry_interval_min_min=self._min_restart_interval_min,
            retry_interval_max_min=RETRY_INTERVAL_MAX_MIN,
        )

    def _effective(self) -> SafetyConfig:
        stored = self._store.load_config()
        config = self._defaults if stored is None else stored
        floor = self._min_restart_interval_min
        if config.retry_interval_min >= floor:
            return config
        self._diagnostics.warn_retry_floor()
        return replace(config, retry_interval_min=floor)

    def _commit(self, decision: SafetyDecision) -> None:
        """Persist one decision and its events.

        :param decision: Result of ``decide`` for this poll.
        """
        with self._lock:
            self._store.save_state(decision.state)
            self._record(decision.events, EventSource.SYSTEM)
            _LOGGER.debug(
                "Safety evaluated (phase=%s attempts=%s start=%s)",
                decision.state.phase.value,
                decision.state.attempts,
                decision.start_requested,
            )

    def note_unreadable(self, exc: ConfigurationError) -> None:
        """Log one ERROR per process when the stored safety rows cannot be read.

        :param exc: Decode failure. Its message is not logged.
        """
        self._diagnostics.note_unreadable(exc)

    def _keep_start(
        self,
        config: SafetyConfig,
        baseline: SafetyState,
        decision: SafetyDecision,
        guard: GuardResult,
    ) -> bool:
        """Save a start decision when the card and the episode are still current.

        :param config: Card ``decide`` used.
        :param baseline: Episode ``decide`` used.
        :param decision: Start decision. It is not written when the inputs changed.
        :param guard: Preview result. A defer does not call start.
        :return: ``True`` when the caller should send the start.
        """
        with self._lock:
            if self._effective() != config or self._store.load_state() != baseline:
                _LOGGER.info("Safety start dropped because the card or the episode changed")
                return False
            self._commit(decision)
            if guard.decision is Decision.DEFER:
                _LOGGER.info("Safety start postponed (reason=%s)", guard.reason)
                return False
            return not self._restart_blocked()

    def _start_guard(self, status: StoveStatus) -> GuardResult | None:
        """Preview from the poll, and live-read only when that says apply.

        :param status: Fresh poll status.
        :return: The decision to act on, or ``None`` when the live read flagged stale.
        """
        guard = self._stove.guard_start(status)
        if guard.decision is not Decision.APPLY:
            return guard
        guard = self._preview_start()
        if self._stove.is_stale():
            # Reason: this read, not the poller's, is the one that flagged the display.
            _LOGGER.info("Safety start skipped because the stove status is stale")
            return None
        return guard

    def _restart_blocked(self) -> bool:
        """Re-read the blocks immediately before a start.

        :return: ``True`` when enabled, the latch or vacation forbids the start.
        """
        with self._lock:
            if not self._effective().enabled:
                return True
            if self._store.load_state().paused_reason is not None:
                return True
            return self._vacation_active()

    def _preview_start(self) -> GuardResult:
        """Ask the port whether a start would be sent now. Nothing is written.

        :return: The guard decision. A preview read failure is reported as defer.
        """
        try:
            return self._stove.preview_start()
        except GatewayError as exc:
            _LOGGER.warning("Safety start preview failed (%s)", type(exc).__name__)
            return GuardResult(Decision.DEFER, "preview_unavailable")

    def _cached_status(self) -> StoveStatus | None:
        """Return the fresh cache, or ``None`` when it is missing, old or failed.

        :return: The cached status, or ``None`` when ``is_stale`` is true.
        """
        # Reason: is_stale covers a missing, old or failed cache. get_status(None) then
        # returns that cache and does not live-read.
        if self._stove.is_stale():
            return None
        return self._stove.get_status()

    def _ambient(self) -> float | None:
        cached = self._cached_status()
        if cached is None:
            return None
        return cached.ambient_temp_c

    def _invoke_start(self) -> tuple[StartResult, str | None]:
        """Call the guarded start outside the safety lock and map the outcome.

        :return: The rule result and, for a rejection, the reason.
        :raises Exception: Any error that is not a known stove failure.
        """
        try:
            outcome = self._stove.start(source=EventSource.SYSTEM)
        except CommandRejectedError as exc:
            _LOGGER.info("Safety start rejected (reason=%s)", exc.reason)
            return StartResult.REJECTED, exc.reason
        except UnverifiedRegisterError as exc:
            _LOGGER.info("Safety start rejected (%s)", type(exc).__name__)
            return StartResult.REJECTED, exc.name
        except WriteOutcomeUnknownError:
            _LOGGER.warning("Safety start outcome unknown")
            return StartResult.UNKNOWN, None
        except GatewayError as exc:
            _LOGGER.warning("Safety start failed (%s)", type(exc).__name__)
            return StartResult.GATEWAY_ERROR, None
        except Exception:
            _LOGGER.error("Safety start failed unexpectedly", exc_info=True)
            raise
        mapped = _START_RESULTS.get(outcome.result)
        if mapped is None:
            _LOGGER.error("Safety start failed unexpectedly")
            raise ValueError(f"Unknown command result {outcome.result!r}")
        if mapped is StartResult.DEFERRED:
            # Reason: the preview can lose a race; the pending start is left as the guard stored it.
            _LOGGER.warning("Safety start was deferred after the preview")
        _LOGGER.info("Safety start finished (result=%s)", mapped.value)
        return mapped, None

    def _record(self, events: tuple[SafetyEvent, ...], source: EventSource) -> None:
        for event in events:
            self._events.record(event.kind, source, **event.detail)

    def _now(self) -> datetime:
        now = self._clock()
        self._require_aware(now)
        return now

    @staticmethod
    def _require_aware(moment: datetime) -> None:
        if moment.tzinfo is None or moment.utcoffset() is None:
            raise ValueError("Safety timestamps must be timezone-aware")
