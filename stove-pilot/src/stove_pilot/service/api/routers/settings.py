"""Stove clock, settings (ENERGY SAVING, DISPLAY ECO) and maintenance routes.

Route handlers are closures defined inside ``register_*`` functions (see ``health``).
"""

from collections.abc import Callable
from typing import Any

from flask import Blueprint

from stove_pilot.core.stove.models import EventSource
from stove_pilot.core.stove.repository import StoveRepository
from stove_pilot.service.api.errors import ApiProblemError
from stove_pilot.service.api.parsing import parse_body
from stove_pilot.service.api.schemas.common import SettingChangeDto, dump
from stove_pilot.service.api.schemas.settings import (
    ClockAutoSyncRequest,
    ClockStatusDto,
    DisplayEcoRequest,
    EnergySavingRequest,
    MaintenanceDto,
    StoveSettingsDto,
)


def register_settings_routes(bp: Blueprint, get_stove_repo: Callable[[], StoveRepository]) -> None:
    """Add the clock, settings and maintenance routes.

    :param bp: API blueprint.
    :param get_stove_repo: Lazy getter of the stove repository.
    """

    @bp.get("/stove/clock")
    def clock() -> dict[str, Any]:
        return dump(ClockStatusDto.from_domain(get_stove_repo().clock_status()))

    @bp.post("/stove/clock/sync")
    def sync_clock() -> dict[str, Any]:
        status = get_stove_repo().sync_clock(force=True, source=EventSource.USER)
        return dump(ClockStatusDto.from_domain(status))

    @bp.put("/stove/clock/auto-sync")
    def set_clock_auto_sync() -> dict[str, Any]:
        body = parse_body(ClockAutoSyncRequest)
        status = get_stove_repo().set_clock_auto_sync(body.enabled, source=EventSource.USER)
        return dump(ClockStatusDto.from_domain(status))

    @bp.get("/stove/settings")
    def stove_settings() -> dict[str, Any]:
        return dump(StoveSettingsDto.from_domain(get_stove_repo().settings()))

    @bp.put("/stove/settings/energy-saving")
    def set_energy_saving() -> dict[str, Any]:
        body = parse_body(EnergySavingRequest)
        changed = get_stove_repo().set_energy_saving(
            stop_offset_c=body.stop_offset_c,
            start_offset_c=body.start_offset_c,
            source=EventSource.USER,
        )
        return dump(SettingChangeDto(changed=changed))

    @bp.put("/stove/settings/display-eco")
    def set_display_eco() -> dict[str, Any]:
        body = parse_body(DisplayEcoRequest)
        changed = get_stove_repo().set_display_eco(body.enabled, source=EventSource.USER)
        return dump(SettingChangeDto(changed=changed))

    @bp.get("/stove/maintenance")
    def maintenance() -> dict[str, Any]:
        info = get_stove_repo().maintenance()
        if info is None:
            raise ApiProblemError(
                404, "not_mapped", "Not mapped", "The profile maps no maintenance counters."
            )
        return dump(MaintenanceDto.from_domain(info))
