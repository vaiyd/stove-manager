"""``stove-pilot-ui`` entrypoint: composes the API client and serves the web UI.

The UI keeps no database, so it takes no instance lock (a second instance fails to bind the port).
"""

import logging
import sys
from collections.abc import Callable

import waitress
from flask import Flask
from pydantic import ValidationError

from stove_pilot.common.logging import SecretRedactionFilter, configure_logging
from stove_pilot.common.settings import Settings, get_settings
from stove_pilot.service.app import create_ui_app
from stove_pilot.service.features import FEATURES
from stove_pilot.service.signals import install_signal_handlers
from stove_pilot.service.ui.client import StovePilotApiClient

_LOGGER = logging.getLogger(__name__)
_SERVER_THREADS = 8


def _ui_registrars(
    settings: Settings,
) -> list[Callable[[Flask, Callable[[], StovePilotApiClient]], None]]:
    """List the page registrars of the enabled features.

    :param settings: Application settings.
    :return: One registrar per enabled feature that has UI pages.
    """
    specs = [FEATURES[name] for name in sorted(settings.features) if name in FEATURES]
    return [spec.register_ui for spec in specs if spec.register_ui is not None]


def main() -> int:
    """Run the UI server until SIGINT/SIGTERM.

    :return: Process exit code (1 on configuration errors).
    """
    try:
        settings = get_settings()
    except ValidationError as exc:
        fields = ", ".join(".".join(str(part) for part in err["loc"]) for err in exc.errors())
        print(f"error: invalid configuration ({fields})", file=sys.stderr)
        return 1
    if settings.dev:
        # Reason: the dev supervisor stays out of the production import graph.
        from stove_pilot.service.dev_server import enter

        code = enter("ui")
        if code is not None:
            return code
    api_key = settings.api_key.get_secret_value()
    SecretRedactionFilter.register(api_key)
    SecretRedactionFilter.register(settings.ui_secret_key.get_secret_value())
    configure_logging("INFO")
    if not settings.ui_secret_key.get_secret_value():
        _LOGGER.info("UI secret key not set, a random CSRF key is generated for this process")
    client = StovePilotApiClient(settings.ui_api_base_url, api_key)
    try:
        app = create_ui_app(
            settings=settings,
            get_api_client=lambda: client,
            ui_registrars=_ui_registrars(settings),
        )
        install_signal_handlers()
        waitress.serve(app, host=settings.ui_host, port=settings.ui_port, threads=_SERVER_THREADS)
    finally:
        client.close()
    _LOGGER.info("UI stopped")
    return 0


if __name__ == "__main__":
    sys.exit(main())
