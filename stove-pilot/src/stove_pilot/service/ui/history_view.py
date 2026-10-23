"""History page view models: range tabs, the temperature chart and the event timeline."""

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Final, Self

from stove_pilot.service.ui.alert_view import AlertRow
from stove_pilot.service.ui.chart_view import ChartView, build_temperature_chart
from stove_pilot.service.ui.formatting import parse_datetime, since_label
from stove_pilot.service.ui.history_cursor import EventCursor, cursor_of, parse_cursor
from stove_pilot.service.ui.i18n import has_key, t
from stove_pilot.service.ui.viewmodels import event_text

_SECONDS_PER_MINUTE: Final = 60
_TOGGLE_SETTING: Final = "display_eco"
_SAFETY_SETTING: Final = "safety"
_SOURCE_CLASSES: Final = {
    "USER": "badge-outline",
    "CHRONO": "badge-outline badge-warning border-warning text-base-content",
    "POLLER": "badge-ghost",
    "SYSTEM": "badge-ghost",
}
_NEUTRAL_SOURCE_CLASS: Final = "badge-ghost"


@dataclass(frozen=True, slots=True)
class RangeSpec:
    """One history tab: its span and the bucket width of the temperature points."""

    key: str
    label: str
    span: timedelta
    bucket_min: int
    date_labels: bool


RANGES: Final = (
    RangeSpec("24h", "history.range.24h", timedelta(hours=24), 10, False),
    RangeSpec("7d", "history.range.7d", timedelta(days=7), 60, True),
    RangeSpec("30d", "history.range.30d", timedelta(days=30), 240, True),
)


def range_spec(key: str | None) -> RangeSpec:
    """Pick a tab by key (unknown keys fall back to the first one).

    :param key: Query value (``24h``, ``7d`` or ``30d``).
    :return: The range.
    """
    return next((spec for spec in RANGES if spec.key == key), RANGES[0])


def _drift_label(drift_s: Any) -> str | None:
    """Format a clock drift for an event line.

    :param drift_s: Drift in seconds (anything else is ignored).
    :return: For example ``+16 min`` or ``-30 s``, or ``None`` when unusable.
    """
    if isinstance(drift_s, bool) or not isinstance(drift_s, int | float):
        return None
    if abs(drift_s) < _SECONDS_PER_MINUTE:
        seconds = round(drift_s)
        return f"{seconds:+d} s" if seconds else "0 s"
    return f"{round(drift_s / _SECONDS_PER_MINUTE):+d} min"


def _clock_synced_text(detail: dict[str, Any]) -> str:
    """Describe a ``clock_synced`` event.

    :param detail: Event detail (``drift_before_s``).
    :return: The French text.
    """
    drift = _drift_label(detail.get("drift_before_s"))
    if drift is None:
        return t("event.kind.clock_synced")
    return t("history.event.clock_synced", drift=drift)


def _setting_value(setting: Any, value: Any) -> str:
    """Format a register value of a ``setting_changed`` event.

    :param setting: Register name.
    :param value: JSON value (number, or ``None`` for OFF).
    :return: The text.
    """
    if setting == _TOGGLE_SETTING:
        return t("settings.on" if value else "settings.off")
    if isinstance(value, int | float) and not isinstance(value, bool):
        return f"{value:g}"
    return "" if value is None else str(value)


def _whole_number(value: Any) -> int | None:
    """Read a whole number from an event detail, rejecting bools.

    :param value: JSON value.
    :return: The integer, or ``None`` when it is missing or not an int.
    """
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def _safety_setting_text(detail: dict[str, Any]) -> str:
    """Describe a safety ``setting_changed`` event (no single ``value`` field).

    :param detail: Event detail (``enabled``, ``threshold_c``, ``max_attempts``, ``retry_interval_min``).
    :return: The French text, or the generic label when a number is missing.
    """
    threshold = _whole_number(detail.get("threshold_c"))
    attempts = _whole_number(detail.get("max_attempts"))
    interval = _whole_number(detail.get("retry_interval_min"))
    if threshold is None or attempts is None or interval is None:
        return t("event.kind.setting_changed")
    state_key = "history.setting.safety.on" if detail.get("enabled") else "history.setting.safety.off"
    return t(
        "history.setting.safety",
        state=t(state_key),
        threshold=threshold,
        attempts=attempts,
        interval=interval,
    )


def _setting_changed_text(detail: dict[str, Any]) -> str:
    """Describe a ``setting_changed`` event.

    :param detail: Event detail (``setting`` and ``value``, or the safety fields).
    :return: The French text (generic when the register is unknown).
    """
    if detail.get("setting") == _SAFETY_SETTING:
        return _safety_setting_text(detail)
    setting, value = detail.get("setting"), detail.get("value")
    key = f"history.setting.{setting}"
    if value is None and has_key(f"{key}.off"):
        return t(f"{key}.off")
    if has_key(key) and value is not None:
        return t(key, value=_setting_value(setting, value))
    return t("event.kind.setting_changed")


def history_event_text(event: dict[str, Any]) -> str:
    """Describe an event in French; unknown kinds get a neutral label.

    :param event: ``EventDto`` payload.
    :return: The text.
    """
    kind = event.get("kind")
    detail = event.get("detail")
    detail = detail if isinstance(detail, dict) else {}
    if kind == "setting_changed":
        return _setting_changed_text(detail)
    if kind == "clock_synced":
        return _clock_synced_text(detail)
    return event_text(event)


@dataclass(frozen=True, slots=True)
class TimelineEntry:
    """One line of the event timeline."""

    when: str
    text: str
    source: str
    source_class: str


def _source_label(source: str) -> str:
    """Name the origin of an event.

    :param source: ``EventDto.source`` value.
    :return: The French badge text.
    """
    key = f"history.source.{source}"
    return t(key) if has_key(key) else t("history.source.unknown")


def timeline_from_api(
    payload: list[dict[str, Any]], page_size: int, now: datetime
) -> tuple[list[TimelineEntry], EventCursor | None]:
    """Turn one fetched page (``page_size + 1`` events) into entries and the next cursor.

    :param payload: ``EventDto`` payloads, newest first, at most ``page_size + 1``.
    :param page_size: Number of events displayed per page.
    :param now: Local current time.
    :return: The displayed entries and the cursor of the older page (``None`` on the last page).
    """
    page = payload[:page_size]
    older = cursor_of(page[-1]) if len(payload) > page_size and page else None
    entries: list[TimelineEntry] = []
    for event in page:
        at = parse_datetime(event.get("at"))
        if at is None:
            continue
        source = str(event.get("source", ""))
        entries.append(
            TimelineEntry(
                since_label(at, now),
                history_event_text(event),
                _source_label(source),
                _SOURCE_CLASSES.get(source, _NEUTRAL_SOURCE_CLASS),
            )
        )
    return entries, older


def _at_least_one(value: Any) -> int:
    """Read a positive page number (anything else becomes 1).

    :param value: JSON value.
    :return: The integer, or 1 when it is missing or unusable.
    """
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        return 1
    return value


def _cursor_id(value: Any) -> str | None:
    """Read a cursor id as the text ``parse_cursor`` expects.

    :param value: JSON value.
    :return: The decimal text, or ``None`` when it is not a non-negative integer.
    """
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return str(value)


def event_page(payload: dict[str, Any]) -> tuple[list[dict[str, Any]], int, int, EventCursor | None]:
    """Read a counted events payload and clamp the page so it never exceeds the page count.

    :param payload: ``EventPageDto`` body.
    :return: Events (newest first), page, pages and the previous-page cursor.
    """
    raw = payload.get("events")
    events = [item for item in raw if isinstance(item, dict)] if isinstance(raw, list) else []
    pages = _at_least_one(payload.get("pages"))
    page = min(_at_least_one(payload.get("page")), pages)
    at = payload.get("previous_at")
    previous = parse_cursor(
        at if isinstance(at, str) else None, _cursor_id(payload.get("previous_id"))
    )
    return events, page, pages, previous


@dataclass(frozen=True, slots=True)
class HistoryView:
    """Everything the live history region needs."""

    spec: RangeSpec
    chart: ChartView
    timeline: list[TimelineEntry]
    older: EventCursor | None
    previous: EventCursor | None
    page: int
    pages: int
    paged: bool
    polled_at: str
    polled_time: str
    alerts: tuple[AlertRow, ...] = ()
    alerts_unavailable: bool = False

    @property
    def show_pager(self) -> bool:
        """Whether the page control is shown (hidden on a single page with no cursor)."""
        return self.pages > 1 or self.paged

    @classmethod
    def build(
        cls,
        spec: RangeSpec,
        temperatures: list[dict[str, Any]],
        events: list[dict[str, Any]],
        now: datetime,
        page_size: int,
        paged: bool,
        *,
        page: int,
        pages: int,
        previous: EventCursor | None,
        alerts: tuple[AlertRow, ...] = (),
        alerts_unavailable: bool = False,
    ) -> Self:
        """Combine the API answers for one live refresh.

        :param spec: The range.
        :param temperatures: ``TemperaturePointDto`` payloads.
        :param events: ``EventDto`` payloads (``page_size + 1`` at most).
        :param now: Local current time.
        :param page_size: Number of events displayed per page.
        :param paged: Whether a cursor selected this page (not the first one).
        :param page: 1-based page (clamped to ``pages``).
        :param pages: Page count (at least 1).
        :param previous: Cursor of the previous page, or ``None`` when that page is the first.
        :param alerts: Display-alert rows (not filtered by the range).
        :param alerts_unavailable: The alerts call failed; ``alerts`` is then empty.
        :return: The view.
        """
        timeline, older = timeline_from_api(events, page_size, now)
        safe_pages = max(1, pages)
        safe_page = min(max(1, page), safe_pages)
        return cls(
            spec,
            build_temperature_chart(
                temperatures,
                end=now,
                span=spec.span,
                bucket_min=spec.bucket_min,
                range_key=spec.key,
            ),
            timeline,
            older,
            previous,
            safe_page,
            safe_pages,
            paged,
            now.isoformat(timespec="seconds"),
            f"{now:%H:%M}",
            alerts,
            alerts_unavailable,
        )
