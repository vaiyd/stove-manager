"""In-memory sliding-window rate limit for the start and stop commands (single process)."""

import math
import threading
import time
from collections import defaultdict, deque
from collections.abc import Callable
from typing import Final

from stove_pilot.service.api.errors import ApiProblemError

COMMAND_LIMIT: Final = 6
COMMAND_WINDOW_S: Final = 600.0


class SlidingWindowLimiter:
    """Allow at most ``limit`` hits per ``window_s`` seconds for each key."""

    def __init__(
        self,
        limit: int = COMMAND_LIMIT,
        window_s: float = COMMAND_WINDOW_S,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        """Configure the window.

        :param limit: Maximum hits per window.
        :param window_s: Window length in seconds.
        :param clock: Monotonic time source in seconds.
        """
        self._limit = limit
        self._window_s = window_s
        self._clock = clock
        self._lock = threading.Lock()
        self._hits: defaultdict[str, deque[float]] = defaultdict(deque)

    def hit(self, key: str) -> None:
        """Record one attempt for a key.

        :param key: Limit bucket (for example the endpoint name).
        :raises ApiProblemError: The window is full (429 with ``Retry-After``).
        """
        now = self._clock()
        with self._lock:
            hits = self._hits[key]
            while hits and now - hits[0] >= self._window_s:
                hits.popleft()
            if len(hits) >= self._limit:
                retry_after = max(1, math.ceil(self._window_s - (now - hits[0])))
                raise ApiProblemError(
                    429,
                    "rate_limited",
                    "Too many commands",
                    f"At most {self._limit} {key} commands are accepted per "
                    f"{int(self._window_s // 60)} minutes; retry in {retry_after} s.",
                    headers={"Retry-After": str(retry_after)},
                )
            hits.append(now)
