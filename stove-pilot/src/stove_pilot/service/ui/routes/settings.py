"""Reglages page: stove settings, safety, clock, maintenance (common base, no optional feature)."""

import logging
from collections.abc import Callable, Mapping
from dataclasses import replace
from datetime import datetime
from typing import Any, Final

from flask import Blueprint, Flask, Response, render_template, request

from stove_pilot.common.settings import Settings
from stove_pilot.service.ui.client import FRESH_READ_TIMEOUT_S, ApiProblem, StovePilotApiClient
from stove_pilot.service.ui.i18n import t
from stove_pilot.service.ui.live import LastGoodCache
from stove_pilot.service.ui.regions import local_now
from stove_pilot.service.ui.safety_view import SafetyView
from stove_pilot.service.ui.settings_view import (
    STATE_ABSENT,
    STATE_ERROR,
    STATE_OK,
    ClockCardView,
    DisplayEcoView,
    EnergyView,
    MaintenanceView,
)
from stove_pilot.service.ui.toasts import Toast, problem_toast

_LOGGER = logging.getLogger(__name__)
_NOT_FOUND: Final = 404
_OFF: Final = "off"
_CLOCK_CACHE_KEY: Final = "settings.clock"
_INVALID_INPUT: Final = ApiProblem(422, "validation_failed", "")


def fetch_resource(client: StovePilotApiClient, path: str) -> tuple[dict[str, Any] | None, str]:
    """Read a resource, mapping a missing one and a failure to a block state.

    :param client: API client.
    :param path: Path below ``/api/v1``.
    :return: Payload and block state (``ok``, ``absent`` on 404, ``error`` otherwise).
    """
    try:
        return client.get(path, timeout_s=FRESH_READ_TIMEOUT_S)[0], STATE_OK
    except ApiProblem as problem:
        if problem.status == _NOT_FOUND:
            return None, STATE_ABSENT
        _LOGGER.warning("Settings read %s failed (%s %s)", path, problem.status, problem.code)
        return None, STATE_ERROR


def _load_settings(client: StovePilotApiClient) -> tuple[EnergyView, DisplayEcoView]:
    """Read the energy saving and display eco settings.

    :param client: API client.
    :return: The two views.
    """
    payload, state = fetch_resource(client, "/stove/settings")
    body = payload or {}
    energy = EnergyView.from_api(body.get("energy_saving"), state)
    if energy.state == STATE_OK and _vacation_active(client):
        energy = replace(energy, locked=True)
    return energy, DisplayEcoView.from_api(body.get("display_eco"), state)


def _vacation_active(client: StovePilotApiClient) -> bool:
    """Tell whether a vacation is running (a failure counts as no: the API still guards writes).

    :param client: API client.
    :return: ``True`` while the vacation mode locks the ENERGY SAVING registers.
    """
    try:
        suspension = client.programming()
    except ApiProblem:
        return False
    return bool(suspension.get("suspended")) and suspension.get("kind") == "vacation"


def _load_clock(client: StovePilotApiClient, now: datetime) -> ClockCardView:
    """Read the stove clock (raises when the API fails, so the last good card is kept).

    :param client: API client.
    :param now: Local current time.
    :return: The view (absent on 404).
    :raises ApiProblem: The API failed.
    """
    try:
        payload = client.get("/stove/clock", timeout_s=FRESH_READ_TIMEOUT_S)[0]
    except ApiProblem as problem:
        if problem.status != _NOT_FOUND:
            raise
        return ClockCardView(STATE_ABSENT)
    return ClockCardView.from_api(payload, STATE_OK, now)


def _energy_body(form: Mapping[str, str]) -> dict[str, int | None]:
    """Build the energy saving request from the form.

    :param form: Submitted fields (``enabled``, ``stop``, ``start``).
    :return: The API body (``None`` stop offset means OFF).
    :raises ValueError: A field is not a number.
    """
    stop_raw = form.get("stop", _OFF)
    stop = int(stop_raw) if form.get("enabled") == "on" and stop_raw != _OFF else None
    return {"stop_offset_c": stop, "start_offset_c": int(form.get("start", ""))}


def render_flash(toasts: list[Toast]) -> str:
    """Render the out-of-band toasts.

    :param toasts: Toasts to show.
    :return: HTML fragment.
    """
    return render_template("partials/flash.html", toasts=toasts)


def toast_only(toasts: list[Toast]) -> Response:
    """Answer with toasts and no swap.

    :param toasts: Toasts to show.
    :return: The response.
    """
    response = Response(render_flash(toasts))
    response.headers["HX-Reswap"] = "none"
    return response


def _saved_toast(key: str, change: dict[str, Any]) -> Toast:
    """Describe a settings write outcome.

    :param key: Catalog key of the success text.
    :param change: ``SettingChangeDto`` payload.
    :return: The toast.
    """
    if change.get("changed"):
        return Toast("success", t(key))
    return Toast("info", t("controls.unchanged"))


def register_settings_ui(
    app: Flask,
    settings: Settings,
    get_api_client: Callable[[], StovePilotApiClient],
    cache: LastGoodCache,
) -> None:
    """Add the Reglages page, its clock partial and its write endpoints (CSRF-protected).

    :param app: UI application.
    :param settings: Application settings (timezone).
    :param get_api_client: Lazy getter of the API client.
    :param cache: Last-good cache of the live regions.
    """
    bp = Blueprint("settings", __name__)

    def clock_card(client: StovePilotApiClient, now: datetime) -> str:
        clock, down = cache.fetch(_CLOCK_CACHE_KEY, lambda: _load_clock(client, now))
        return render_template("partials/settings_clock.html", clock=clock, clock_stale=down)

    def settings_card(template: str, view_name: str, toasts: list[Toast]) -> Response:
        # Reason: the card is rebuilt from the stove, so a rejected or unknown
        # write shows the truth.
        energy, display = _load_settings(get_api_client())
        if STATE_ERROR in (energy.state, display.state):
            return toast_only(toasts)
        views = {"energy": energy, "display": display}
        return Response(render_template(template, **{view_name: views[view_name]}) + render_flash(toasts))

    @bp.get("/settings")
    def index() -> str:
        client, now = get_api_client(), local_now(settings.timezone)
        energy, display = _load_settings(client)
        maintenance_payload, maintenance_state = fetch_resource(client, "/stove/maintenance")
        safety_payload, safety_state = fetch_resource(client, "/safety")
        return render_template(
            "settings.html",
            energy=energy,
            display=display,
            safety=SafetyView.from_api(safety_payload, now, safety_state),
            maintenance=MaintenanceView.from_api(maintenance_payload, maintenance_state),
            clock_html=clock_card(client, now),
        )

    @bp.get("/partials/settings/clock")
    def clock_partial() -> str:
        return clock_card(get_api_client(), local_now(settings.timezone))

    @bp.post("/actions/settings/energy-saving")
    def energy_saving() -> Response:
        try:
            body = _energy_body(request.form)
        except ValueError:
            return toast_only([problem_toast(_INVALID_INPUT)])
        try:
            change, _ = get_api_client().put("/stove/settings/energy-saving", body)
        except ApiProblem as problem:
            toasts = [problem_toast(problem)]
            return settings_card("partials/settings_energy.html", "energy", toasts)
        toast = _saved_toast("settings.energy.saved", change)
        return settings_card("partials/settings_energy.html", "energy", [toast])

    @bp.post("/actions/settings/display-eco")
    def display_eco() -> Response:
        body = {"enabled": request.form.get("enabled") == "on"}
        try:
            change, _ = get_api_client().put("/stove/settings/display-eco", body)
        except ApiProblem as problem:
            toasts = [problem_toast(problem)]
            return settings_card("partials/settings_display.html", "display", toasts)
        toast = _saved_toast("settings.display.saved", change)
        return settings_card("partials/settings_display.html", "display", [toast])

    @bp.post("/actions/settings/clock-sync")
    def clock_sync() -> Response:
        client, now = get_api_client(), local_now(settings.timezone)
        try:
            payload = client.post("/stove/clock/sync")
        except ApiProblem as problem:
            toasts = [problem_toast(problem)]
            if not problem.outcome_unknown:
                return toast_only(toasts)
            return Response(clock_card(client, now) + render_flash(toasts))
        view = ClockCardView.from_api(payload, STATE_OK, now)
        cache.fetch(_CLOCK_CACHE_KEY, lambda: view)
        card = render_template("partials/settings_clock.html", clock=view, clock_stale=False)
        return Response(card + render_flash([Toast("success", t("settings.clock.synced"))]))

    @bp.post("/actions/settings/clock-auto-sync")
    def clock_auto_sync() -> Response:
        client, now = get_api_client(), local_now(settings.timezone)
        enabled = request.form.get("enabled") == "on"
        try:
            payload, _ = client.put("/stove/clock/auto-sync", {"enabled": enabled})
        except ApiProblem as problem:
            return Response(clock_card(client, now) + render_flash([problem_toast(problem)]))
        view = ClockCardView.from_api(payload, STATE_OK, now)
        cache.fetch(_CLOCK_CACHE_KEY, lambda: view)
        card = render_template("partials/settings_clock.html", clock=view, clock_stale=False)
        key = "settings.clock.auto_saved_on" if view.auto_sync else "settings.clock.auto_saved_off"
        return Response(card + render_flash([Toast("success", t(key))]))

    app.register_blueprint(bp)
