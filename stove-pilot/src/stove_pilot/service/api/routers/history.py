"""History routes: bucketed temperatures, the event log and display alerts.

Route handlers are closures defined inside ``register_*`` functions (see ``health``).
"""

from collections.abc import Callable
from datetime import datetime, timedelta, tzinfo
from typing import Any, Final

from flask import Blueprint, request

from stove_pilot.core.history.repository import HistoryRepository
from stove_pilot.service.api.parsing import query_datetime, query_int, query_optional_int
from stove_pilot.service.api.schemas.history import (
    EventDto,
    EventPageDto,
    HistoryAlertDto,
    TemperaturePointDto,
)

DEFAULT_RANGE: Final = timedelta(hours=24)
DEFAULT_BUCKET_MIN: Final = 5
MAX_BUCKET_MIN: Final = 1440
DEFAULT_EVENT_LIMIT: Final = 50
MAX_EVENT_LIMIT: Final = 500
DEFAULT_ALERT_LIMIT: Final = 50
MAX_ALERT_LIMIT: Final = 200
# Reason: SQLite integers are signed 64-bit; a larger cursor id would fail at bind time.
MAX_EVENT_ID: Final = 2**63 - 1
_COUNT_FLAGS: Final = frozenset({"1", "true", "yes"})


def _counts_requested(raw: str | None) -> bool:
    """Return whether ``include_counts`` asks for the counted page object.

    :param raw: Query value, or ``None`` when the parameter is absent.
    :return: True for ``1``, ``true`` or ``yes``, ignoring case and surrounding space.
    """
    if raw is None:
        return False
    return raw.strip().lower() in _COUNT_FLAGS


def register_history_routes(
    bp: Blueprint, get_history_repo: Callable[[], HistoryRepository], tz: tzinfo
) -> None:
    """Add the history routes.

    :param bp: API blueprint.
    :param get_history_repo: Lazy getter of the history repository.
    :param tz: Local timezone, also applied to naive ``from``/``to`` values.
    """

    @bp.get("/history/temperatures")
    def temperatures() -> list[dict[str, Any]]:
        end = query_datetime("to", tz) or datetime.now(tz)
        start = query_datetime("from", tz) or end - DEFAULT_RANGE
        bucket_min = query_int(
            "bucket_min", default=DEFAULT_BUCKET_MIN, minimum=1, maximum=MAX_BUCKET_MIN
        )
        points = get_history_repo().temperatures(start, end, bucket_min)
        return [TemperaturePointDto.from_domain(point).model_dump(mode="json") for point in points]

    @bp.get("/history/events")
    def events() -> list[dict[str, Any]] | dict[str, Any]:
        limit = query_int("limit", default=DEFAULT_EVENT_LIMIT, minimum=1, maximum=MAX_EVENT_LIMIT)
        start = query_datetime("from", tz)
        end = query_datetime("to", tz)
        before_at = query_datetime("before_at", tz)
        before_id = query_optional_int("before_id", minimum=0, maximum=MAX_EVENT_ID)
        repo = get_history_repo()
        # Reason: old clients keep the JSON array. Counts are opt-in on this same route.
        if not _counts_requested(request.args.get("include_counts")):
            page = repo.events_page(
                limit, start=start, end=end, before_at=before_at, before_id=before_id
            )
            return [EventDto.from_domain(event).model_dump(mode="json") for event in page]
        page_size = query_optional_int("page_size", minimum=1, maximum=MAX_EVENT_LIMIT)
        window = repo.events_window(
            limit,
            start=start,
            end=end,
            before_at=before_at,
            before_id=before_id,
            page_size=page_size,
        )
        return EventPageDto.from_domain(window).model_dump(mode="json", exclude_none=True)

    @bp.get("/history/alerts")
    def alerts() -> list[dict[str, Any]]:
        limit = query_int("limit", default=DEFAULT_ALERT_LIMIT, minimum=1, maximum=MAX_ALERT_LIMIT)
        rows = get_history_repo().alerts(limit)
        return [HistoryAlertDto.from_domain(row).model_dump(mode="json") for row in rows]
