"""Server-side view of the ambient temperature chart (dashboard and history)."""

import json
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from operator import attrgetter
from typing import Any, Final

from stove_pilot.service.ui.chart_math import (
    day_ticks,
    decimate_indexes,
    format_sample_label,
    format_tick,
    format_xy,
    gap_boundary_indexes,
    gap_groups,
    gap_seconds,
    hour_ticks,
    join_points,
    place_x_labels,
    step_coordinates,
    temperature_ticks,
    x_percent,
    y_percent,
)
from stove_pilot.service.ui.formatting import parse_datetime
from stove_pilot.service.ui.i18n import has_key, t

_HOUR_STEP: Final = 4
_WEEK_DAY_STEP: Final = 1
_MONTH_DAY_STEP: Final = 5
_HOUR_KEYS: Final = frozenset({"24h"})
_MONTH_KEYS: Final = frozenset({"30d"})


@dataclass(frozen=True, slots=True)
class ChartLabel:
    """One axis label, positioned as a percentage of the plot."""

    text: str
    percent: float
    align: str


@dataclass(frozen=True, slots=True)
class ChartView:
    """Polylines, axis labels and the hover-data payload of one chart."""

    segments: tuple[str, ...] = ()
    dots: tuple[str, ...] = ()
    setpoint_segments: tuple[str, ...] = ()
    setpoint_dots: tuple[str, ...] = ()
    y_labels: tuple[ChartLabel, ...] = ()
    x_labels: tuple[ChartLabel, ...] = ()
    low_text: str = ""
    high_text: str = ""
    last_text: str = ""
    aria_label: str = ""
    points_json: str = "[]"
    range_start: int = 0
    range_end: int = 0
    has_setpoint: bool = False
    texts_json: str = "{}"

    @property
    def y_track(self) -> str:
        """Widest Y tick, so the label column is as wide as the numbers."""
        return max((label.text for label in self.y_labels), key=len, default="")

    @property
    def empty(self) -> bool:
        """Whether there is no ambient sample to draw."""
        return not self.segments and not self.dots

    @property
    def x_grid(self) -> tuple[float, ...]:
        """Horizontal positions of the light vertical grid lines."""
        return tuple(
            label.percent
            for label in self.x_labels
            if label.align != "end" and 1.0 < label.percent < 99.0
        )


@dataclass(frozen=True, slots=True)
class _Reading:
    """One usable sample in epoch seconds."""

    at: float
    ambient: float
    setpoint: float | None
    state: str


def build_temperature_chart(
    payload: list[dict[str, Any]],
    *,
    end: datetime,
    span: timedelta,
    bucket_min: int,
    range_key: str,
) -> ChartView:
    """Build the ambient chart of one range, including the setpoint when present.

    :param payload: ``TemperaturePointDto`` payloads, oldest first.
    :param end: Range end (local, aware).
    :param span: Length of the range.
    :param bucket_min: Bucket width in minutes (used only to break outage gaps).
    :param range_key: ``24h``, ``7d`` or ``30d`` (anything else follows the span).
    :return: The view (``empty`` without ambient samples).
    """
    start = end - span
    t0, t1 = start.timestamp(), end.timestamp()
    if t1 <= t0:
        t1 = t0 + 1.0
    readings = _readings(payload)
    if not readings:
        return ChartView(range_start=int(round(t0)), range_end=int(round(t1)))
    bottom, mid, top = temperature_ticks(*_extent(readings))
    groups = gap_groups([row.at for row in readings], gap_seconds(bucket_min))
    segments, dots = _draw(readings, groups, t0, t1, bottom, top, setpoint=False)
    setpoint_segments, setpoint_dots = _draw(
        readings, groups, t0, t1, bottom, top, setpoint=True
    )
    low_text = f"{min(row.ambient for row in readings):.1f}"
    high_text = f"{max(row.ambient for row in readings):.1f}"
    last_text = f"{readings[-1].ambient:.1f}"
    return ChartView(
        segments,
        dots,
        setpoint_segments,
        setpoint_dots,
        (
            ChartLabel(format_tick(top), 0.0, "center"),
            ChartLabel(format_tick(mid), 50.0, "center"),
            ChartLabel(format_tick(bottom), 100.0, "center"),
        ),
        _x_labels(start, end, range_key),
        low_text,
        high_text,
        last_text,
        t("history.chart_aria", low=low_text, high=high_text, last=last_text),
        _points_json(readings, t0, t1, bottom, top, end, gap_seconds(bucket_min)),
        int(round(t0)),
        int(round(t1)),
        any(row.setpoint is not None for row in readings),
        _texts_json(),
    )


def _readings(payload: list[dict[str, Any]]) -> list[_Reading]:
    """Collect dated ambient samples.

    :param payload: Temperature payloads.
    :return: Readings, oldest first.
    """
    rows: list[_Reading] = []
    for point in payload:
        if not isinstance(point, dict):
            continue
        raw_at = point.get("at")
        at = parse_datetime(raw_at if isinstance(raw_at, str) else None)
        ambient = _finite(point.get("ambient_c"))
        if at is None or ambient is None:
            continue
        rows.append(
            _Reading(
                at.timestamp(),
                ambient,
                _finite(point.get("setpoint_c")),
                _state_label(point.get("state")),
            )
        )
    rows.sort(key=attrgetter("at"))
    return rows


def _finite(value: Any) -> float | None:
    """Read a finite number.

    :param value: JSON value.
    :return: The float, or ``None`` when it is missing or not a finite number.
    """
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    number = float(value)
    if number != number or number in {float("inf"), float("-inf")}:
        return None
    return number


def _state_label(value: Any) -> str:
    """French label of a stove state, or the raw code when unknown.

    :param value: ``state`` field.
    :return: The label.
    """
    code = value if isinstance(value, str) else ""
    key = f"state.{code}"
    if code and has_key(key):
        return t(key)
    return code


def _extent(readings: Sequence[_Reading]) -> tuple[float, float]:
    """Temperature extent of the ambient line and any setpoint.

    :param readings: Samples.
    :return: Minimum and maximum.
    """
    values = [row.ambient for row in readings]
    values.extend(row.setpoint for row in readings if row.setpoint is not None)
    return min(values), max(values)


def _draw(
    readings: Sequence[_Reading],
    groups: Sequence[Sequence[int]],
    t0: float,
    t1: float,
    bottom: float,
    top: float,
    *,
    setpoint: bool,
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Scale one series into polyline strings and single-sample dots.

    :param readings: Samples.
    :param groups: Index groups split on outages.
    :param t0: Axis start (epoch seconds).
    :param t1: Axis end (epoch seconds).
    :param bottom: Axis minimum.
    :param top: Axis maximum.
    :param setpoint: Draw the setpoint (stepped) instead of ambient.
    :return: Segment strings and dot strings.
    """
    segments: list[str] = []
    dots: list[str] = []
    for group in groups:
        runs = _setpoint_runs(readings, group) if setpoint else [list(group)]
        for run in runs:
            coords = _coords(readings, run, t0, t1, bottom, top, setpoint=setpoint)
            if len(coords) >= 2:
                segments.append(join_points(coords))
            elif len(coords) == 1:
                dots.append(format_xy(coords[0][0], coords[0][1]))
    return tuple(segments), tuple(dots)


def _setpoint_runs(readings: Sequence[_Reading], group: Sequence[int]) -> list[list[int]]:
    """Split a gap-group wherever the setpoint is missing.

    :param readings: Samples.
    :param group: Indexes of one continuous ambient run.
    :return: Runs that each have a setpoint.
    """
    runs: list[list[int]] = []
    current: list[int] = []
    for index in group:
        if readings[index].setpoint is None:
            if current:
                runs.append(current)
                current = []
            continue
        current.append(index)
    if current:
        runs.append(current)
    return runs


def _coords(
    readings: Sequence[_Reading],
    indexes: Sequence[int],
    t0: float,
    t1: float,
    bottom: float,
    top: float,
    *,
    setpoint: bool,
) -> list[tuple[float, float]]:
    """Map a run into viewBox coordinates (0..100).

    :param readings: Samples.
    :param indexes: Indexes of the run.
    :param t0: Axis start (epoch seconds).
    :param t1: Axis end (epoch seconds).
    :param bottom: Axis minimum.
    :param top: Axis maximum.
    :param setpoint: Use the setpoint and insert step corners.
    :return: Corners.
    """
    xs: list[float] = []
    ys: list[float] = []
    for index in indexes:
        row = readings[index]
        value = row.setpoint if setpoint else row.ambient
        if value is None:
            continue
        xs.append(x_percent(row.at, t0, t1))
        ys.append(y_percent(value, bottom, top))
    if setpoint:
        return step_coordinates(xs, ys)
    return list(zip(xs, ys, strict=True))


def _day_step(start: datetime, end: datetime, range_key: str) -> int | None:
    """Day step of the axis, or ``None`` when the labels are clock hours.

    :param start: Range start (local).
    :param end: Range end (local).
    :param range_key: Range tab key.
    :return: ``1`` for a week, ``5`` for a month, ``None`` for 24 h.
    """
    span = end - start
    wide = range_key not in _HOUR_KEYS and range_key != "7d" and span >= timedelta(days=14)
    if range_key in _MONTH_KEYS or wide:
        return _MONTH_DAY_STEP
    if range_key == "7d" or (range_key not in _HOUR_KEYS and span > timedelta(hours=36)):
        return _WEEK_DAY_STEP
    return None


def _edge_text(moment: datetime, day_step: int | None) -> str:
    """Format a window edge the same way as the ticks of that axis.

    :param moment: Edge instant (local).
    :param day_step: Day step, or ``None`` for a clock-hour axis.
    :return: ``HH:MM`` or ``dd/mm``.
    """
    if day_step is None:
        return f"{moment:%H:%M}"
    return f"{moment:%d/%m}"


def _tick_moments(start: datetime, end: datetime, range_key: str) -> list[tuple[datetime, str]]:
    """Choose hour or day labels for the range.

    :param start: Range start (local).
    :param end: Range end (local).
    :param range_key: Range tab key.
    :return: ``(instant, text)`` pairs, oldest first.
    """
    step = _day_step(start, end, range_key)
    if step is None:
        moments = hour_ticks(start, end, _HOUR_STEP)
        return [(moment, f"{moment:%H:%M}") for moment in moments]
    moments = day_ticks(start, end, step)
    return [(moment, f"{moment:%d/%m}") for moment in moments]


def _x_labels(start: datetime, end: datetime, range_key: str) -> tuple[ChartLabel, ...]:
    """Build the time axis, with the window end pinned to the right edge.

    :param start: Range start (local).
    :param end: Range end (local).
    :param range_key: Range tab key.
    :return: Labels.
    """
    moments = _tick_moments(start, end, range_key)
    t0, t1 = start.timestamp(), end.timestamp()
    if t1 <= t0:
        t1 = t0 + 1.0
    candidates = [
        (round(x_percent(moment.timestamp(), t0, t1), 2), text) for moment, text in moments
    ]
    step = _day_step(start, end, range_key)
    placed = place_x_labels(
        candidates,
        start_text=_edge_text(start, step),
        end_text=_edge_text(end, step),
    )
    return tuple(ChartLabel(text, percent, align) for percent, text, align in placed)


def _texts_json() -> str:
    """French words the hover tooltip will need.

    :return: Compact JSON object ``ambient``, ``setpoint`` and ``state``.
    """
    payload = {
        "ambient": t("history.legend.ambient"),
        "setpoint": t("history.legend.setpoint"),
        "state": t("history.legend.state"),
    }
    return json.dumps(payload, separators=(",", ":"), ensure_ascii=True)


def _points_json(
    readings: Sequence[_Reading],
    t0: float,
    t1: float,
    bottom: float,
    top: float,
    end: datetime,
    gap_s: float,
) -> str:
    """Serialize the hover points (at most 300, spread evenly).

    The first sample after an outage is kept and marked ``gap`` so a tooltip
    does not interpolate across the hole.

    :param readings: Samples, oldest first.
    :param t0: Axis start (epoch seconds).
    :param t1: Axis end (epoch seconds).
    :param bottom: Axis minimum.
    :param top: Axis maximum.
    :param end: Range end (local), used to format each ``label``.
    :param gap_s: Spacing that starts an outage.
    :return: Compact JSON array.
    """
    stamps = [row.at for row in readings]
    boundaries = set(gap_boundary_indexes(stamps, gap_s))
    payload = []
    for index in decimate_indexes(len(readings), keep=sorted(boundaries)):
        row = readings[index]
        item: dict[str, Any] = {
            "t": int(round(row.at)),
            "x": round(x_percent(row.at, t0, t1), 2),
            "y": round(y_percent(row.ambient, bottom, top), 2),
            "ambient": round(row.ambient, 3),
            "setpoint": None if row.setpoint is None else round(row.setpoint, 3),
            "state": row.state,
            "label": format_sample_label(row.at, end),
        }
        if index in boundaries:
            item["gap"] = True
        payload.append(item)
    return json.dumps(payload, separators=(",", ":"), ensure_ascii=True)
