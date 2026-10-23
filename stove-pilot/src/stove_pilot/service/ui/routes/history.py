"""History page: temperature chart, event timeline and alert catalog (read only)."""

import logging
from collections.abc import Callable
from datetime import datetime
from typing import Final

from flask import Blueprint, Flask, render_template, request, url_for

from stove_pilot.common.settings import Settings
from stove_pilot.service.ui.alert_view import AlertRow, alert_rows
from stove_pilot.service.ui.client import ApiProblem, StovePilotApiClient
from stove_pilot.service.ui.history_cursor import EventCursor, parse_cursor
from stove_pilot.service.ui.history_view import RANGES, HistoryView, RangeSpec, event_page, range_spec
from stove_pilot.service.ui.live import LastGoodCache
from stove_pilot.service.ui.regions import local_now

_LOGGER = logging.getLogger(__name__)
_UNPROCESSABLE: Final = 422
_ALERT_LIMIT: Final = 200


def _load_alerts(
    client: StovePilotApiClient, now: datetime
) -> tuple[tuple[AlertRow, ...], bool]:
    """Load the alert catalog without failing the rest of the history page.

    :param client: API client.
    :param now: Local current time.
    :return: The rows, and whether the alerts call failed.
    """
    try:
        return alert_rows(client.alerts(_ALERT_LIMIT), now), False
    except ApiProblem as problem:
        _LOGGER.warning("History alerts unavailable (%s %s)", problem.status, problem.code)
        return (), True


def _load_history(
    client: StovePilotApiClient,
    spec: RangeSpec,
    now: datetime,
    page_size: int,
    cursor: EventCursor | None,
) -> HistoryView:
    """Load the chart, one event page and the alert catalog.

    A failed alerts call leaves the chart and the events in place and marks only that
    card unavailable. The region stays fresh (``data-stale`` false) because those two
    reads succeeded.

    :param client: API client.
    :param spec: The range.
    :param now: Local current time.
    :param page_size: Number of events displayed per page.
    :param cursor: Keyset cursor of an older page, or ``None`` for the first page.
    :return: The view.
    :raises ApiProblem: Temperatures or events failed.
    """
    points = client.temperatures(now - spec.span, now, spec.bucket_min)
    body = client.events_page(
        page_size + 1,
        page_size=page_size,
        start=now - spec.span,
        end=now,
        before_at=cursor.at if cursor else None,
        before_id=cursor.id if cursor else None,
    )
    events, page, pages, previous = event_page(body)
    alerts, alerts_down = _load_alerts(client, now)
    return HistoryView.build(
        spec,
        points,
        events,
        now,
        page_size,
        cursor is not None,
        page=page,
        pages=pages,
        previous=previous,
        alerts=alerts,
        alerts_unavailable=alerts_down,
    )


def _load_uncached(
    client: StovePilotApiClient,
    spec: RangeSpec,
    now: datetime,
    page_size: int,
    cursor: EventCursor | None,
) -> tuple[HistoryView | None, bool]:
    """Load a page outside the last-good cache (an older page depends on its cursor).

    :param client: API client.
    :param spec: The range.
    :param now: Local current time.
    :param page_size: Number of events displayed per page.
    :param cursor: Keyset cursor of the page, or ``None`` for the first page.
    :return: The view (``None`` when the API failed) and the ``api_down`` flag.
    """
    try:
        return _load_history(client, spec, now, page_size, cursor), False
    except ApiProblem as problem:
        _LOGGER.warning("History page failed (%s %s)", problem.status, problem.code)
        if cursor is not None and problem.status == _UNPROCESSABLE:
            # Reason: the API refused the cursor; the first page beats an error.
            return _load_uncached(client, spec, now, page_size, None)
        return None, True

def register_history_ui(
    app: Flask,
    settings: Settings,
    get_api_client: Callable[[], StovePilotApiClient],
    cache: LastGoodCache,
) -> None:
    """Add the history page and its live partial (both read only).

    :param app: UI application.
    :param settings: Application settings (timezone, events page size).
    :param get_api_client: Lazy getter of the API client.
    :param cache: Last-good cache of the live regions.
    """
    bp = Blueprint("history", __name__)

    def render_live(template: str) -> str:
        spec = range_spec(request.args.get("range"))
        cursor = parse_cursor(request.args.get("before_at"), request.args.get("before_id"))
        client, now = get_api_client(), local_now(settings.timezone)
        size = settings.ui_history_events_page_size
        if cursor is None:
            view, down = cache.fetch(
                f"history.{spec.key}", lambda: _load_history(client, spec, now, size, None)
            )
        else:
            view, down = _load_uncached(client, spec, now, size, cursor)
        # Reason: only the first page is live; an older page is static (no polling trigger).
        live = cursor is None or (view is not None and not view.paged)
        return render_template(
            template,
            history=view,
            history_stale=down,
            spec=spec,
            ranges=RANGES,
            live=live,
            partial_url=url_for("history.live_partial", range=spec.key),
        )

    @bp.get("/history")
    def index() -> str:
        return render_live("history.html")

    @bp.get("/partials/history")
    def live_partial() -> str:
        return render_live("partials/history_live.html")

    app.register_blueprint(bp)
