"""Stove status, command and setpoint DTOs."""

from datetime import datetime
from typing import Literal, Self

from pydantic import BaseModel

from stove_pilot.core.stove.messages import AlarmInfo, DisplayMessage
from stove_pilot.core.stove.models import (
    CommandOutcome,
    EnergySavingStatus,
    StoveCapabilities,
    StoveStatus,
)
from stove_pilot.core.stove.repository import StoveRepository
from stove_pilot.service.api.schemas.common import RequestModel


class DisplayMessageDto(BaseModel):
    """A message decoded from the panel display (French texts)."""

    code: str
    level: Literal["info", "warning", "error"]
    title: str
    action: str

    @classmethod
    def from_domain(cls, message: DisplayMessage) -> Self:
        """Map a decoded display message.

        :param message: Domain message.
        :return: The DTO.
        """
        return cls(
            code=message.code, level=message.level, title=message.title_fr, action=message.action_fr
        )


class AlarmDto(BaseModel):
    """An active stove alarm (French texts)."""

    code: str
    title: str
    action: str
    requires_technician: bool

    @classmethod
    def from_domain(cls, alarm: AlarmInfo) -> Self:
        """Map an alarm.

        :param alarm: Domain alarm.
        :return: The DTO.
        """
        return cls(
            code=alarm.code,
            title=alarm.title_fr,
            action=alarm.action_fr,
            requires_technician=alarm.requires_technician,
        )


class EnergySavingDto(BaseModel):
    """ENERGY SAVING mode as seen in the status (``null`` offset = OFF)."""

    enabled: bool
    stop_offset_c: int | None
    start_offset_c: int | None
    restart_below_c: float | None

    @classmethod
    def from_domain(cls, energy: EnergySavingStatus) -> Self:
        """Map the ENERGY SAVING status.

        :param energy: Domain status.
        :return: The DTO.
        """
        return cls(
            enabled=energy.enabled,
            stop_offset_c=energy.stop_offset_c,
            start_offset_c=energy.start_offset_c,
            restart_below_c=energy.restart_below_c,
        )


class CapabilitiesDto(BaseModel):
    """Which writes are allowed (register or recipe verified, or unverified writes enabled)."""

    start: bool
    stop: bool
    setpoint: bool
    power: bool
    energy_saving: bool
    display_eco: bool
    clock_sync: bool

    @classmethod
    def from_domain(cls, capabilities: StoveCapabilities) -> Self:
        """Map the capability flags.

        :param capabilities: Domain capabilities.
        :return: The DTO.
        """
        return cls(
            start=capabilities.start,
            stop=capabilities.stop,
            setpoint=capabilities.setpoint,
            power=capabilities.power,
            energy_saving=capabilities.energy_saving,
            display_eco=capabilities.display_eco,
            clock_sync=capabilities.clock_sync,
        )


class StoveStatusDto(BaseModel):
    """One stove snapshot with its panel decoding, clock, staleness and capabilities."""

    state: str
    raw_state_code: int
    state_label: str
    ambient_temp_c: float
    fumes_temp_c: float
    setpoint_c: float
    power_level: int
    display_text: str | None
    display_message: DisplayMessageDto | None
    alarm: AlarmDto | None
    energy_saving: EnergySavingDto | None
    stove_time: datetime | None
    clock_drift_s: float | None
    polled_at: datetime
    stale: bool
    capabilities: CapabilitiesDto

    @classmethod
    def from_domain(
        cls, status: StoveStatus, *, stale: bool, capabilities: StoveCapabilities
    ) -> Self:
        """Map a status snapshot.

        :param status: Domain status.
        :param stale: Whether the cache is flagged stale.
        :param capabilities: Allowed writes.
        :return: The DTO.
        """
        return cls(
            state=status.state.value,
            raw_state_code=status.raw_state_code,
            state_label=status.state_label,
            ambient_temp_c=status.ambient_temp_c,
            fumes_temp_c=status.fumes_temp_c,
            setpoint_c=status.setpoint_c,
            power_level=status.power_level,
            display_text=status.display_text,
            display_message=(
                None
                if status.display_message is None
                else DisplayMessageDto.from_domain(status.display_message)
            ),
            alarm=None if status.alarm is None else AlarmDto.from_domain(status.alarm),
            energy_saving=(
                None
                if status.energy_saving is None
                else EnergySavingDto.from_domain(status.energy_saving)
            ),
            stove_time=status.stove_time,
            clock_drift_s=status.clock_drift_s,
            polled_at=status.polled_at,
            stale=stale,
            capabilities=CapabilitiesDto.from_domain(capabilities),
        )

    @classmethod
    def load(cls, repository: StoveRepository, max_age_s: float | None) -> Self:
        """Read the status through the repository cache and map it.

        :param repository: Stove repository.
        :param max_age_s: Maximum accepted cache age (``0`` forces a throttled live read).
        :return: The DTO.
        :raises GatewayError: Nothing is cached and the live read failed.
        """
        status = repository.get_status(max_age_s)
        return cls.from_domain(
            status, stale=repository.is_stale(), capabilities=repository.capabilities()
        )


class CommandOutcomeDto(BaseModel):
    """Result of a start or stop command (``result``: applied, no_op or deferred)."""

    result: str
    reason: str | None
    status: StoveStatusDto | None

    @classmethod
    def from_domain(cls, outcome: CommandOutcome, repository: StoveRepository) -> Self:
        """Map a command outcome.

        :param outcome: Domain outcome.
        :param repository: Stove repository (staleness and capabilities of the status).
        :return: The DTO.
        """
        return cls(
            result=outcome.result.value.lower(),
            reason=outcome.reason,
            status=(
                None
                if outcome.status is None
                else StoveStatusDto.from_domain(
                    outcome.status,
                    stale=repository.is_stale(),
                    capabilities=repository.capabilities(),
                )
            ),
        )


class StopRequest(RequestModel):
    """Optional body of a stop command."""

    confirm_ignition_stop: bool = False


class SetpointRequest(RequestModel):
    """Body of a setpoint change (range checked against the stove profile)."""

    value_c: float


class PowerRequest(RequestModel):
    """Body of a power level change (range checked against the stove profile)."""

    level: int
