"""Stove status, start/stop commands, setpoint and power routes.

Route handlers are closures defined inside ``register_*`` functions (see ``health``).
"""

from collections.abc import Callable
from typing import Any

from flask import Blueprint

from stove_pilot.core.stove.models import EventSource
from stove_pilot.core.stove.repository import StoveRepository
from stove_pilot.service.api.parsing import parse_body, query_float
from stove_pilot.service.api.ratelimit import SlidingWindowLimiter
from stove_pilot.service.api.schemas.common import SettingChangeDto, dump
from stove_pilot.service.api.schemas.stove import (
    CommandOutcomeDto,
    PowerRequest,
    SetpointRequest,
    StopRequest,
    StoveStatusDto,
)


def register_stove_routes(
    bp: Blueprint,
    get_stove_repo: Callable[[], StoveRepository],
    limiter: SlidingWindowLimiter,
) -> None:
    """Add the stove routes.

    :param bp: API blueprint.
    :param get_stove_repo: Lazy getter of the stove repository.
    :param limiter: Rate limiter applied to the start and stop commands.
    """

    @bp.get("/stove/status")
    def status() -> dict[str, Any]:
        return dump(StoveStatusDto.load(get_stove_repo(), query_float("max_age_s", minimum=0)))

    @bp.post("/stove/commands/start")
    def start() -> dict[str, Any]:
        limiter.hit("start")
        repository = get_stove_repo()
        return dump(
            CommandOutcomeDto.from_domain(repository.start(source=EventSource.USER), repository)
        )

    @bp.post("/stove/commands/stop")
    def stop() -> dict[str, Any]:
        limiter.hit("stop")
        body = parse_body(StopRequest, optional=True)
        repository = get_stove_repo()
        outcome = repository.stop(
            source=EventSource.USER, confirm_ignition_stop=body.confirm_ignition_stop
        )
        return dump(CommandOutcomeDto.from_domain(outcome, repository))

    @bp.put("/stove/setpoint")
    def set_setpoint() -> dict[str, Any]:
        body = parse_body(SetpointRequest)
        changed = get_stove_repo().set_setpoint(body.value_c, source=EventSource.USER)
        return dump(SettingChangeDto(changed=changed))

    @bp.put("/stove/power")
    def set_power() -> dict[str, Any]:
        body = parse_body(PowerRequest)
        changed = get_stove_repo().set_power(body.level, source=EventSource.USER)
        return dump(SettingChangeDto(changed=changed))
