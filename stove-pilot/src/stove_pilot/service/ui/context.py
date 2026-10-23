"""Template context: translations, navigation, and refresh settings shared by every page."""

import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Final

from flask import Flask, request, url_for

from stove_pilot.common.settings import Settings
from stove_pilot.service.ui.client import ApiProblem, StovePilotApiClient
from stove_pilot.service.ui.i18n import t
from stove_pilot.service.ui.nav import visible_entries

_LOGGER = logging.getLogger(__name__)
_FEATURES_TTL_S: Final = 30.0
_RETRY_AFTER_FAILURE_S: Final = 5.0
REFRESH_CHOICES: Final = (10, 30, 60, 300, 0)


@dataclass(frozen=True, slots=True)
class NavItem:
    """A visible menu entry ready for the templates."""

    key: str
    label: str
    url: str
    icon: str
    active: bool


class FeaturesCache:
    """The enabled features, cached briefly; the last known value survives an API outage."""

    def __init__(self, get_api_client: Callable[[], StovePilotApiClient]) -> None:
        """Bind the client getter.

        :param get_api_client: Lazy getter of the API client.
        """
        self._get_api_client = get_api_client
        self._lock = threading.Lock()
        self._features: dict[str, bool] = {}
        self._loaded_at = float("-inf")

    def get(self) -> dict[str, bool]:
        """Return the enabled features (empty until the API answered once).

        :return: Feature name to enabled flag.
        """
        with self._lock:
            if time.monotonic() - self._loaded_at < _FEATURES_TTL_S:
                return self._features
            try:
                self._features = self._get_api_client().features()
                self._loaded_at = time.monotonic()
            except ApiProblem as problem:
                _LOGGER.warning("Features unavailable (%s %s)", problem.status, problem.code)
                # Reason: retry soon instead of waiting a full TTL, without hammering a dead API.
                self._loaded_at = time.monotonic() - _FEATURES_TTL_S + _RETRY_AFTER_FAILURE_S
            return self._features


def register_context(
    app: Flask, settings: Settings, get_api_client: Callable[[], StovePilotApiClient]
) -> None:
    """Expose ``t``, ``nav_items`` and the refresh settings to every template.

    :param app: UI application.
    :param settings: Application settings.
    :param get_api_client: Lazy getter of the API client.
    """
    features = FeaturesCache(get_api_client)
    app.jinja_env.globals["t"] = t

    def nav_items() -> list[NavItem]:
        endpoint = request.endpoint or ""
        return [
            NavItem(
                entry.key,
                t(entry.label_key),
                url_for(entry.endpoint),
                entry.icon,
                endpoint == entry.endpoint or (request.blueprint or "") == entry.key,
            )
            for entry in visible_entries(features.get(), app.view_functions)
        ]

    @app.context_processor
    def inject() -> dict[str, Any]:
        return {
            "nav_items": nav_items,
            "refresh_default": settings.ui_refresh_default_s,
            "poll_interval_s": settings.poll_interval_s,
            "refresh_choices": REFRESH_CHOICES,
            "vacation_setpoint_text": f"{settings.vacation_setpoint_c:g}",
        }
