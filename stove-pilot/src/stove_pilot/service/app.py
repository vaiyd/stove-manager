"""Application factories: the JSON API and the web UI, both wired around lazy getters."""

from collections.abc import Callable, Sequence
from datetime import datetime
from typing import Any, Final
from zoneinfo import ZoneInfo

from flask import Blueprint, Flask

from stove_pilot.common.settings import Settings
from stove_pilot.core.history.repository import HistoryRepository
from stove_pilot.core.safety.repository import SafetyRepository
from stove_pilot.core.stove.repository import StoveRepository
from stove_pilot.core.suspension.repository import SuspensionRepository
from stove_pilot.service.api.auth import register_api_key_check
from stove_pilot.service.api.errors import register_error_handlers
from stove_pilot.service.api.ratelimit import SlidingWindowLimiter
from stove_pilot.service.api.routers.features import register_features_routes
from stove_pilot.service.api.routers.health import register_health_routes
from stove_pilot.service.api.routers.history import register_history_routes
from stove_pilot.service.api.routers.programming import register_programming_routes
from stove_pilot.service.api.routers.safety import register_safety_routes
from stove_pilot.service.api.routers.settings import register_settings_routes
from stove_pilot.service.api.routers.stove import register_stove_routes
from stove_pilot.service.api.routers.summary import register_summary_routes
from stove_pilot.service.api.schemas.summary import ProgrammingSummaryDto, SuspensionDto
from stove_pilot.service.features import FEATURES
from stove_pilot.service.ui.client import StovePilotApiClient
from stove_pilot.service.ui.context import register_context
from stove_pilot.service.ui.live import LastGoodCache
from stove_pilot.service.ui.routes.commands import register_command_routes
from stove_pilot.service.ui.routes.dashboard import register_dashboard_routes
from stove_pilot.service.ui.routes.history import register_history_ui
from stove_pilot.service.ui.routes.pwa import register_pwa_routes
from stove_pilot.service.ui.routes.safety import register_safety_ui
from stove_pilot.service.ui.routes.settings import register_settings_ui
from stove_pilot.service.ui.routes.widget import register_widget_routes
from stove_pilot.service.ui.security import register_security

API_PREFIX: Final = "/api/v1"


def create_api_app(
    *,
    settings: Settings,
    get_stove_repo: Callable[[], StoveRepository],
    get_history_repo: Callable[[], HistoryRepository],
    get_health: Callable[[], dict[str, Any]],
    feature_registrars: Sequence[Callable[[Blueprint], None]],
    summary_providers: Sequence[Callable[[datetime], ProgrammingSummaryDto]],
    get_suspension: Callable[[], SuspensionDto | None] | None = None,
    get_suspension_repo: Callable[[], SuspensionRepository] | None = None,
    get_safety_repo: Callable[[], SafetyRepository] | None = None,
) -> Flask:
    """Build the JSON API application.

    :param settings: Application settings.
    :param get_stove_repo: Lazy getter of the stove repository.
    :param get_history_repo: Lazy getter of the history repository.
    :param get_health: Returns the ``/health`` payload.
    :param feature_registrars: One callable per enabled feature, adding its routes to the blueprint.
    :param summary_providers: One callable per enabled feature, returning its summary line.
    :param get_suspension: Optional suspension getter for ``/summary``.
    :param get_suspension_repo: Lazy getter of the suspension repository (the programming routes
        are registered only when it is given, that is when a participant exists).
    :param get_safety_repo: Lazy getter of the safety repository (routes omitted when ``None``).
    :return: The Flask application (served by Waitress).
    """
    app = Flask("stove_pilot.api")
    app.json.sort_keys = False
    tz = ZoneInfo(settings.timezone)
    bp = Blueprint("api", __name__, url_prefix=API_PREFIX)
    register_api_key_check(bp, settings.api_key.get_secret_value())
    register_health_routes(bp, get_health)
    register_features_routes(bp, settings, tuple(FEATURES))
    register_stove_routes(bp, get_stove_repo, SlidingWindowLimiter())
    register_settings_routes(bp, get_stove_repo)
    register_summary_routes(
        bp,
        settings,
        get_stove_repo,
        summary_providers,
        get_suspension,
        tz,
        get_history_repo,
        get_safety_repo,
    )
    register_history_routes(bp, get_history_repo, tz)
    if get_suspension_repo is not None:
        register_programming_routes(bp, get_suspension_repo, get_stove_repo, tz)
    if get_safety_repo is not None:
        register_safety_routes(bp, get_safety_repo)
    for registrar in feature_registrars:
        registrar(bp)
    app.register_blueprint(bp)
    register_error_handlers(app)
    return app


def create_ui_app(
    *,
    settings: Settings,
    get_api_client: Callable[[], StovePilotApiClient],
    ui_registrars: Sequence[Callable[[Flask, Callable[[], StovePilotApiClient]], None]],
) -> Flask:
    """Build the web UI application (no login: the LAN is the trust boundary).

    :param settings: Application settings.
    :param get_api_client: Lazy getter of the API client (the UI reaches the backend over HTTP).
    :param ui_registrars: One callable per enabled feature, adding its pages to the app (it gets
        the app and the lazy API client getter).
    :return: The Flask application (served by Waitress).
    """
    app = Flask(
        "stove_pilot.service.ui", static_folder="static", template_folder="templates"
    )
    if settings.dev:
        # Reason: dev-only UI tweaks live next to the supervisor, so they leave with it.
        from stove_pilot.service.dev_server import apply_ui_dev

        apply_ui_dev(app)
    csrf = register_security(app, settings)
    register_context(app, settings, get_api_client)
    cache = LastGoodCache()
    register_dashboard_routes(app, settings, get_api_client, cache)
    register_command_routes(app, settings, get_api_client, cache)
    register_history_ui(app, settings, get_api_client, cache)
    register_settings_ui(app, settings, get_api_client, cache)
    register_safety_ui(app, settings, get_api_client)
    register_widget_routes(app, settings, get_api_client, cache, csrf)
    register_pwa_routes(app)
    for registrar in ui_registrars:
        registrar(app, get_api_client)
    return app
