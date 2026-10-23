"""``stove-pilot-api`` entrypoint: composes the singletons, starts the threads and serves the API."""

import logging
import sys
import threading
from collections.abc import Callable
from datetime import datetime
from functools import partial
from typing import Any

import waitress
from flask import Blueprint
from pydantic import ValidationError

from stove_pilot.common.exceptions import StovePilotError
from stove_pilot.common.logging import SecretRedactionFilter, configure_logging
from stove_pilot.common.settings import Settings, get_settings
from stove_pilot.core.safety.repository import SafetyRepository
from stove_pilot.core.stove.poller import StatusPoller
from stove_pilot.core.suspension.repository import SuspensionRepository
from stove_pilot.service.api.schemas.programming import suspension_dto
from stove_pilot.service.api.schemas.summary import ProgrammingSummaryDto, SuspensionDto
from stove_pilot.service.app import create_api_app
from stove_pilot.service.composition import (
    STALE_AFTER_POLL_FACTOR,
    Container,
    build_container,
    build_status_poller,
)
from stove_pilot.service.features import FEATURES, FeatureSpec
from stove_pilot.service.instance_lock import InstanceLock
from stove_pilot.service.signals import install_signal_handlers

_LOGGER = logging.getLogger(__name__)

_API_KEY_WARNING = (
    "API key disabled, API must stay bound to localhost or a private Docker network"
)
_SERVER_THREADS = 4
_JOIN_TIMEOUT_S = 10.0


def _health_snapshot(settings: Settings, poller: StatusPoller) -> dict[str, Any]:
    """Describe the poller state for ``/health`` without touching the stove or its lock.

    :param settings: Application settings.
    :param poller: Running status poller.
    :return: Small JSON-safe dict, never a secret.
    """
    last = poller.last_poll_at
    stale_after_s = STALE_AFTER_POLL_FACTOR * settings.poll_interval_s
    stale = last is None or (datetime.now(last.tzinfo) - last).total_seconds() > stale_after_s
    return {
        "status": "ok",
        "gateway_mode": settings.gateway,
        "gateway_reachable": not stale,
        "stale": stale,
        "last_poll_at": None if last is None else last.isoformat(),
    }


def _enabled_features(settings: Settings) -> list[FeatureSpec]:
    """List the registered features enabled in the settings.

    :param settings: Application settings.
    :return: Specs ordered by name.
    """
    return [FEATURES[name] for name in sorted(settings.features) if name in FEATURES]


def _require_safety(container: Container) -> SafetyRepository:
    """Return the safety repository (only called when the container has one).

    :param container: Composed repositories.
    :return: The safety repository.
    :raises RuntimeError: The container has no safety repository.
    """
    if container.safety is None:
        raise RuntimeError("No safety is composed")
    return container.safety


def _require_suspension(container: Container) -> SuspensionRepository:
    """Return the suspension repository (only called when the container has one).

    :param container: Composed repositories.
    :return: The suspension repository.
    :raises RuntimeError: The container has no suspension.
    """
    if container.suspension is None:
        raise RuntimeError("No suspension is composed")
    return container.suspension


def _suspension_summary(container: Container) -> SuspensionDto | None:
    """Describe the suspension for ``/summary``.

    :param container: Composed repositories.
    :return: The DTO while suspended, otherwise ``None``.
    """
    state = _require_suspension(container).get()
    return suspension_dto(state) if state.suspended else None


def _serve(settings: Settings, container: Container, stop_event: threading.Event) -> None:
    """Start the background threads, then serve the API until interrupted.

    :param settings: Application settings.
    :param container: Composed repositories.
    :param stop_event: Set by the caller on shutdown to stop every background loop.
    """
    poller = build_status_poller(settings, container, stop_event)
    threads = [threading.Thread(target=poller.run_forever, name="status-poller", daemon=True)]
    threads[0].start()
    registrars: list[Callable[[Blueprint], None]] = []
    providers: list[Callable[[datetime], ProgrammingSummaryDto]] = []
    no_suspension = container.suspension is None
    no_safety = container.safety is None
    try:
        for spec in _enabled_features(settings):
            registrars.append(partial(spec.register_api, container=container))
            if spec.summary is not None:
                providers.append(partial(spec.summary, container))
            if spec.start_threads is not None:
                threads.extend(spec.start_threads(container, stop_event))
        app = create_api_app(
            settings=settings,
            get_stove_repo=lambda: container.stove,
            get_history_repo=lambda: container.history,
            get_health=partial(_health_snapshot, settings, poller),
            feature_registrars=registrars,
            summary_providers=providers,
            get_suspension=None if no_suspension else partial(_suspension_summary, container),
            get_suspension_repo=None if no_suspension else partial(_require_suspension, container),
            get_safety_repo=None if no_safety else partial(_require_safety, container),
        )
        install_signal_handlers()
        waitress.serve(app, host=settings.api_host, port=settings.api_port, threads=_SERVER_THREADS)
    finally:
        stop_event.set()
        for thread in threads:
            thread.join(timeout=_JOIN_TIMEOUT_S)


def main() -> int:
    """Run the API server until SIGINT/SIGTERM.

    :return: Process exit code (1 on configuration errors or when another instance runs).
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

        code = enter("api")
        if code is not None:
            return code
    api_key = settings.api_key.get_secret_value()
    SecretRedactionFilter.register(settings.navel_security.get_secret_value())
    SecretRedactionFilter.register(api_key)
    configure_logging("INFO")
    lock = InstanceLock(settings.db_path.parent / "api.lock")
    container: Container | None = None
    stop_event = threading.Event()
    try:
        lock.acquire()
        if not api_key:
            _LOGGER.warning(_API_KEY_WARNING)
        factories = [spec.participant for spec in _enabled_features(settings) if spec.participant]
        container = build_container(settings, factories)
        _serve(settings, container, stop_event)
    except StovePilotError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    finally:
        if container is not None:
            container.close()
        lock.release()
    _LOGGER.info("API stopped")
    return 0


if __name__ == "__main__":
    sys.exit(main())
