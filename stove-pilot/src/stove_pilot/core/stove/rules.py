"""Pure guards protecting the stove from unsafe or useless commands."""

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum

from stove_pilot.core.stove.models import EventSource, StoveState, StoveStatus


class Decision(StrEnum):
    """Outcome of a command guard."""

    APPLY = "apply"
    NO_OP = "no_op"
    DEFER = "defer"
    REJECT = "reject"


@dataclass(frozen=True)
class GuardResult:
    """Guard decision with a machine-readable reason."""

    decision: Decision
    reason: str | None = None


@dataclass(frozen=True)
class GuardPolicy:
    """Tunable safety limits."""

    min_restart_interval: timedelta


_ALREADY_ON = {StoveState.IGNITION, StoveState.RUNNING, StoveState.CLEANING, StoveState.STANDBY}


def decide_start(
    status: StoveStatus, *, last_ignition_at: datetime | None, now: datetime, policy: GuardPolicy
) -> GuardResult:
    """Decide whether a start command may be sent now.

    :param status: Fresh stove status.
    :param last_ignition_at: Last applied start, if any.
    :param now: Current time (aware).
    :param policy: Safety limits.
    :return: Guard decision.
    """
    if status.state in _ALREADY_ON:
        return GuardResult(Decision.NO_OP, "already_on")
    if status.state is StoveState.SHUTDOWN:
        return GuardResult(Decision.DEFER, "cooling_down")
    if status.state is not StoveState.OFF:
        return GuardResult(Decision.REJECT, f"state_{status.state.value.lower()}")
    if last_ignition_at and now - last_ignition_at < policy.min_restart_interval:
        return GuardResult(Decision.DEFER, "min_restart_interval")
    return GuardResult(Decision.APPLY)


def decide_stop(
    status: StoveStatus, *, source: EventSource, confirm_ignition_stop: bool = False
) -> GuardResult:
    """Decide whether a stop command may be sent now.

    :param status: Fresh stove status.
    :param source: Who asks for the stop.
    :param confirm_ignition_stop: User confirmed a stop during ignition.
    :return: Guard decision.
    """
    if status.state is StoveState.IGNITION:
        # Reason: a stop during ignition forces an on-site brazier emptying (manual, VIDER BRASERO).
        if source is not EventSource.USER:
            return GuardResult(Decision.DEFER, "ignition_in_progress")
        if not confirm_ignition_stop:
            return GuardResult(Decision.REJECT, "confirm_ignition_stop_required")
        return GuardResult(Decision.APPLY)
    if status.state in _ALREADY_ON:
        return GuardResult(Decision.APPLY)
    if status.state in {StoveState.OFF, StoveState.SHUTDOWN}:
        return GuardResult(Decision.NO_OP, "already_off")
    return GuardResult(Decision.REJECT, f"state_{status.state.value.lower()}")
