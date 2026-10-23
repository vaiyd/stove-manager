"""Clock, settings and maintenance DTOs."""

from datetime import datetime
from typing import Self

from pydantic import BaseModel, StrictBool

from stove_pilot.core.stove.models import ClockStatus, MaintenanceInfo, StoveSettings
from stove_pilot.service.api.schemas.common import RequestModel


class ClockStatusDto(BaseModel):
    """Stove clock compared with the server clock (``stove_time`` is naive local time)."""

    stove_time: datetime
    server_time: datetime
    drift_s: float
    auto_sync: bool
    last_sync_at: datetime | None
    drift_tolerance_s: float
    auto_sync_writable: bool

    @classmethod
    def from_domain(cls, clock: ClockStatus) -> Self:
        """Map a clock status.

        :param clock: Domain clock status.
        :return: The DTO.
        """
        return cls(
            stove_time=clock.stove_time,
            server_time=clock.server_time,
            drift_s=clock.drift_s,
            auto_sync=clock.auto_sync,
            last_sync_at=clock.last_sync_at,
            drift_tolerance_s=clock.drift_tolerance_s,
            auto_sync_writable=clock.auto_sync_writable,
        )


class EnergySavingSettingDto(BaseModel):
    """ENERGY SAVING offsets with ``writable`` (both offset registers can be written now)."""

    enabled: bool
    stop_offset_c: int | None
    start_offset_c: int | None
    writable: bool


class DisplayEcoSettingDto(BaseModel):
    """DISPLAY ECO value with ``writable``."""

    enabled: bool
    writable: bool


class StoveSettingsDto(BaseModel):
    """Adjustable stove settings (``null`` while the registers are not mapped)."""

    energy_saving: EnergySavingSettingDto | None
    display_eco: DisplayEcoSettingDto | None

    @classmethod
    def from_domain(cls, settings: StoveSettings) -> Self:
        """Map the stove settings.

        :param settings: Domain settings.
        :return: The DTO.
        """
        energy = settings.energy_saving
        return cls(
            energy_saving=(
                None
                if energy is None
                else EnergySavingSettingDto(
                    enabled=energy.enabled,
                    stop_offset_c=energy.stop_offset_c,
                    start_offset_c=energy.start_offset_c,
                    writable=settings.energy_stop_writable and settings.energy_start_writable,
                )
            ),
            display_eco=(
                None
                if settings.display_eco is None
                else DisplayEcoSettingDto(
                    enabled=settings.display_eco, writable=settings.display_eco_writable
                )
            ),
        )


class EnergySavingRequest(RequestModel):
    """Body of an ENERGY SAVING change; both fields are required, ``null`` means OFF."""

    stop_offset_c: int | None
    start_offset_c: int | None


class DisplayEcoRequest(RequestModel):
    """Body of a DISPLAY ECO change."""

    enabled: bool


class ClockAutoSyncRequest(RequestModel):
    """Body of a clock auto-sync toggle change (a JSON boolean, no coercion)."""

    enabled: StrictBool


class MaintenanceDto(BaseModel):
    """Maintenance counters (``null``/empty when not mapped)."""

    total_hours: int | None
    partial_hours: int | None
    ignitions: int | None
    hours_to_service: int | None
    last_alarms: list[str]

    @classmethod
    def from_domain(cls, info: MaintenanceInfo) -> Self:
        """Map the maintenance counters.

        :param info: Domain counters.
        :return: The DTO.
        """
        return cls(
            total_hours=info.total_hours,
            partial_hours=info.partial_hours,
            ignitions=info.ignitions,
            hours_to_service=info.hours_to_service,
            last_alarms=list(info.last_alarms),
        )
