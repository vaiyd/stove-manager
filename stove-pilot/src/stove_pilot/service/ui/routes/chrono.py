"""Chrono poele page: editor, block toggles and the fingerprint probe.

The endpoint paths of the chrono API live here; the generic API client never names them.
"""

import json
import logging
from collections.abc import Callable
from typing import Any, Final

from flask import Blueprint, Flask, Response, abort, jsonify, render_template, request

from stove_pilot.service.ui.chrono_form import build_config_body
from stove_pilot.service.ui.chrono_view import (
    BLOCK_ORDER,
    ChronoView,
    issue_text_fr,
    js_texts,
)
from stove_pilot.service.ui.client import FRESH_READ_TIMEOUT_S, ApiProblem, StovePilotApiClient
from stove_pilot.service.ui.i18n import t
from stove_pilot.service.ui.routes.params import query_max_age
from stove_pilot.service.ui.toasts import Toast, problem_toast

_LOGGER = logging.getLogger(__name__)
_NOT_FOUND: Final = 404
_VERSION_CONFLICT: Final = 412
_VALIDATION_FAILED: Final = 422
_SUSPENDED_REASON: Final = "programming_suspended"
# Reason: a chrono read takes 2-3 s of the slow module; a background tick never asks fresher.
PROBE_MIN_AGE_S: Final = 60.0
_INVALID_INPUT: Final = ApiProblem(_VALIDATION_FAILED, "validation_failed", "")


def _fetch_chrono(client: StovePilotApiClient) -> tuple[dict[str, Any] | None, str | None]:
    """Read the chrono (always live: an editor must show the stove as it is now).

    :param client: API client.
    :return: Payload and ETag, or ``(None, None)`` when the API failed.
    """
    try:
        return client.get("/chrono", timeout_s=FRESH_READ_TIMEOUT_S)
    except ApiProblem as problem:
        _LOGGER.warning("Chrono unavailable (%s %s)", problem.status, problem.code)
        return None, None


def _is_suspended(client: StovePilotApiClient) -> bool:
    """Tell whether programming is suspended (a missing or failed read means not suspended).

    :param client: API client.
    :return: ``True`` when suspended.
    """
    try:
        return bool(client.programming().get("suspended"))
    except ApiProblem as problem:
        if problem.status != _NOT_FOUND:
            _LOGGER.warning("Suspension state unavailable (%s %s)", problem.status, problem.code)
        return False


def _editor_context(
    client: StovePilotApiClient, payload: dict[str, Any] | None, etag: str | None
) -> dict[str, Any]:
    """Build the template context of the editor.

    :param client: API client.
    :param payload: Chrono payload, or ``None`` when unavailable.
    :param etag: ``ETag`` of the payload.
    :return: Template variables.
    """
    view = None if payload is None else ChronoView.from_api(payload, etag, _is_suspended(client))
    return {"view": view, "texts": js_texts()}


def _render_editor(
    client: StovePilotApiClient, payload: dict[str, Any] | None, etag: str | None
) -> str:
    """Render the swappable editor root.

    :param client: API client.
    :param payload: Chrono payload, or ``None`` when unavailable.
    :param etag: ``ETag`` of the payload.
    :return: HTML fragment.
    """
    context = _editor_context(client, payload, etag)
    return render_template("partials/chrono_editor.html", **context)


def _flash(toasts: list[Toast]) -> str:
    """Render the out-of-band toasts.

    :param toasts: Toasts to show.
    :return: HTML fragment.
    """
    return render_template("partials/flash.html", toasts=toasts)


def _action_response(toasts: list[Toast], trigger: dict[str, Any] | None = None) -> Response:
    """Answer an action with toasts only (no swap), optionally firing client events.

    :param toasts: Toasts to show.
    :param trigger: Events for ``HX-Trigger`` (name to detail).
    :return: The response.
    """
    response = Response(_flash(toasts))
    response.headers["HX-Reswap"] = "none"
    if trigger:
        response.headers["HX-Trigger"] = json.dumps(trigger)
    return response


def register_chrono_ui(app: Flask, get_api_client: Callable[[], StovePilotApiClient]) -> None:
    """Add the chrono page and its action endpoints (CSRF-protected by the app).

    :param app: UI application.
    :param get_api_client: Lazy getter of the API client.
    """
    bp = Blueprint("chrono", __name__)

    @bp.get("/chrono")
    def index() -> str:
        client = get_api_client()
        payload, etag = _fetch_chrono(client)
        return render_template("chrono.html", **_editor_context(client, payload, etag))

    @bp.get("/partials/chrono/editor")
    def editor_partial() -> str:
        client = get_api_client()
        payload, etag = _fetch_chrono(client)
        return _render_editor(client, payload, etag)

    @bp.get("/partials/chrono/fingerprint")
    def fingerprint_partial() -> Response | tuple[Response, int]:
        max_age_s = query_max_age()
        if max_age_s:
            # Reason: background ticks must not read the stove more than about once a minute.
            max_age_s = max(max_age_s, PROBE_MIN_AGE_S)
        params = None if max_age_s is None else {"max_age_s": f"{max_age_s:g}"}
        try:
            payload, _ = get_api_client().get(
                "/chrono/fingerprint", params, timeout_s=FRESH_READ_TIMEOUT_S
            )
        except ApiProblem as problem:
            return jsonify(error=problem.code), 503
        return jsonify(fingerprint=str(payload.get("fingerprint", "")))

    @bp.post("/actions/chrono/blocks/<kind>/state")
    def toggle_block(kind: str) -> Response:
        if kind not in BLOCK_ORDER:
            abort(_NOT_FOUND)
        enabled = request.form.get(f"toggle.{kind}") == "true"
        client = get_api_client()
        try:
            _, etag = client.put(f"/chrono/blocks/{kind}/state", {"enabled": enabled})
        except ApiProblem as problem:
            if problem.outcome_unknown:
                return _reload_after_unknown(client, problem)
            rejected = {"kind": kind, "suspended": problem.reason == _SUSPENDED_REASON}
            return _action_response([problem_toast(problem)], {"chrono-toggle-rejected": rejected})
        key = "chrono.block_enabled" if enabled else "chrono.block_disabled"
        changed = {"kind": kind, "enabled": enabled, "etag": etag}
        return _action_response([Toast("success", t(key))], {"chrono-etag": changed})

    @bp.post("/actions/chrono/save")
    def save() -> Response:
        try:
            body = build_config_body(request.form)
        except ValueError:
            return _action_response([problem_toast(_INVALID_INPUT)])
        client = get_api_client()
        try:
            payload, etag = client.put("/chrono", body, request.form.get("etag") or None)
        except ApiProblem as problem:
            if problem.outcome_unknown:
                return _reload_after_unknown(client, problem)
            return _save_failure(problem)
        return _swap_editor(client, payload, etag, [Toast("success", t("chrono.saved"))])

    app.register_blueprint(bp)


def _swap_editor(
    client: StovePilotApiClient, payload: dict[str, Any], etag: str | None, toasts: list[Toast]
) -> Response:
    """Answer with the editor re-rendered from a payload, plus toasts.

    :param client: API client.
    :param payload: Chrono payload.
    :param etag: ``ETag`` of the payload.
    :param toasts: Toasts to show.
    :return: The response retargeted on the editor.
    """
    response = Response(_render_editor(client, payload, etag) + _flash(toasts))
    response.headers["HX-Retarget"] = "#chrono-editor"
    response.headers["HX-Reswap"] = "outerHTML"
    return response


def _reload_after_unknown(client: StovePilotApiClient, problem: ApiProblem) -> Response:
    """Answer a write with an unknown outcome: warn and show the chrono as the stove holds it.

    The read waits for the API to finish the write; when it fails the editor is left as is.

    :param client: API client.
    :param problem: The unknown-outcome problem.
    :return: The response (editor swap with the fresh state, or a toast only).
    """
    toasts = [problem_toast(problem)]
    payload, etag = _fetch_chrono(client)
    if payload is None:
        return _action_response(toasts)
    return _swap_editor(client, payload, etag, toasts)


def _save_failure(problem: ApiProblem) -> Response:
    """Answer a rejected save: inline issues, the stale-version dialog, or a toast.

    :param problem: The API problem.
    :return: The response (swapped into ``#chrono-feedback`` or a toast only).
    """
    if problem.status == _VERSION_CONFLICT:
        page = render_template("partials/chrono_issues.html", issues=[], conflict=True)
        response = Response(page)
        # Reason: the dialog is part of the swapped fragment, so open it after the swap.
        response.headers["HX-Trigger-After-Swap"] = json.dumps({"chrono-conflict": {}})
        return response
    if problem.status == _VALIDATION_FAILED and problem.issues:
        issues = [issue_text_fr(issue) for issue in problem.issues]
        page = render_template("partials/chrono_issues.html", issues=issues, conflict=False)
        return Response(page)
    return _action_response([problem_toast(problem)])
