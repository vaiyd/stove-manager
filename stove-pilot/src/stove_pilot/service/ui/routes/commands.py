"""Same-origin action endpoints: forward a command to the API, answer with fresh controls."""

from collections.abc import Callable
from datetime import datetime
from typing import Any, Final

from flask import Flask, Response, render_template, request

from stove_pilot.common.settings import Settings
from stove_pilot.service.ui.client import ApiProblem, StovePilotApiClient
from stove_pilot.service.ui.i18n import t
from stove_pilot.service.ui.live import LastGoodCache
from stove_pilot.service.ui.regions import load_controls, local_now
from stove_pilot.service.ui.toasts import (
    Toast,
    change_toast,
    outcome_toast,
    problem_toast,
    suspend_toasts,
)

_REFRESH_NOW_EVENT: Final = "refresh-now"
_FORCE_LIVE_S: Final = 0.0
_INVALID_INPUT: Final = ApiProblem(422, "validation_failed", "")


def _parse_until(raw: str) -> datetime | None:
    """Parse the ``datetime-local`` field of the suspend form.

    :param raw: Field value (``YYYY-MM-DDTHH:MM``), possibly empty.
    :return: The naive datetime (the API reads it in its timezone), or ``None`` when empty.
    :raises ValueError: The text is not a datetime.
    """
    return datetime.fromisoformat(raw) if raw.strip() else None


def register_command_routes(
    app: Flask,
    settings: Settings,
    get_api_client: Callable[[], StovePilotApiClient],
    cache: LastGoodCache,
) -> None:
    """Add the action endpoints (CSRF-protected; start/stop rate limiting is done by the API).

    :param app: UI application.
    :param settings: Application settings (timezone).
    :param get_api_client: Lazy getter of the API client.
    :param cache: Last-good cache of the live regions.
    """

    def respond(toasts: list[Toast], status: dict[str, Any] | None = None) -> Response:
        client, now = get_api_client(), local_now(settings.timezone)
        controls, down = cache.fetch(
            "controls", lambda: load_controls(client, now, _FORCE_LIVE_S, status)
        )
        flash = render_template("partials/flash.html", toasts=toasts)
        if controls is None:
            response = Response(flash)
            response.headers["HX-Reswap"] = "none"
        else:
            page = render_template("partials/controls.html", controls=controls, controls_stale=down)
            response = Response(page + flash)
        response.headers["HX-Trigger"] = _REFRESH_NOW_EVENT
        return response

    @app.post("/actions/start")
    def start() -> Response:
        try:
            outcome = get_api_client().start()
        except ApiProblem as problem:
            return respond([problem_toast(problem)])
        return respond([outcome_toast("start", outcome)], outcome.get("status"))

    @app.post("/actions/stop")
    def stop() -> Response:
        confirmed = request.form.get("confirm_ignition_stop") == "true"
        try:
            outcome = get_api_client().stop(confirm_ignition_stop=confirmed)
        except ApiProblem as problem:
            return respond([problem_toast(problem)])
        return respond([outcome_toast("stop", outcome)], outcome.get("status"))

    @app.post("/actions/setpoint")
    def setpoint() -> Response:
        try:
            value = float(request.form.get("value_c", ""))
        except ValueError:
            return respond([problem_toast(_INVALID_INPUT)])
        try:
            change = get_api_client().set_setpoint(value)
        except ApiProblem as problem:
            return respond([problem_toast(problem)])
        return respond([change_toast("setpoint", change)])

    @app.post("/actions/power")
    def power() -> Response:
        try:
            level = int(request.form.get("level", ""))
        except ValueError:
            return respond([problem_toast(_INVALID_INPUT)])
        try:
            change = get_api_client().set_power(level)
        except ApiProblem as problem:
            return respond([problem_toast(problem)])
        return respond([change_toast("power", change)])

    @app.post("/actions/suspend")
    def suspend() -> Response:
        try:
            until = _parse_until(request.form.get("until", ""))
        except ValueError:
            return respond([problem_toast(_INVALID_INPUT)])
        thermostat = request.form.get("thermostat_mode") == "on"
        try:
            outcome = get_api_client().suspend(until, thermostat)
        except ApiProblem as problem:
            return respond([problem_toast(problem)])
        start_outcome = outcome.get("start_outcome")
        status = start_outcome.get("status") if isinstance(start_outcome, dict) else None
        return respond(suspend_toasts(outcome), status)

    @app.post("/actions/vacation")
    def vacation() -> Response:
        try:
            until = _parse_until(request.form.get("until", ""))
        except ValueError:
            return respond([problem_toast(_INVALID_INPUT)])
        try:
            outcome = get_api_client().vacation(until)
        except ApiProblem as problem:
            return respond([problem_toast(problem)])
        start_outcome = outcome.get("start_outcome")
        status = start_outcome.get("status") if isinstance(start_outcome, dict) else None
        return respond(suspend_toasts(outcome, "vacation.done"), status)

    @app.post("/actions/resume")
    def resume() -> Response:
        try:
            get_api_client().resume()
        except ApiProblem as problem:
            return respond([problem_toast(problem)])
        key = "vacation.ended" if request.form.get("kind") == "vacation" else "suspension.resumed"
        return respond([Toast("success", t(key))])
