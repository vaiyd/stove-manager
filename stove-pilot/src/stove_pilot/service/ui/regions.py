"""Loaders of the live regions: call the API and build the matching view model."""

from datetime import datetime, timedelta
from typing import Any, Final
from zoneinfo import ZoneInfo

from stove_pilot.service.ui.chart_view import ChartView, build_temperature_chart
from stove_pilot.service.ui.client import ApiProblem, StovePilotApiClient
from stove_pilot.service.ui.formatting import parse_datetime, since_label
from stove_pilot.service.ui.history_view import history_event_text
from stove_pilot.service.ui.viewmodels import (
    ControlsView,
    EventView,
    GlanceView,
    event_text,
)

EVENT_COUNT: Final = 5
SPARKLINE_RANGE: Final = timedelta(hours=24)
SPARKLINE_BUCKET_MIN: Final = 15
_NOT_FOUND: Final = 404


def local_now(timezone: str) -> datetime:
    """Return the current aware time in the configured timezone.

    :param timezone: IANA timezone name.
    :return: Aware local time.
    """
    return datetime.now(ZoneInfo(timezone))


def load_glance(
    client: StovePilotApiClient, now: datetime, max_age_s: float | None
) -> GlanceView:
    """Load the glance card.

    :param client: API client.
    :param now: Local current time.
    :param max_age_s: Maximum accepted cache age.
    :return: The view.
    :raises ApiProblem: The API failed.
    """
    return GlanceView.from_summary(client.summary(max_age_s), now)


def load_controls(
    client: StovePilotApiClient,
    now: datetime,
    max_age_s: float | None,
    status: dict[str, Any] | None = None,
) -> ControlsView:
    """Load the controls (status plus the suspension state, hidden without participant).

    :param client: API client.
    :param now: Local current time.
    :param max_age_s: Maximum accepted cache age (ignored when ``status`` is given).
    :param status: An already known status payload (for example from a command outcome).
    :return: The view.
    :raises ApiProblem: The API failed.
    """
    payload = status if status is not None else client.status(max_age_s)
    try:
        programming: dict[str, Any] | None = client.programming()
    except ApiProblem as problem:
        if problem.status != _NOT_FOUND:
            raise
        programming = None
    return ControlsView.build(payload, programming, now)


def _dashboard_event_text(event: dict[str, Any]) -> str:
    """Describe a dashboard event, with the safety card change spelled out.

    :param event: ``EventDto`` payload.
    :return: The French text.
    """
    detail = event.get("detail")
    if (
        event.get("kind") == "setting_changed"
        and isinstance(detail, dict)
        and detail.get("setting") == "safety"
    ):
        return history_event_text(event)
    return event_text(event)


def load_events(client: StovePilotApiClient, now: datetime) -> list[EventView]:
    """Load the latest events.

    :param client: API client.
    :param now: Local current time.
    :return: Events, newest first.
    :raises ApiProblem: The API failed.
    """
    views: list[EventView] = []
    for event in client.events(EVENT_COUNT):
        raw_at = event.get("at")
        at = parse_datetime(raw_at if isinstance(raw_at, str) else None)
        views.append(
            EventView("" if at is None else since_label(at, now), _dashboard_event_text(event))
        )
    return views


def load_sparkline(client: StovePilotApiClient, now: datetime) -> ChartView:
    """Load the 24 h ambient chart (the same plot as the history page).

    :param client: API client.
    :param now: Local current time.
    :return: The view.
    :raises ApiProblem: The API failed.
    """
    points = client.temperatures(now - SPARKLINE_RANGE, now, SPARKLINE_BUCKET_MIN)
    return build_temperature_chart(
        points,
        end=now,
        span=SPARKLINE_RANGE,
        bucket_min=SPARKLINE_BUCKET_MIN,
        range_key="24h",
    )
