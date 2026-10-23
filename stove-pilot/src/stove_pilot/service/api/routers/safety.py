"""Safety settings and rearm routes.

Route handlers are closures defined inside ``register_*`` functions (see ``health``).
"""

from collections.abc import Callable
from typing import Any

from flask import Blueprint

from stove_pilot.common.exceptions import ConfigurationError
from stove_pilot.core.safety.repository import SafetyRepository
from stove_pilot.service.api.errors import ApiProblemError
from stove_pilot.service.api.parsing import parse_body
from stove_pilot.service.api.schemas.common import dump
from stove_pilot.service.api.schemas.safety import SafetyDto, SafetyUpdateRequest, safety_config


def _unavailable(repo: SafetyRepository, exc: ConfigurationError) -> ApiProblemError:
    """Map a corrupt safety row to a problem response.

    :param repo: Safety facade that logs the failure once.
    :param exc: Decode failure. Its message is not logged.
    :return: HTTP 503 ``safety_unavailable``.
    """
    repo.note_unreadable(exc)
    return ApiProblemError(
        503,
        "safety_unavailable",
        "Safety unavailable",
        "The stored safety data cannot be read.",
    )


def register_safety_routes(bp: Blueprint, get_safety_repo: Callable[[], SafetyRepository]) -> None:
    """Add the safety routes.

    :param bp: API blueprint.
    :param get_safety_repo: Lazy getter of the safety repository.
    """

    @bp.get("/safety")
    def get_safety() -> dict[str, Any]:
        repo = get_safety_repo()
        try:
            report = repo.view()
        except ConfigurationError as exc:
            raise _unavailable(repo, exc) from None
        return dump(SafetyDto.from_domain(report))

    @bp.put("/safety")
    def put_safety() -> dict[str, Any]:
        body = parse_body(SafetyUpdateRequest)
        repo = get_safety_repo()
        try:
            report = repo.update(safety_config(body))
        except ConfigurationError as exc:
            raise _unavailable(repo, exc) from None
        return dump(SafetyDto.from_domain(report))

    @bp.post("/safety/rearm")
    def rearm_safety() -> dict[str, Any]:
        repo = get_safety_repo()
        try:
            report = repo.rearm()
        except ConfigurationError as exc:
            raise _unavailable(repo, exc) from None
        return dump(SafetyDto.from_domain(report))
