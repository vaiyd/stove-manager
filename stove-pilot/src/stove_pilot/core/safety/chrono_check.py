"""Chrono stop match for one detected panel-stop transition."""

import logging
from collections.abc import Callable
from datetime import datetime

from stove_pilot.core.chrono.models import ChronoConfig
from stove_pilot.core.chrono.stops import stop_matches

_LOGGER = logging.getLogger(__name__)


class ChronoStopMatcher:
    """Callable chrono check. The getter runs only when the matcher is called.

    The facade calls it only after a panel-stop transition with no recent server
    stop, so a poll that is not a stop does not read the chrono configuration.
    """

    def __init__(self, get_config: Callable[[], ChronoConfig], window_min: int) -> None:
        """Bind the configuration getter and the match window.

        :param get_config: Returns the chrono configuration. May read the stove on a miss.
        :param window_min: Minutes after a stop minute that still count.
        """
        self._get_config = get_config
        self._window_min = window_min

    def __call__(self, previous_local: datetime, current_local: datetime) -> bool | None:
        """Tell whether an enabled chrono stop explains the transition.

        :param previous_local: Stove-local time of the previous poll.
        :param current_local: Stove-local time of the poll that shows the stop.
        :return: The match, or ``None`` when the configuration cannot be read.
        """
        try:
            config = self._get_config()
        except Exception as exc:
            _LOGGER.warning("Chrono stop check failed (%s)", type(exc).__name__)
            return None
        return stop_matches(config, previous_local, current_local, self._window_min)
