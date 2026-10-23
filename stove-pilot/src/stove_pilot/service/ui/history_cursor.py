"""Keyset cursor of the history event timeline (carried in the page URL query)."""

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Final

# Reason: SQLite integers are signed 64-bit; the API rejects a larger cursor id.
MAX_CURSOR_ID: Final = 2**63 - 1


@dataclass(frozen=True, slots=True)
class EventCursor:
    """Position after an event: its ``at`` text exactly as the API sent it, and its id."""

    at: str
    id: int


def parse_cursor(at_raw: str | None, id_raw: str | None) -> EventCursor | None:
    """Validate the cursor query values (any problem means no cursor, i.e. the first page).

    :param at_raw: ``before_at`` query value.
    :param id_raw: ``before_id`` query value.
    :return: The cursor, or ``None`` when absent, incomplete or invalid.
    """
    if not at_raw or not id_raw or not (id_raw.isascii() and id_raw.isdigit()):
        return None
    event_id = int(id_raw)
    if event_id > MAX_CURSOR_ID:
        return None
    try:
        parsed = datetime.fromisoformat(at_raw)
    except ValueError:
        return None
    # Reason: the API only emits aware instants; a naive one would be re-read in another zone.
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    try:
        parsed.astimezone(UTC)
    except OverflowError:
        # Reason: the API normalizes to UTC and answers 500 when that overflows.
        return None
    return EventCursor(at_raw, event_id)


def cursor_of(event: dict[str, Any]) -> EventCursor | None:
    """Build the cursor that continues after an event.

    :param event: ``EventDto`` payload.
    :return: The cursor, or ``None`` when the payload lacks a usable ``at`` or ``id``.
    """
    at, event_id = event.get("at"), event.get("id")
    if not isinstance(at, str) or isinstance(event_id, bool) or not isinstance(event_id, int):
        return None
    return EventCursor(at, event_id)
