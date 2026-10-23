"""At-a-glance summary DTOs; ``ProgrammingSummaryDto`` is the contract shared with every feature."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel

from stove_pilot.service.api.schemas.stove import StoveStatusDto


class CurrentProgramDto(BaseModel):
    """The program running now (``start``/``stop`` are local ``HH:MM``).

    ``stop_at`` is the aware end of the window. ``open_ended`` is true when no stop
    exists; ``stop`` then repeats ``start`` and ``stop_at`` is null.
    """

    start: str
    stop: str
    setpoint_c: float
    power_level: int
    open_ended: bool = False
    stop_at: datetime | None = None


class NextProgramDto(BaseModel):
    """The next scheduled action (``at`` is aware; ``action`` is ``start`` or ``stop``)."""

    at: datetime
    action: str


class ProgrammingSummaryDto(BaseModel):
    """What one programming feature does now and next."""

    feature: str
    label: str
    active: bool
    current: CurrentProgramDto | None
    next: NextProgramDto | None
    blocked_reason: str | None


class AlertDto(BaseModel):
    """The single most important message for the glance card (French texts)."""

    level: str
    title: str
    action: str | None


class SuspensionDto(BaseModel):
    """State of "suspend all programming"; the vacation fields are null for a plain suspension."""

    suspended: bool
    since: datetime | None
    until: datetime | None
    thermostat_mode: bool
    kind: Literal["programming", "vacation"] = "programming"
    frost_setpoint_c: float | None = None
    previous_setpoint_c: float | None = None
    phase: Literal["activating", "active", "restoring"] = "active"


class SafetySummaryDto(BaseModel):
    """Safety line of the glance summary (null when safety is not wired)."""

    enabled: bool
    status: str
    threshold_c: int


class SummaryDto(BaseModel):
    """Stove status, top alert, programming lines, suspension and safety in one response."""

    stove: StoveStatusDto
    alert: AlertDto | None
    programming: list[ProgrammingSummaryDto]
    suspension: SuspensionDto | None
    safety: SafetySummaryDto | None = None
