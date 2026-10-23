"""History models: temperature samples, display alerts and the stove event log."""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal

from stove_pilot.core.stove.models import EventSource, StoveState


@dataclass(frozen=True, slots=True)
class TemperatureSample:
    """One reading (``at`` is timezone-aware; optional fields are ``None`` when unavailable)."""

    at: datetime
    ambient_c: float | None
    fumes_c: float | None
    state: StoveState
    power_level: int | None = None
    setpoint_c: float | None = None


@dataclass(frozen=True, slots=True)
class StoveEvent:
    """One audit-trail entry (``detail`` holds JSON-serializable key/value pairs).

    ``id`` is the storage identity: ``None`` until the event has been read back from the store.
    """

    at: datetime
    kind: str
    source: EventSource
    detail: dict[str, Any] = field(default_factory=dict)
    id: int | None = None


@dataclass(frozen=True, slots=True)
class EventPage:
    """One keyset page of events plus the counts that label it.

    ``newer_count`` is ``None`` on the first page (no cursor). ``page`` and ``pages`` are
    ``None`` when the caller did not ask for page numbers. ``previous_at`` and ``previous_id``
    are set together when the previous page is not the first one.
    """

    events: list[StoveEvent]
    total: int
    newer_count: int | None
    page: int | None = None
    pages: int | None = None
    previous_at: datetime | None = None
    previous_id: int | None = None


def page_numbers(total: int, newer_count: int | None, page_size: int) -> tuple[int, int]:
    """Return the 1-based page and the page count of a range.

    ``newer_count`` counts events at or newer than the cursor inside the range. The cursor
    row is the last event of the previous page, so it is included in that count.
    ``N = newer_count // page_size + 1``. Without a cursor the page is 1.
    ``N`` is clamped to ``M``, and ``M`` is at least 1.

    :param total: Events in the range, before pagination.
    :param newer_count: Events at or newer than the cursor, or ``None`` on the first page.
    :param page_size: Events displayed per page (at least 1).
    :return: ``(page, pages)``.
    :raises ValueError: ``page_size`` is below 1.
    """
    if page_size < 1:
        raise ValueError("page_size must be at least 1")
    pages = max(1, (total + page_size - 1) // page_size)
    if newer_count is None or newer_count < 0:
        return 1, pages
    return min(newer_count // page_size + 1, pages), pages


def previous_page_offset(page: int, page_size: int) -> int | None:
    """Return the newest-first offset of the cursor that opens the previous page.

    The cursor of page ``P`` (``P >= 2``) is the event at offset ``(P - 1) * page_size - 1``.

    :param page: Current 1-based page.
    :param page_size: Events displayed per page (at least 1).
    :return: Offset, or ``None`` when the previous page is the first page.
    """
    if page <= 2 or page_size < 1:
        return None
    return (page - 2) * page_size - 1


@dataclass(frozen=True, slots=True)
class DisplayAlert:
    """One display-message code remembered across polls.

    ``occurrences`` counts distinct appearances: a return counts only after the code was
    absent from at least one poll. ``cleared_at`` is set when the code leaves the display.
    """

    code: str
    title: str
    severity: str
    occurrences: int
    first_seen_at: datetime
    last_seen_at: datetime
    state: Literal["active", "cleared"]
    cleared_at: datetime | None = None
