"""``GET /health``: liveness and poller state (exempt from the API key).

Route handlers are closures defined inside ``register_*`` functions, the documented pattern used
by every router module.
"""

from collections.abc import Callable
from typing import Any

from flask import Blueprint


def register_health_routes(bp: Blueprint, get_health: Callable[[], dict[str, Any]]) -> None:
    """Add the health route.

    :param bp: API blueprint.
    :param get_health: Returns the health payload (never touches the stove, never secrets).
    """

    @bp.get("/health")
    def health() -> dict[str, Any]:
        return get_health()
