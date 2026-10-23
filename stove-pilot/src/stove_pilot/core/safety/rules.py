"""Pure safety decisions. No I/O, no clock reads, no payload logging."""

from dataclasses import replace
from datetime import datetime, timedelta
from typing import Final

from stove_pilot.core.safety.models import (
    EVENT_PAUSED,
    EVENT_REARMED,
    EVENT_RESTART_SUCCEEDED,
    MAX_ATTEMPTS_MAX,
    MAX_ATTEMPTS_MIN,
    PAUSE_APP_STOP,
    PAUSE_REASONS,
    REASON_ATTEMPTS_EXHAUSTED,
    REASON_HYSTERESIS,
    REASON_IGNITION_FAILED,
    REASON_STOVE_LIT,
    RETRY_INTERVAL_MAX_MIN,
    THRESHOLD_MAX_C,
    THRESHOLD_MIN_C,
    WARNING_THRESHOLD_NOT_BELOW_SETPOINT,
    SafetyConfig,
    SafetyDecision,
    SafetyEvent,
    SafetyObservation,
    SafetyParams,
    SafetyPhase,
    SafetyState,
    SafetyStatus,
    StartResult,
)
from stove_pilot.core.safety.detection import STOPPED_LIKE
from stove_pilot.core.safety.transitions import (
    apply_phase,
    count_attempt,
    fail_episode,
    make_event,
    standby_after_ignition,
    threshold_or_default,
)
from stove_pilot.core.stove.models import StoveState

_PLAUSIBLE_MIN_C: Final = 0.0
_PLAUSIBLE_MAX_C: Final = 40.0
_LIT: Final = frozenset({StoveState.IGNITION, StoveState.RUNNING})
_SUCCESS: Final = frozenset({StoveState.RUNNING, StoveState.CLEANING})
_BURNING: Final = frozenset(
    {StoveState.IGNITION, StoveState.RUNNING, StoveState.CLEANING, StoveState.STANDBY}
)


def _plausible(ambient_c: float) -> bool:
    return _PLAUSIBLE_MIN_C <= ambient_c <= _PLAUSIBLE_MAX_C


def decide(
    config: SafetyConfig,
    state: SafetyState,
    obs: SafetyObservation,
    params: SafetyParams,
    now: datetime,
) -> SafetyDecision:
    """Evaluate one fresh poll.

    The caller must not invoke this for a stale or failed poll. ``start_requested`` does not
    record the attempt: ``after_start`` does.

    :param config: Effective safety card.
    :param state: Episode before this poll.
    :param obs: Fresh poll and the manual-stop attribution.
    :param params: Container knobs.
    :param now: Evaluation instant supplied by the caller.
    :return: Updated episode, start request, and events.
    """
    if not config.enabled:
        return SafetyDecision(state, False, ())
    state = replace(state, last_check_at=now)
    if obs.vacation_active:
        # Reason: a stop during vacation must not be replayed as a manual latch when it ends.
        return SafetyDecision(
            replace(
                _remember_poll(state, obs),
                vacation_was_active=True,
                last_app_stop_at=now,
            ),
            False,
            (),
        )
    events: list[SafetyEvent] = []
    if state.vacation_was_active:
        state = _end_vacation(state, now)
    # Reason: last_stove_state stays the previous poll until the decision is done.
    state, latch_events = _apply_latch(state, obs)
    events.extend(latch_events)
    state, rearm_events = _apply_rearm(config, state, obs, params)
    events.extend(rearm_events)
    state, success_events = _apply_success(config, state, obs)
    events.extend(success_events)
    state = _apply_confirm(config, state, obs, params)
    state, start_requested, phase_events = apply_phase(config, state, obs, params, now)
    events.extend(phase_events)
    state = _remember_poll(state, obs)
    return SafetyDecision(state, start_requested, tuple(events))


def after_start(
    config: SafetyConfig,
    state: SafetyState,
    result: StartResult,
    now: datetime,
    *,
    reason: str | None = None,
    ambient_c: float | None = None,
    threshold_c: int | None = None,
) -> SafetyDecision:
    """Apply the outcome of the guarded start that ``decide`` requested.

    :param config: Effective safety card.
    :param state: Episode after ``decide``, before the attempt is recorded.
    :param result: Outcome of ``StoveRepository.start``.
    :param now: Instant of the attempt.
    :param reason: Guard reason when ``result`` is ``REJECTED``.
    :param ambient_c: Ambient reading that triggered the attempt.
    :param threshold_c: Threshold in force; defaults to the config value.
    :return: Episode with the attempt recorded, and the matching event.
    """
    if result == StartResult.GATEWAY_ERROR:
        return SafetyDecision(state, False, ())
    if result == StartResult.NO_OP:
        # Reason: the stove was already on, so this episode must not keep a spent budget.
        return SafetyDecision(_reset_idle(state, clear_latch=False), False, ())
    if result == StartResult.REJECTED:
        updated, event = fail_episode(
            state,
            reason if reason is not None else "rejected",
            attempt=state.attempts,
            max_attempts=config.max_attempts,
            ambient_c=ambient_c,
            threshold_c=threshold_or_default(config, threshold_c),
        )
        return SafetyDecision(updated, False, (event,))
    if result in (StartResult.APPLIED, StartResult.DEFERRED, StartResult.UNKNOWN):
        return count_attempt(config, state, now, ambient_c, threshold_c)
    raise ValueError(f"Unknown start result {result!r}")


def on_config_changed(
    old: SafetyConfig,
    new: SafetyConfig,
    state: SafetyState,
    now: datetime,
) -> SafetyDecision:
    """Apply a saved card. Only enabling rearms; disabling keeps the episode.

    :param old: Card before the save.
    :param new: Card after the save.
    :param state: Episode at the save.
    :param now: Instant of the save.
    :return: Episode after the save. No start is requested.
    """
    if not old.enabled and new.enabled:
        reset = replace(
            _reset_idle(state, clear_latch=True, clear_last_attempt=True),
            last_stove_state=None,
            last_app_stop_at=now,
            vacation_was_active=False,
        )
        return SafetyDecision(reset, False, ())
    if not new.enabled:
        return SafetyDecision(state, False, ())
    if state.phase != SafetyPhase.WAITING_RETRY:
        return SafetyDecision(state, False, ())
    updated = state
    if state.last_attempt_at is not None:
        updated = replace(
            updated,
            next_attempt_at=state.last_attempt_at + timedelta(minutes=new.retry_interval_min),
        )
    if updated.attempts >= new.max_attempts:
        failed, event = fail_episode(
            updated,
            REASON_ATTEMPTS_EXHAUSTED,
            attempt=updated.attempts,
            max_attempts=new.max_attempts,
        )
        return SafetyDecision(failed, False, (event,))
    return SafetyDecision(updated, False, ())


def rearm(
    state: SafetyState,
    reason: str,
    ambient_c: float | None = None,
    threshold_c: int | None = None,
) -> SafetyDecision:
    """Rearm from the API: idle, counters cleared, manual latch cleared.

    :param state: Episode to rearm.
    :param reason: Why the user rearmed.
    :param ambient_c: Ambient reading to store on the event, when known.
    :param threshold_c: Threshold to store on the event, when known.
    :return: Idle episode and one ``safety_rearmed`` event.
    """
    detail: dict[str, object] = {"reason": reason}
    if ambient_c is not None:
        detail["ambient_c"] = ambient_c
    if threshold_c is not None:
        detail["threshold_c"] = threshold_c
    return SafetyDecision(
        _reset_idle(state, clear_latch=True),
        False,
        (SafetyEvent(EVENT_REARMED, detail),),
    )


def validate_config(config: SafetyConfig, min_restart_interval_min: int) -> list[str]:
    """Return one English message per value outside its range.

    :param config: Card to check. The fields are already integers.
    :param min_restart_interval_min: Lowest retry interval the stove guard allows.
    :return: Messages, empty when the card is valid.
    """
    issues: list[str] = []
    if not THRESHOLD_MIN_C <= config.threshold_c <= THRESHOLD_MAX_C:
        issues.append(f"threshold_c must be between {THRESHOLD_MIN_C} and {THRESHOLD_MAX_C}")
    if not MAX_ATTEMPTS_MIN <= config.max_attempts <= MAX_ATTEMPTS_MAX:
        issues.append(f"max_attempts must be between {MAX_ATTEMPTS_MIN} and {MAX_ATTEMPTS_MAX}")
    if not min_restart_interval_min <= config.retry_interval_min <= RETRY_INTERVAL_MAX_MIN:
        issues.append(
            "retry_interval_min must be between "
            f"{min_restart_interval_min} and {RETRY_INTERVAL_MAX_MIN}"
        )
    return issues


def derive_status(
    config: SafetyConfig,
    state: SafetyState,
    *,
    vacation_active: bool,
    stale: bool,
) -> SafetyStatus:
    """Derive the API status. The first match wins.

    :param config: Effective safety card.
    :param state: Current episode.
    :param vacation_active: Vacation suspension is active.
    :param stale: The latest poll is stale or failed.
    :return: Status to show.
    """
    if not config.enabled:
        return SafetyStatus.DISABLED
    if vacation_active:
        return SafetyStatus.SUSPENDED_VACATION
    if stale:
        return SafetyStatus.STALE
    if state.paused_reason is not None:
        return SafetyStatus.PAUSED_MANUAL
    if state.phase == SafetyPhase.FAILED:
        return SafetyStatus.FAILED
    if state.phase == SafetyPhase.WAITING_RETRY:
        return SafetyStatus.WAITING_RETRY
    if state.phase == SafetyPhase.RESTARTING:
        return SafetyStatus.RESTARTING
    if state.confirm_count > 0:
        return SafetyStatus.CONFIRMING
    return SafetyStatus.ARMED


def warning_for(config: SafetyConfig, setpoint_c: float) -> str | None:
    """Return the setpoint warning, which informs and does not block a start.

    :param config: Effective safety card.
    :param setpoint_c: Cached stove setpoint.
    :return: ``threshold_not_below_setpoint`` when the threshold is not strictly below it.
    """
    if config.threshold_c >= setpoint_c:
        return WARNING_THRESHOLD_NOT_BELOW_SETPOINT
    return None


def _remember_poll(state: SafetyState, obs: SafetyObservation) -> SafetyState:
    return replace(
        state,
        last_stove_state=obs.stove_state.value,
        last_polled_at=obs.polled_at,
    )


def _end_vacation(state: SafetyState, now: datetime) -> SafetyState:
    # Reason: same cursor as during vacation, so stops from the vacation are not replayed.
    return replace(
        _reset_idle(state, clear_latch=False),
        vacation_was_active=False,
        last_app_stop_at=now,
    )


def _reset_idle(
    state: SafetyState,
    *,
    clear_latch: bool,
    clear_last_attempt: bool = False,
) -> SafetyState:
    """Return an idle episode. The manual latch is kept unless ``clear_latch`` is set."""
    return replace(
        state,
        phase=SafetyPhase.IDLE,
        attempts=0,
        confirm_count=0,
        failure_reason=None,
        next_attempt_at=None,
        paused_reason=None if clear_latch else state.paused_reason,
        last_attempt_at=None if clear_last_attempt else state.last_attempt_at,
    )


def _advance_app_stop(state: SafetyState, at: datetime | None) -> SafetyState:
    if at is None:
        return state
    current = state.last_app_stop_at
    if current is not None and at <= current:
        return state
    return replace(state, last_app_stop_at=at)


def _apply_latch(
    state: SafetyState, obs: SafetyObservation
) -> tuple[SafetyState, list[SafetyEvent]]:
    if (
        obs.stove_state in _LIT
        and state.paused_reason is not None
        and state.last_stove_state in STOPPED_LIKE
    ):
        state = replace(state, paused_reason=None)
    reason = obs.manual_stop_reason
    if reason not in PAUSE_REASONS:
        return state, []
    state = replace(_reset_idle(state, clear_latch=False), paused_reason=reason)
    if reason == PAUSE_APP_STOP:
        state = _advance_app_stop(state, obs.app_stop_at)
    return state, [make_event(EVENT_PAUSED, reason=reason)]


def _apply_rearm(
    config: SafetyConfig,
    state: SafetyState,
    obs: SafetyObservation,
    params: SafetyParams,
) -> tuple[SafetyState, list[SafetyEvent]]:
    if state.phase not in (SafetyPhase.WAITING_RETRY, SafetyPhase.FAILED):
        return state, []
    if _ignition_failed_blocks_rearm(state):
        if _ignition_failed_relit(state, obs):
            return _rearmed(state, REASON_STOVE_LIT, obs.ambient_c, config.threshold_c)
        return state, []
    if obs.stove_state in _LIT:
        return _rearmed(state, REASON_STOVE_LIT, obs.ambient_c, config.threshold_c)
    if _plausible(obs.ambient_c) and obs.ambient_c >= config.threshold_c + params.rearm_hysteresis_c:
        return _rearmed(state, REASON_HYSTERESIS, obs.ambient_c, config.threshold_c)
    return state, []


def _ignition_failed_blocks_rearm(state: SafetyState) -> bool:
    # Reason: a stove that lit and went out may hold a loaded brazier. Warm air does not clear it.
    return state.failure_reason == REASON_IGNITION_FAILED


def _ignition_failed_relit(state: SafetyState, obs: SafetyObservation) -> bool:
    return obs.stove_state in _LIT and state.last_stove_state in STOPPED_LIKE


def _rearmed(
    state: SafetyState,
    reason: str,
    ambient_c: float,
    threshold_c: int,
) -> tuple[SafetyState, list[SafetyEvent]]:
    return _reset_idle(state, clear_latch=True), [
        make_event(EVENT_REARMED, reason=reason, ambient_c=ambient_c, threshold_c=threshold_c)
    ]


def _apply_success(
    config: SafetyConfig,
    state: SafetyState,
    obs: SafetyObservation,
) -> tuple[SafetyState, list[SafetyEvent]]:
    if state.phase != SafetyPhase.RESTARTING:
        return state, []
    # Reason: STANDBY after the flame caught is the stove's energy saving, same as RUNNING.
    if obs.stove_state not in _SUCCESS and not standby_after_ignition(state, obs):
        return state, []
    event = make_event(
        EVENT_RESTART_SUCCEEDED,
        attempt=state.attempts,
        max_attempts=config.max_attempts,
        ambient_c=obs.ambient_c,
        threshold_c=config.threshold_c,
    )
    return _reset_idle(state, clear_latch=False), [event]


def _apply_confirm(
    config: SafetyConfig,
    state: SafetyState,
    obs: SafetyObservation,
    params: SafetyParams,
) -> SafetyState:
    if obs.stove_state in _BURNING:
        if state.confirm_count == 0:
            return state
        return replace(state, confirm_count=0)
    if not _plausible(obs.ambient_c):
        return state
    if obs.ambient_c > config.threshold_c:
        return replace(state, confirm_count=0)
    if state.confirm_count >= params.confirm_polls:
        return state
    return replace(state, confirm_count=state.confirm_count + 1)
