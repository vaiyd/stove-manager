"""Manual-stop attribution for the safety latch.

Pure predicates over event fields and stove states. A doubtful attribution
counts as manual so the safety does not restart the stove.
"""

from typing import Final

from stove_pilot.core.stove.models import EventSource, StoveState

SERVER_STOP_LOOKBACK_MIN: Final = 5

RUNNING_LIKE: Final[frozenset[StoveState]] = frozenset(
    (
        StoveState.IGNITION,
        StoveState.RUNNING,
        StoveState.CLEANING,
        StoveState.STANDBY,
    )
)
STOPPED_LIKE: Final[frozenset[StoveState]] = frozenset((StoveState.SHUTDOWN, StoveState.OFF))

_USER_STOP_RESULTS: Final[frozenset[str]] = frozenset(("applied", "unknown", "no_op"))


def is_user_app_stop(command: str, source: str, result: str) -> bool:
    """Tell whether a command event is a user stop that arms the manual latch.

    A still-``deferred`` result is not a stop yet. The repository records a
    deferred stop again as ``applied`` once it is sent, or as ``unknown`` when
    the write outcome was not confirmed; that later event is the one that counts.
    The ``deferred`` flag is not an input: only ``result`` is. ``no_op`` counts:
    the user asked for a stop and the stove was already off.

    :param command: Command name stored on the event.
    :param source: Event source name.
    :param result: Command result stored on the event.
    :return: ``True`` for ``stop`` from ``USER`` with ``applied``, ``unknown`` or ``no_op``.
    """
    return command == "stop" and source == EventSource.USER and result in _USER_STOP_RESULTS


def is_stop_transition(previous: StoveState | str | None, current: StoveState | str) -> bool:
    """Tell whether the stove went from a running-like state to shutdown or off.

    :param previous: State before the poll; ``None`` is not a transition.
    :param current: State of the poll that shows the stove stopped.
    :return: ``True`` from ``IGNITION``, ``RUNNING``, ``CLEANING`` or ``STANDBY``.
    """
    if previous is None:
        return False
    return previous in RUNNING_LIKE and current in STOPPED_LIKE


def panel_stop_is_manual(*, server_stop_recent: bool, chrono_match: bool | None) -> bool:
    """Tell whether a stop transition is a manual panel stop.

    A server stop inside ``SERVER_STOP_LOOKBACK_MIN`` is the server's own
    command, so this returns false. Otherwise anything that is not a positive
    chrono match is manual. ``chrono_match is None`` (disabled, unreadable, or
    any doubt) is manual.

    :param server_stop_recent: A server stop was sent within the lookback.
    :param chrono_match: An enabled chrono stop matched; ``None`` means doubt.
    :return: ``True`` when the panel stop must set the manual latch.
    """
    if server_stop_recent:
        return False
    return chrono_match is not True


def classify_stop(
    previous_state: StoveState | str | None,
    current_state: StoveState | str,
    server_stop_recent: bool,
    chrono_match: bool | None,
) -> str | None:
    """Classify a poll pair as a manual panel stop.

    :param previous_state: State before the transition.
    :param current_state: State after the transition.
    :param server_stop_recent: A server stop was sent within the lookback.
    :param chrono_match: An enabled chrono stop matched; ``None`` means doubt.
    :return: ``"panel_stop"`` when the latch must be set, otherwise ``None``.
    """
    if not is_stop_transition(previous_state, current_state):
        return None
    if not panel_stop_is_manual(server_stop_recent=server_stop_recent, chrono_match=chrono_match):
        return None
    return "panel_stop"
