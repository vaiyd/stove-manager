"""Programming suspension routes: state, suspend, vacation and resume (registered when a participant exists).

Route handlers are closures defined inside ``register_*`` functions (see ``health``).
"""

from collections.abc import Callable
from datetime import datetime, tzinfo
from typing import Any

from flask import Blueprint

from stove_pilot.core.stove.repository import StoveRepository
from stove_pilot.core.suspension.repository import SuspensionRepository
from stove_pilot.service.api.parsing import parse_body
from stove_pilot.service.api.schemas.common import dump
from stove_pilot.service.api.schemas.programming import (
    SuspendOutcomeDto,
    SuspendRequest,
    VacationRequest,
    suspension_dto,
)


def _localize(until: datetime | None, tz: tzinfo) -> datetime | None:
    """Read a naive ``until`` in the configured timezone.

    :param until: Requested time, or ``None``.
    :param tz: Local timezone applied to a naive value.
    :return: The aware time, or ``None``.
    """
    if until is not None and until.tzinfo is None:
        return until.replace(tzinfo=tz)
    return until


def register_programming_routes(
    bp: Blueprint,
    get_suspension_repo: Callable[[], SuspensionRepository],
    get_stove_repo: Callable[[], StoveRepository],
    tz: tzinfo,
) -> None:
    """Add the programming suspension and vacation routes.

    :param bp: API blueprint.
    :param get_suspension_repo: Lazy getter of the suspension repository.
    :param get_stove_repo: Lazy getter of the stove repository.
    :param tz: Local timezone applied to a naive ``until``.
    """

    @bp.get("/programming")
    def programming() -> dict[str, Any]:
        return dump(suspension_dto(get_suspension_repo().get()))

    @bp.post("/programming/suspend")
    def suspend() -> dict[str, Any]:
        body = parse_body(SuspendRequest, optional=True)
        outcome = get_suspension_repo().suspend(
            until=_localize(body.until, tz), thermostat_mode=body.thermostat_mode
        )
        return dump(SuspendOutcomeDto.from_domain(outcome, get_stove_repo()))

    @bp.post("/programming/vacation")
    def vacation() -> dict[str, Any]:
        body = parse_body(VacationRequest, optional=True)
        outcome = get_suspension_repo().start_vacation(until=_localize(body.until, tz))
        return dump(SuspendOutcomeDto.from_domain(outcome, get_stove_repo()))

    @bp.post("/programming/resume")
    def resume() -> dict[str, Any]:
        return dump(suspension_dto(get_suspension_repo().resume()))
