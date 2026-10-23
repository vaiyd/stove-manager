"""Securite card writes (CSRF-protected by the application, same as the other Reglages posts)."""

from collections.abc import Callable, Mapping
from dataclasses import replace
from datetime import datetime
from typing import Any, Final

from flask import Blueprint, Flask, Response, render_template, request

from stove_pilot.common.settings import Settings
from stove_pilot.service.ui.client import ApiProblem, StovePilotApiClient
from stove_pilot.service.ui.i18n import t
from stove_pilot.service.ui.regions import local_now
from stove_pilot.service.ui.routes.settings import fetch_resource, render_flash, toast_only
from stove_pilot.service.ui.safety_view import SafetyView
from stove_pilot.service.ui.settings_view import STATE_OK
from stove_pilot.service.ui.toasts import Toast, problem_toast

_UNPROCESSABLE: Final = 422
_TEMPLATE: Final = "partials/settings_safety.html"
_INT_FIELDS: Final = ("threshold_c", "max_attempts", "retry_interval_min")
_INVALID_INPUT: Final = ApiProblem(422, "validation_failed", "")


def _safety_body(form: Mapping[str, str]) -> dict[str, bool | int]:
    """Build the safety request from the form.

    :param form: Submitted fields (``enabled`` and the three whole numbers).
    :return: The API body.
    :raises ValueError: A number is missing or not a whole decimal integer.
    """
    body: dict[str, bool | int] = {"enabled": form.get("enabled") == "on"}
    for name in _INT_FIELDS:
        raw = (form.get(name) or "").strip()
        if not raw or not _decimal(raw):
            raise ValueError(name)
        body[name] = int(raw, 10)
    return body


def _decimal(raw: str) -> bool:
    """Tell whether ``raw`` is an optional sign and digits (no fraction, no exponent).

    :param raw: Stripped form value.
    :return: ``True`` when ``int(raw, 10)`` is the intended parse.
    """
    digits = raw[1:] if raw.startswith(("+", "-")) and len(raw) > 1 else raw
    return digits.isdecimal()


def _card(view: SafetyView, toasts: list[Toast]) -> Response:
    """Render the card plus the out-of-band toasts.

    :param view: Card view.
    :param toasts: Toasts to show.
    :return: The response.
    """
    html = render_template(_TEMPLATE, safety=view)
    return Response(html + render_flash(toasts))


def _load(client: StovePilotApiClient, now: datetime) -> SafetyView:
    """Read the safety card.

    :param client: API client.
    :param now: Local current time.
    :return: The view (error or absent when the read fails).
    """
    payload, state = fetch_resource(client, "/safety")
    return SafetyView.from_api(payload, now, state)


def _rejected(view: SafetyView, body: Mapping[str, bool | int]) -> SafetyView:
    """Keep the submitted numbers on a 422 and add the inline error.

    :param view: Card reloaded after the rejection (may be an error state).
    :param body: Values the user submitted.
    :return: A form view showing those values.
    """
    fields: dict[str, Any] = {
        "enabled": bool(body["enabled"]),
        "threshold_c": int(body["threshold_c"]),
        "max_attempts": int(body["max_attempts"]),
        "retry_interval_min": int(body["retry_interval_min"]),
        "error_text": t("settings.safety.rejected"),
    }
    if view.state != STATE_OK:
        return SafetyView(STATE_OK, **fields)
    return replace(view, **fields)


def register_safety_ui(
    app: Flask,
    settings: Settings,
    get_api_client: Callable[[], StovePilotApiClient],
) -> None:
    """Add the Securite save and rearm posts (CSRF-protected like the other settings posts).

    :param app: UI application.
    :param settings: Application settings (timezone).
    :param get_api_client: Lazy getter of the API client.
    """
    bp = Blueprint("safety_ui", __name__)

    @bp.post("/actions/settings/safety")
    def save() -> Response:
        try:
            body = _safety_body(request.form)
        except ValueError:
            return toast_only([problem_toast(_INVALID_INPUT)])
        client, now = get_api_client(), local_now(settings.timezone)
        try:
            payload, _ = client.put("/safety", body)
        except ApiProblem as problem:
            view = _load(client, now)
            if problem.status == _UNPROCESSABLE:
                view = _rejected(view, body)
            return _card(view, [problem_toast(problem)])
        return _card(
            SafetyView.from_api(payload, now),
            [Toast("success", t("settings.safety.saved"))],
        )

    @bp.post("/actions/settings/safety/rearm")
    def rearm() -> Response:
        client, now = get_api_client(), local_now(settings.timezone)
        try:
            payload = client.post("/safety/rearm")
        except ApiProblem as problem:
            return _card(_load(client, now), [problem_toast(problem)])
        return _card(
            SafetyView.from_api(payload, now),
            [Toast("success", t("settings.safety.rearmed"))],
        )

    app.register_blueprint(bp)
