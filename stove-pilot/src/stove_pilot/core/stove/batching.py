"""Adaptive batch size for module requests: shrinks after blank replies, recovers slowly."""

import logging
import threading
from typing import Final

# Reason: a degraded module answers small batches reliably; wait before trusting larger ones.
RECOVERY_SUCCESSES: Final = 20

_LOGGER = logging.getLogger(__name__)


class AdaptiveBatch:
    """Current batch size shared by reads and writes (only reads drive it)."""

    def __init__(self, max_size: int, *, recovery_successes: int = RECOVERY_SUCCESSES) -> None:
        """Start at the configured size.

        :param max_size: Configured (and maximum) batch size, at least 1.
        :param recovery_successes: Successful read requests before trying a larger size.
        """
        if max_size < 1:
            raise ValueError("max_size must be at least 1")
        self._max_size = max_size
        self._recovery_successes = recovery_successes
        self._size = max_size
        self._probe_from: int | None = None
        self._streak = 0
        self._lock = threading.Lock()

    @property
    def size(self) -> int:
        """Current batch size, for diagnostics and reads."""
        return self._size

    @property
    def write_size(self) -> int:
        """Batch size for writes: never a size that a read has not confirmed yet."""
        probe_from = self._probe_from
        return self._size if probe_from is None else probe_from

    def read_succeeded(self, count: int) -> None:
        """Record one successful read request.

        :param count: Number of items in the request.
        """
        with self._lock:
            if self._probe_from is not None:
                if count >= self._size:
                    self._probe_from = None
                    self._streak = 0
                return
            if self._size >= self._max_size:
                return
            self._streak += 1
            if self._streak >= self._recovery_successes:
                self._raise_locked()

    def read_failed(self) -> None:
        """Record a failed read request: the success streak restarts."""
        with self._lock:
            self._streak = 0

    def lower_to(self, size: int) -> None:
        """Lower the size to the largest request size that just succeeded after a failure.

        :param size: Largest successful request size (clamped to at least 1).
        """
        with self._lock:
            size = max(1, size)
            self._streak = 0
            self._probe_from = None
            if size >= self._size:
                return
            _LOGGER.warning("Module batch size lowered from %d to %d", self._size, size)
            self._size = size

    def _raise_locked(self) -> None:
        """Try the next larger size once (double, capped); the lock is held."""
        new_size = min(self._size * 2, self._max_size)
        _LOGGER.info("Module batch size raised from %d to %d (trial)", self._size, new_size)
        self._probe_from = self._size
        self._size = new_size
        self._streak = 0
