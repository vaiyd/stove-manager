"""Phase table of the safety restart guard (pure helpers for rules)."""

from dataclasses import replace
from datetime import datetime, timedelta

from stove_pilot.core.safety.detection import RUNNING_LIKE, STOPPED_LIKE
from stove_pilot.core.safety.models import (
    EVENT_RESTART,
    EVENT_RESTART_FAILED,
    NO_IGNITION_WAIT,
    REASON_ALARM,
    REASON_ATTEMPTS_EXHAUSTED,
    REASON_IGNITION_FAILED,
    REASON_UNKNOWN,
    SafetyConfig,
    SafetyDecision,
    SafetyEvent,
    SafetyObservation,
    SafetyParams,
    SafetyPhase,
    SafetyState,
)
from stove_pilot.core.stove.models import StoveState


def apply_phase(
    config: SafetyConfig,
    state: SafetyState,
    obs: SafetyObservation,
    params: SafetyParams,
    now: datetime,
) -> tuple[SafetyState, bool, list[SafetyEvent]]:
    """Advance the episode for one fresh poll.

    ``state.last_stove_state`` is still the previous poll.

    :param config: Effective safety card.
    :param state: Episode after the latch and the success check.
    :param obs: Fresh poll.
    :param params: Container knobs.
    :param now: Evaluation instant.
    :return: Updated episode, whether to start, and events.
    """
    if state.phase == SafetyPhase.FAILED:
        return state, False, []
    if state.phase == SafetyPhase.RESTARTING:
        return _phase_restarting(config, state, obs, now)
    if state.phase == SafetyPhase.WAITING_RETRY:
        return _phase_due(config, state, obs, params, now, require_due=True)
    if state.phase == SafetyPhase.IDLE:
        return _phase_due(config, state, obs, params, now, require_due=False)
    raise ValueError(f"Unknown safety phase {state.phase!r}")


def _phase_restarting(
    config: SafetyConfig,
    state: SafetyState,
    obs: SafetyObservation,
    now: datetime,
) -> tuple[SafetyState, bool, list[SafetyEvent]]:
    if obs.stove_state == StoveState.UNKNOWN:
        return _fail(state, config, obs, REASON_UNKNOWN)
    if _is_alarm(obs):
        return _fail(state, config, obs, REASON_ALARM)
    if standby_after_ignition(state, obs):
        # Reason: success is recorded by the rules. This poll must not fail or time out.
        return state, False, []
    if _ignition_failed(state, obs):
        return _fail(state, config, obs, REASON_IGNITION_FAILED)
    timed_out = (
        obs.stove_state == StoveState.OFF
        and not obs.pending_start
        and _ignition_wait_elapsed(state, now)
    )
    if not timed_out:
        return state, False, []
    if state.attempts < config.max_attempts:
        return _to_waiting_retry(state, config), False, []
    updated, event = fail_episode(
        state,
        REASON_ATTEMPTS_EXHAUSTED,
        attempt=state.attempts,
        max_attempts=config.max_attempts,
        ambient_c=obs.ambient_c,
        threshold_c=config.threshold_c,
    )
    return updated, False, [event]


def _phase_due(
    config: SafetyConfig,
    state: SafetyState,
    obs: SafetyObservation,
    params: SafetyParams,
    now: datetime,
    *,
    require_due: bool,
) -> tuple[SafetyState, bool, list[SafetyEvent]]:
    if require_due and not _retry_due(state, config, now):
        return state, False, []
    if state.confirm_count < params.confirm_polls or state.paused_reason is not None:
        return state, False, []
    if obs.stove_state == StoveState.UNKNOWN:
        return _blocked(state, config, obs, REASON_UNKNOWN)
    if _is_alarm(obs):
        return _blocked(state, config, obs, REASON_ALARM)
    if obs.stove_state == StoveState.OFF and not obs.pending_start:
        # Reason: the attempt, the timestamps and the restarting phase are applied by after_start.
        return state, True, []
    return state, False, []


def _blocked(
    state: SafetyState,
    config: SafetyConfig,
    obs: SafetyObservation,
    reason: str,
) -> tuple[SafetyState, bool, list[SafetyEvent]]:
    updated, event = fail_episode(
        state,
        reason,
        attempt=state.attempts,
        max_attempts=config.max_attempts,
        ambient_c=obs.ambient_c,
        threshold_c=config.threshold_c,
    )
    return updated, False, [event]


def _to_waiting_retry(state: SafetyState, config: SafetyConfig) -> SafetyState:
    next_at = state.next_attempt_at
    if state.last_attempt_at is not None:
        next_at = state.last_attempt_at + timedelta(minutes=config.retry_interval_min)
    return replace(
        state,
        phase=SafetyPhase.WAITING_RETRY,
        failure_reason=None,
        next_attempt_at=next_at,
    )


def count_attempt(
    config: SafetyConfig,
    state: SafetyState,
    now: datetime,
    ambient_c: float | None,
    threshold_c: int | None,
) -> SafetyDecision:
    """Record one start attempt and enter ``restarting``.

    :param config: Effective safety card.
    :param state: Episode that requested the start.
    :param now: Instant of the attempt.
    :param ambient_c: Ambient reading that triggered the attempt.
    :param threshold_c: Threshold in force, or ``None`` to use the card.
    :return: Episode in ``restarting`` and one ``safety_restart`` event.
    """
    attempts = state.attempts + 1
    updated = replace(
        state,
        phase=SafetyPhase.RESTARTING,
        attempts=attempts,
        last_attempt_at=now,
        next_attempt_at=now + timedelta(minutes=config.retry_interval_min),
        failure_reason=None,
    )
    event = make_event(
        EVENT_RESTART,
        attempt=attempts,
        max_attempts=config.max_attempts,
        ambient_c=ambient_c,
        threshold_c=threshold_or_default(config, threshold_c),
    )
    return SafetyDecision(updated, False, (event,))


def fail_episode(
    state: SafetyState,
    reason: str,
    *,
    attempt: int,
    max_attempts: int,
    ambient_c: float | None = None,
    threshold_c: int | None = None,
) -> tuple[SafetyState, SafetyEvent]:
    """End the episode as failed and build the failure event.

    :param state: Episode to fail.
    :param reason: Failure reason stored on the episode and the event.
    :param attempt: Attempts already counted.
    :param max_attempts: Configured maximum.
    :param ambient_c: Ambient reading, when known.
    :param threshold_c: Threshold in force, when known.
    :return: Failed episode and one ``safety_restart_failed`` event.
    """
    detail: dict[str, object] = {
        "reason": reason,
        "attempt": attempt,
        "max_attempts": max_attempts,
    }
    if ambient_c is not None:
        detail["ambient_c"] = ambient_c
    if threshold_c is not None:
        detail["threshold_c"] = threshold_c
    updated = replace(
        state,
        phase=SafetyPhase.FAILED,
        failure_reason=reason,
        next_attempt_at=None,
    )
    return updated, SafetyEvent(EVENT_RESTART_FAILED, detail)


def make_event(kind: str, **detail: object) -> SafetyEvent:
    """Build one safety event.

    :param kind: Event kind.
    :param detail: JSON-serializable fields.
    :return: The event.
    """
    return SafetyEvent(kind=kind, detail=dict(detail))


def threshold_or_default(config: SafetyConfig, threshold_c: int | None) -> int:
    """Return ``threshold_c``, or the card value when it is ``None``.

    :param config: Effective safety card.
    :param threshold_c: Threshold in force, or ``None``.
    :return: The threshold to store.
    """
    return config.threshold_c if threshold_c is None else threshold_c


def standby_after_ignition(state: SafetyState, obs: SafetyObservation) -> bool:
    """Tell whether STANDBY follows IGNITION during this restart.

    The stove is then on its own energy-saving logic. A later stop is not
    ``ignition_failed``. STANDBY with no IGNITION on the previous poll is not this case.

    :param state: Episode before the success check. ``last_stove_state`` is the previous poll.
    :param obs: Fresh poll.
    :return: ``True`` when STANDBY counts as success.
    """
    return obs.stove_state == StoveState.STANDBY and state.last_stove_state == StoveState.IGNITION


def _retry_due(state: SafetyState, config: SafetyConfig, now: datetime) -> bool:
    if state.last_attempt_at is None:
        return False
    return now >= state.last_attempt_at + timedelta(minutes=config.retry_interval_min)


def _fail(
    state: SafetyState,
    config: SafetyConfig,
    obs: SafetyObservation,
    reason: str,
) -> tuple[SafetyState, bool, list[SafetyEvent]]:
    updated, event = fail_episode(
        state,
        reason,
        attempt=state.attempts,
        max_attempts=config.max_attempts,
        ambient_c=obs.ambient_c,
        threshold_c=config.threshold_c,
    )
    return updated, False, [event]


def _is_alarm(obs: SafetyObservation) -> bool:
    if obs.stove_state is StoveState.ALARM:
        return True
    return bool(obs.alarm_code)


def _ignition_failed(state: SafetyState, obs: SafetyObservation) -> bool:
    # Reason: a flame that appeared and then went out is not retried (brazier risk).
    if standby_after_ignition(state, obs):
        return False
    if state.last_stove_state not in RUNNING_LIKE:
        return False
    return obs.stove_state in STOPPED_LIKE


def _ignition_wait_elapsed(state: SafetyState, now: datetime) -> bool:
    if state.last_attempt_at is None:
        return False
    return now >= state.last_attempt_at + NO_IGNITION_WAIT
