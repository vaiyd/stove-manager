"""Embeddable read-only widget: ``/widget`` page and its glance partial (no controls)."""

from collections.abc import Callable
from typing import Final

from flask import Flask, render_template, request, url_for
from flask_wtf.csrf import CSRFProtect

from stove_pilot.common.settings import Settings
from stove_pilot.service.ui.client import StovePilotApiClient
from stove_pilot.service.ui.live import LastGoodCache
from stove_pilot.service.ui.regions import load_glance, local_now
from stove_pilot.service.ui.routes.params import query_flag, query_max_age

WIDGET_REFRESH_CHOICES: Final = (10, 30, 60, 300)
WIDGET_REFRESH_DEFAULT_S: Final = 30
_THEMES: Final = {"light": "caramellatte", "dark": "coffee"}


def _widget_refresh() -> int:
    """Read the ``refresh`` query parameter (``0`` and unknown values fall back to the default).

    :return: Refresh interval in seconds.
    """
    value = request.args.get("refresh", type=int)
    return value if value in WIDGET_REFRESH_CHOICES else WIDGET_REFRESH_DEFAULT_S


def register_widget_routes(
    app: Flask,
    settings: Settings,
    get_api_client: Callable[[], StovePilotApiClient],
    cache: LastGoodCache,
    csrf: CSRFProtect,
) -> None:
    """Add the widget routes (GET only, CSRF-exempt, same last-good cache as the dashboard).

    :param app: UI application.
    :param settings: Application settings (timezone).
    :param get_api_client: Lazy getter of the API client.
    :param cache: Last-good cache of the live regions.
    :param csrf: CSRF protection the read-only routes are exempted from.
    """

    @app.get("/widget")
    def widget() -> str:
        client, now = get_api_client(), local_now(settings.timezone)
        glance, down = cache.fetch("glance", lambda: load_glance(client, now, None))
        compact = query_flag("compact")
        return render_template(
            "widget.html",
            glance=glance,
            glance_stale=down,
            compact=compact,
            theme=_THEMES.get(request.args.get("theme", "auto")),
            refresh_s=_widget_refresh(),
            partial_url=url_for("widget_glance", compact=1 if compact else None),
        )

    @app.get("/widget/glance")
    def widget_glance() -> str:
        client, now = get_api_client(), local_now(settings.timezone)
        max_age_s = query_max_age()
        glance, down = cache.fetch("glance", lambda: load_glance(client, now, max_age_s))
        compact = query_flag("compact")
        return render_template(
            "partials/glance.html",
            glance=glance,
            glance_stale=down,
            compact=compact,
            partial_url=url_for("widget_glance", compact=1 if compact else None),
        )

    csrf.exempt(widget)
    csrf.exempt(widget_glance)
