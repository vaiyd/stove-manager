"""Once-per-instance safety log lines. The repository owns the instance."""

import logging
import threading

from stove_pilot.common.exceptions import ConfigurationError

_LOGGER = logging.getLogger(__name__)


class SafetyDiagnostics:
    """Log lines that must appear at most once for this instance."""

    def __init__(self) -> None:
        """Start with both lines still unlogged."""
        self._lock = threading.Lock()
        self._unreadable_logged = False
        self._retry_floor_warned = False

    def note_unreadable(self, exc: ConfigurationError) -> None:
        """Log one ERROR when the stored safety rows cannot be read.

        :param exc: Decode failure. Its message is not logged.
        """
        with self._lock:
            if self._unreadable_logged:
                return
            self._unreadable_logged = True
        _LOGGER.error("Safety state is unreadable (%s)", type(exc).__name__)

    def warn_retry_floor(self) -> None:
        """Log one WARNING when the stored retry interval is below the floor."""
        with self._lock:
            if self._retry_floor_warned:
                return
            self._retry_floor_warned = True
        _LOGGER.warning(
            "Safety retry interval is below the restart floor; the effective value was raised"
        )
