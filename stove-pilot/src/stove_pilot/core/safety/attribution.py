"""Manual-stop attribution for one fresh safety poll.

A user stop (``applied``, ``unknown`` or ``no_op``) latches. A panel transition
latches unless a non-user stop, newer than the previous poll and inside the
lookback, explains it. A user stop in that window latches as an app stop
instead of explaining the transition. A gap above ``min(max(3 * poll_interval_s, 90), 300)``
seconds, or a missing previous poll, is doubt and counts as manual. A
lit-to-stopped step while a restart is in progress is not a panel latch:
the phase machine records ``ignition_failed``. ``STANDBY`` after ``IGNITION``
during that restart is success, so a later stop is attributed normally.
"""

import logging
from datetime import datetime, timedelta

from stove_pilot.core.safety.detection import (
    RUNNING_LIKE,
    STOPPED_LIKE,
    classify_stop,
    is_stop_transition,
)
from stove_pilot.core.safety.models import (
    PAUSE_APP_STOP,
    PAUSE_PANEL_STOP,
    SafetyPhase,
    SafetyState,
)
from stove_pilot.core.safety.ports import ChronoStopCheck, StopCommandLookup
from stove_pilot.core.stove.models import EventSource, StoveStatus

_LOGGER = logging.getLogger(__name__)
_GAP_FLOOR_S = 90
_GAP_CAP_S = 300


def attribute_manual_stop(
    state: SafetyState,
    status: StoveStatus,
    now: datetime,
    lookup: StopCommandLookup,
    chrono_check: ChronoStopCheck | None,
    lookback_min: int,
    poll_interval_s: float,
) -> tuple[str | None, datetime | None]:
    """Return the manual-stop reason and the app-stop time for one poll.

    An app stop wins. A panel stop is considered only on a running-to-stopped
    transition. A poll gap, or a missing previous poll, is doubt (manual)
    before any stop command can explain the transition.

    :param state: Episode before this poll.
    :param status: Fresh stove status.
    :param now: Evaluation instant.
    :param lookup: Stop-command history.
    :param chrono_check: Chrono matcher, or ``None`` when chrono is disabled.
    :param lookback_min: Minutes of stop-command history considered.
    :param poll_interval_s: Poll period used to size the doubt gap.
    :return: ``(reason, app_stop_at)``. ``app_stop_at`` is set only for an app stop.
    """
    stopped_at = lookup.newest_user_stop_since(state.last_app_stop_at)
    if stopped_at is not None:
        return PAUSE_APP_STOP, stopped_at
    if not is_stop_transition(state.last_stove_state, status.state):
        return None, None
    if _gap_is_doubt(state, status, poll_interval_s):
        return PAUSE_PANEL_STOP, None
    # Reason: the restarting episode owns this step and fails it as ignition_failed.
    if _restart_went_out(state, status):
        return None, None
    found = lookup.server_stop_within(now, lookback_min, not_before=state.last_polled_at)
    if found is not None:
        source, at = found
        if source == EventSource.USER.value:
            return PAUSE_APP_STOP, at
        return None, None
    matched = chrono_match(state, status, chrono_check, poll_interval_s)
    reason = classify_stop(state.last_stove_state, status.state, False, matched)
    if reason is None:
        return None, None
    return reason, None


def chrono_match(
    state: SafetyState,
    status: StoveStatus,
    chrono_check: ChronoStopCheck | None,
    poll_interval_s: float,
) -> bool | None:
    """Return the chrono match for a panel transition, or ``None`` when unknown.

    A gap above :func:`_gap_bound_s`, or a missing previous poll, is doubt.

    :param state: Episode holding the previous poll time.
    :param status: Fresh status (clock drift and poll time).
    :param chrono_check: Chrono matcher, or ``None``.
    :param poll_interval_s: Poll period used to size the doubt gap.
    :return: Match result, or ``None`` for doubt.
    """
    if chrono_check is None or status.clock_drift_s is None or state.last_polled_at is None:
        return None
    if _gap_is_doubt(state, status, poll_interval_s):
        return None
    drift = timedelta(seconds=status.clock_drift_s)
    previous_local = state.last_polled_at + drift
    current_local = status.polled_at + drift
    try:
        return chrono_check(previous_local, current_local)
    except Exception as exc:
        _LOGGER.warning("Chrono stop check failed (%s)", type(exc).__name__)
        return None


def _restart_went_out(state: SafetyState, status: StoveStatus) -> bool:
    """Tell whether a restarting episode just saw the flame go out.

    :param state: Episode before this poll.
    :param status: Fresh stove status.
    :return: ``True`` when the phase machine should record ``ignition_failed``.
    """
    if state.phase is not SafetyPhase.RESTARTING:
        return False
    if state.last_stove_state not in RUNNING_LIKE:
        return False
    return status.state in STOPPED_LIKE


def _gap_bound_s(poll_interval_s: float) -> float:
    return min(max(3 * poll_interval_s, _GAP_FLOOR_S), _GAP_CAP_S)


def _gap_is_doubt(state: SafetyState, status: StoveStatus, poll_interval_s: float) -> bool:
    if state.last_polled_at is None:
        return True
    gap = (status.polled_at - state.last_polled_at).total_seconds()
    return gap < 0 or gap > _gap_bound_s(poll_interval_s)
