"""History DTOs: temperature points and audit events."""

from datetime import datetime
from typing import Any, Self

from pydantic import BaseModel

from stove_pilot.core.history.models import DisplayAlert, EventPage, StoveEvent, TemperatureSample


class TemperaturePointDto(BaseModel):
    """One (possibly bucket-averaged) temperature sample."""

    at: datetime
    ambient_c: float | None
    fumes_c: float | None
    state: str
    setpoint_c: float | None = None

    @classmethod
    def from_domain(cls, sample: TemperatureSample) -> Self:
        """Map a sample.

        :param sample: Domain sample.
        :return: The DTO.
        """
        return cls(
            at=sample.at,
            ambient_c=sample.ambient_c,
            fumes_c=sample.fumes_c,
            state=sample.state.value,
            setpoint_c=sample.setpoint_c,
        )


class EventDto(BaseModel):
    """One audit-trail entry (``id`` is the keyset cursor tiebreaker)."""

    id: int
    at: datetime
    kind: str
    source: str
    detail: dict[str, Any]

    @classmethod
    def from_domain(cls, event: StoveEvent) -> Self:
        """Map an event.

        :param event: Domain event.
        :return: The DTO.
        :raises ValueError: The event was not read from the store (no id).
        """
        if event.id is None:
            raise ValueError("An event DTO needs a stored event (id is missing)")
        return cls(
            id=event.id,
            at=event.at,
            kind=event.kind,
            source=event.source.value,
            detail=event.detail,
        )


class EventPageDto(BaseModel):
    """Counted event page (opt-in; the bare event list stays the default response)."""

    events: list[EventDto]
    total: int
    newer_count: int | None = None
    page: int | None = None
    pages: int | None = None
    previous_at: datetime | None = None
    previous_id: int | None = None

    @classmethod
    def from_domain(cls, window: EventPage) -> Self:
        """Map a counted page.

        :param window: Domain page.
        :return: The DTO.
        """
        return cls(
            events=[EventDto.from_domain(event) for event in window.events],
            total=window.total,
            newer_count=window.newer_count,
            page=window.page,
            pages=window.pages,
            previous_at=window.previous_at,
            previous_id=window.previous_id,
        )


class HistoryAlertDto(BaseModel):
    """One persisted display alert (one row per message code)."""

    code: str
    title: str
    severity: str
    occurrences: int
    first_seen_at: datetime
    last_seen_at: datetime
    state: str

    @classmethod
    def from_domain(cls, alert: DisplayAlert) -> Self:
        """Map a stored alert.

        :param alert: Domain alert.
        :return: The DTO.
        """
        return cls(
            code=alert.code,
            title=alert.title,
            severity=alert.severity,
            occurrences=alert.occurrences,
            first_seen_at=alert.first_seen_at,
            last_seen_at=alert.last_seen_at,
            state=alert.state,
        )
