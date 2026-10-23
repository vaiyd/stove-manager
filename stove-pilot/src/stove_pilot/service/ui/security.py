"""UI security: CSRF protection, session cookie flags, framing and content headers."""

import secrets
from typing import Final

from flask import Flask, Response, render_template, request
from flask_wtf.csrf import CSRFError, CSRFProtect

from stove_pilot.common.settings import Settings
from stove_pilot.service.ui.toasts import csrf_toast

WIDGET_PATH: Final = "/widget"


def register_security(app: Flask, settings: Settings) -> CSRFProtect:
    """Configure the secret key, CSRF and the response headers.

    :param app: UI application.
    :param settings: Application settings.
    :return: The CSRF protection (widget routes are exempted by their registrar).
    """
    # Reason: without a configured key, tokens are valid for the process lifetime only.
    app.config["SECRET_KEY"] = settings.ui_secret_key.get_secret_value() or secrets.token_hex(32)
    app.config["SESSION_COOKIE_SAMESITE"] = "Strict"
    app.config["SESSION_COOKIE_HTTPONLY"] = True
    app.config["WTF_CSRF_TIME_LIMIT"] = None
    csrf = CSRFProtect(app)
    frame_ancestors = f"frame-ancestors {settings.ui_frame_ancestors}"

    @app.errorhandler(CSRFError)
    def csrf_failed(error: CSRFError) -> Response | tuple[str, int]:
        if request.headers.get("HX-Request"):
            body = render_template("partials/flash.html", toasts=[csrf_toast()])
            response = Response(body)
            response.headers["HX-Reswap"] = "none"
            return response
        return "Bad request", 400

    @app.after_request
    def set_headers(response: Response) -> Response:
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "same-origin"
        if request.path == WIDGET_PATH:
            response.headers["Content-Security-Policy"] = frame_ancestors
            response.headers.pop("X-Frame-Options", None)
        else:
            response.headers["X-Frame-Options"] = "SAMEORIGIN"
        if request.endpoint != "static":
            response.headers["Cache-Control"] = "no-store"
        return response

    return csrf
