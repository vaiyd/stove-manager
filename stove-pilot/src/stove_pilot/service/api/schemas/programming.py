"""Programming suspension DTOs (``SuspensionDto`` itself is shared with the summary)."""

from datetime import datetime
from typing import Self

from pydantic import BaseModel

from stove_pilot.core.stove.repository import StoveRepository
from stove_pilot.core.suspension.models import KIND_VACATION, SuspendOutcome, SuspensionState
from stove_pilot.service.api.schemas.common import RequestModel
from stove_pilot.service.api.schemas.stove import CommandOutcomeDto
from stove_pilot.service.api.schemas.summary import SuspensionDto


def suspension_dto(state: SuspensionState) -> SuspensionDto:
    """Map a suspension state.

    :param state: Domain state.
    :return: The DTO (snapshots are internal; only the previous setpoint of a vacation is exposed).
    """
    return SuspensionDto(
        suspended=state.suspended,
        since=state.since,
        until=state.until,
        thermostat_mode=state.thermostat_mode,
        kind=state.kind,
        frost_setpoint_c=state.frost_setpoint_c,
        previous_setpoint_c=(
            state.stove_snapshot.get("setpoint_c") if state.kind == KIND_VACATION else None
        ),
        phase=state.phase,
    )


class SuspendRequest(RequestModel):
    """Body of a suspend call; a naive ``until`` is read in the configured timezone."""

    until: datetime | None = None
    thermostat_mode: bool = True


class VacationRequest(RequestModel):
    """Body of a vacation call; a naive ``until`` is read in the configured timezone."""

    until: datetime | None = None


class SuspendOutcomeDto(BaseModel):
    """Result of a suspend call (``warnings`` are machine-readable codes)."""

    state: SuspensionDto
    start_outcome: CommandOutcomeDto | None
    warnings: list[str]

    @classmethod
    def from_domain(cls, outcome: SuspendOutcome, stove: StoveRepository) -> Self:
        """Map a suspend outcome.

        :param outcome: Domain outcome.
        :param stove: Stove repository (staleness and capabilities of the status).
        :return: The DTO.
        """
        start = outcome.start_outcome
        return cls(
            state=suspension_dto(outcome.state),
            start_outcome=None if start is None else CommandOutcomeDto.from_domain(start, stove),
            warnings=list(outcome.warnings),
        )
