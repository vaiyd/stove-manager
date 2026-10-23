"""Dashboard page and its live partials (glance, controls, events, sparkline)."""

from collections.abc import Callable

from flask import Flask, render_template, url_for

from stove_pilot.common.settings import Settings
from stove_pilot.service.ui.client import StovePilotApiClient
from stove_pilot.service.ui.live import LastGoodCache
from stove_pilot.service.ui.regions import (
    load_controls,
    load_events,
    load_glance,
    load_sparkline,
    local_now,
)
from stove_pilot.service.ui.routes.params import query_max_age


def register_dashboard_routes(
    app: Flask,
    settings: Settings,
    get_api_client: Callable[[], StovePilotApiClient],
    cache: LastGoodCache,
) -> None:
    """Add the dashboard page and its partial endpoints (all read only).

    :param app: UI application.
    :param settings: Application settings (timezone).
    :param get_api_client: Lazy getter of the API client.
    :param cache: Last-good cache of the live regions.
    """

    @app.get("/")
    def dashboard() -> str:
        client, now = get_api_client(), local_now(settings.timezone)
        glance, glance_down = cache.fetch("glance", lambda: load_glance(client, now, None))
        controls, controls_down = cache.fetch("controls", lambda: load_controls(client, now, None))
        events, events_down = cache.fetch("events", lambda: load_events(client, now))
        spark, spark_down = cache.fetch("sparkline", lambda: load_sparkline(client, now))
        return render_template(
            "dashboard.html",
            glance=glance,
            glance_stale=glance_down,
            partial_url=url_for("glance_partial"),
            compact=False,
            show_safety=True,
            controls=controls,
            controls_stale=controls_down,
            events=events,
            events_stale=events_down,
            chart=spark,
            sparkline_stale=spark_down,
        )

    @app.get("/partials/glance")
    def glance_partial() -> str:
        client, now = get_api_client(), local_now(settings.timezone)
        max_age_s = query_max_age()
        glance, down = cache.fetch("glance", lambda: load_glance(client, now, max_age_s))
        return render_template(
            "partials/glance.html",
            glance=glance,
            glance_stale=down,
            partial_url=url_for("glance_partial"),
            compact=False,
            show_safety=True,
        )

    @app.get("/partials/controls")
    def controls_partial() -> str:
        client, now = get_api_client(), local_now(settings.timezone)
        max_age_s = query_max_age()
        controls, down = cache.fetch("controls", lambda: load_controls(client, now, max_age_s))
        return render_template("partials/controls.html", controls=controls, controls_stale=down)

    @app.get("/partials/events")
    def events_partial() -> str:
        client, now = get_api_client(), local_now(settings.timezone)
        events, down = cache.fetch("events", lambda: load_events(client, now))
        return render_template("partials/events.html", events=events, events_stale=down)

    @app.get("/partials/sparkline")
    def sparkline_partial() -> str:
        client, now = get_api_client(), local_now(settings.timezone)
        spark, down = cache.fetch("sparkline", lambda: load_sparkline(client, now))
        return render_template(
            "partials/sparkline.html", chart=spark, sparkline_stale=down
        )
