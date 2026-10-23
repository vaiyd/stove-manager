"""Live regions: last-good cache so a refresh never blanks the page when the API is down."""

import logging
import threading
from collections.abc import Callable
from typing import Any

from stove_pilot.service.ui.client import ApiProblem

_LOGGER = logging.getLogger(__name__)


class LastGoodCache:
    """Keeps the last successfully loaded view of each live region, per UI process."""

    def __init__(self) -> None:
        """Start with an empty cache."""
        self._lock = threading.Lock()
        self._items: dict[str, Any] = {}

    def fetch[T](self, key: str, loader: Callable[[], T]) -> tuple[T | None, bool]:
        """Load a region view, falling back to the last good one when the API fails.

        :param key: Region name.
        :param loader: Builds the view from the API (may raise ``ApiProblem``).
        :return: The view (``None`` when nothing was ever loaded) and the ``api_down`` flag.
        """
        try:
            view = loader()
        except ApiProblem as problem:
            _LOGGER.warning("Region %s kept as stale (%s %s)", key, problem.status, problem.code)
            with self._lock:
                return self._items.get(key), True
        with self._lock:
            self._items[key] = view
        return view, False
