"""Pure geometry for the ambient temperature chart (no I/O, no translations)."""

import math
from collections.abc import Sequence
from datetime import datetime, timedelta
from typing import Final

# Nice steps in degrees. Three ticks (min, mid, max) means the axis is two steps tall.
_NICE_STEPS: Final = (0.5, 1.0, 1.5, 2.0, 2.5, 5.0, 10.0, 15.0, 20.0, 25.0, 50.0)
_SNAP: Final = 0.5
_PAD_FLOOR_C: Final = 0.4
_PAD_FRACTION: Final = 0.12
_GAP_FACTOR: Final = 2.5
_SECONDS_PER_MINUTE: Final = 60
_MAX_POINTS: Final = 300
_STEP_EPSILON_C: Final = 0.05
# Reason: measured at 390 px: the plot is about 280 px and a 5-character tick is about 31 px.
_PLOT_PX: Final = 270.0
_CHAR_PX: Final = 6.8
_LABEL_GAP_PX: Final = 4.0


def temperature_ticks(low: float, high: float) -> tuple[float, float, float]:
    """Pick min, mid and max ticks that contain the values with padding.

    :param low: Lowest temperature to show.
    :param high: Highest temperature to show.
    :return: Three ascending ticks on a 0.5 degree grid.
    """
    if not math.isfinite(low) or not math.isfinite(high):
        return (0.0, 1.0, 2.0)
    if high < low:
        low, high = high, low
    span = high - low
    pad = _PAD_FLOOR_C if span < 1e-9 else max(0.3, span * _PAD_FRACTION)
    lo, hi = low - pad, high + pad
    covered = hi - lo
    steps = list(_NICE_STEPS)
    while steps[-1] * 2 < covered:
        steps.append(steps[-1] * 2)
    for step in steps:
        window = _tick_window(step, lo, hi)
        if window is not None:
            return window
    return (round(lo, 4), round((lo + hi) / 2, 4), round(hi, 4))


def format_tick(value: float) -> str:
    """Format a tick without a trailing ``.0``.

    :param value: Tick temperature.
    :return: Short decimal text.
    """
    text = f"{value:.1f}"
    if text in {"-0.0", "0.0"}:
        return "0"
    if text.endswith(".0"):
        return text[:-2]
    return text


def gap_seconds(bucket_min: int) -> float:
    """Return the spacing that starts a new polyline segment.

    :param bucket_min: Bucket width in minutes.
    :return: Gap threshold in seconds (two and a half buckets).
    """
    width = max(1, bucket_min) * _SECONDS_PER_MINUTE
    return width * _GAP_FACTOR


def x_percent(stamp: float, start: float, end: float) -> float:
    """Map an instant onto the plot width.

    :param stamp: Epoch seconds.
    :param start: Axis start (epoch seconds).
    :param end: Axis end (epoch seconds).
    :return: Percentage from the left, clamped to 0..100.
    """
    return _clamp_percent(stamp, start, end)


def y_percent(value: float, bottom: float, top: float) -> float:
    """Map a temperature onto the plot height, from the top.

    :param value: Temperature.
    :param bottom: Axis minimum.
    :param top: Axis maximum.
    :return: Percentage from the top, clamped to 0..100.
    """
    return _clamp_percent(top - value, 0.0, top - bottom)


def decimate_indexes(
    count: int, limit: int = _MAX_POINTS, keep: Sequence[int] = ()
) -> list[int]:
    """Pick indexes spread evenly across a series, always keeping both ends.

    Indexes in ``keep`` (the first sample after each outage) are kept too and
    count toward the limit. At or below the limit, every index is returned.

    :param count: Number of samples.
    :param limit: Maximum indexes (no decimation at or below this count).
    :param keep: Extra indexes that must survive decimation.
    :return: Increasing indexes.
    """
    if count <= 0 or limit < 1:
        return []
    if count <= limit:
        return list(range(count))
    forced = {0, count - 1}
    for index in keep:
        if 0 <= index < count:
            forced.add(index)
    if len(forced) >= limit:
        return _cap_forced(sorted(forced), limit)
    chosen = set(forced)
    pool = [index for index in range(count) if index not in forced]
    _take_evenly(pool, limit - len(forced), chosen)
    return sorted(chosen)


def gap_groups(stamps: Sequence[float], gap_s: float) -> list[list[int]]:
    """Split sample indexes where two stamps are more than ``gap_s`` apart.

    :param stamps: Epoch seconds, oldest first.
    :param gap_s: Spacing that starts a new group.
    :return: Groups of indexes (a lone sample stays a one-item group).
    """
    if not stamps:
        return []
    groups: list[list[int]] = [[0]]
    for index in range(1, len(stamps)):
        if stamps[index] - stamps[index - 1] > gap_s:
            groups.append([])
        groups[-1].append(index)
    return groups


def gap_boundary_indexes(stamps: Sequence[float], gap_s: float) -> list[int]:
    """Indexes of the first sample after each outage.

    :param stamps: Epoch seconds, oldest first.
    :param gap_s: Spacing that starts an outage.
    :return: Indexes, oldest first.
    """
    groups = gap_groups(stamps, gap_s)
    return [group[0] for group in groups[1:] if group]


def step_coordinates(
    xs: Sequence[float], ys: Sequence[float]
) -> list[tuple[float, float]]:
    """Hold a value until it changes, then step (a flat series stays straight).

    :param xs: Horizontal positions.
    :param ys: Values at those positions.
    :return: Polyline corners, including one extra corner on each step.
    """
    if not xs:
        return []
    coords = [(xs[0], ys[0])]
    for x_pos, y_pos in zip(xs[1:], ys[1:], strict=True):
        previous = coords[-1][1]
        if abs(y_pos - previous) >= _STEP_EPSILON_C:
            coords.append((x_pos, previous))
        coords.append((x_pos, y_pos))
    return coords


def format_xy(x_pos: float, y_pos: float) -> str:
    """Format one SVG point.

    :param x_pos: Horizontal position in viewBox units.
    :param y_pos: Vertical position in viewBox units.
    :return: ``"x,y"`` with two decimals.
    """
    return f"{x_pos:.2f},{y_pos:.2f}"


def join_points(coords: Sequence[tuple[float, float]]) -> str:
    """Join SVG points.

    :param coords: Polyline corners.
    :return: ``"x,y x,y ..."``.
    """
    return " ".join(format_xy(x_pos, y_pos) for x_pos, y_pos in coords)


def hour_ticks(start: datetime, end: datetime, step_h: int) -> list[datetime]:
    """Return clock-aligned hours strictly inside the range.

    :param start: Range start (local, aware).
    :param end: Range end (local, aware).
    :param step_h: Hour step (for example 4).
    :return: Instants, oldest first.
    """
    if step_h < 1 or end <= start:
        return []
    cursor = start.replace(minute=0, second=0, microsecond=0)
    if cursor < start:
        cursor += timedelta(hours=1)
    shift = (step_h - cursor.hour % step_h) % step_h
    cursor += timedelta(hours=shift)
    ticks: list[datetime] = []
    while cursor < end:
        if cursor > start:
            ticks.append(cursor)
        cursor += timedelta(hours=step_h)
    return ticks


def day_ticks(start: datetime, end: datetime, step_days: int) -> list[datetime]:
    """Return local noons inside the range, every ``step_days`` calendar days.

    :param start: Range start (local, aware).
    :param end: Range end (local, aware).
    :param step_days: Day step (1 keeps every noon).
    :return: Instants, oldest first.
    """
    if step_days < 1 or end <= start or start.tzinfo is None:
        return []
    day = start.date()
    last = end.date()
    ticks: list[datetime] = []
    while day <= last:
        if step_days == 1 or day.toordinal() % step_days == 0:
            noon = datetime(day.year, day.month, day.day, 12, 0, tzinfo=start.tzinfo)
            if start < noon < end:
                ticks.append(noon)
        day += timedelta(days=1)
    return ticks


def format_sample_label(stamp: float, end: datetime) -> str:
    """Format a sample time in the timezone of the range end.

    Same local calendar day as ``end`` yields ``HH:MM``. Any earlier day yields
    ``dd/mm HH:MM``. Computed here so the browser timezone does not matter.

    :param stamp: Epoch seconds.
    :param end: Range end (local, aware).
    :return: The label.
    """
    if end.tzinfo is None:
        moment = datetime.fromtimestamp(stamp)
    else:
        moment = datetime.fromtimestamp(stamp, tz=end.tzinfo)
    if moment.date() == end.date():
        return f"{moment:%H:%M}"
    return f"{moment:%d/%m %H:%M}"


def place_x_labels(
    ticks: Sequence[tuple[float, str]],
    *,
    start_text: str,
    end_text: str,
) -> list[tuple[float, str, str]]:
    """Pin the window end and keep every tick that still fits beside it.

    The end label is always at the right edge. A tick that overlaps it, another
    kept tick, or the plot box is dropped. The start label is then added at the
    left edge only when that does not overlap a kept tick or the end label, so
    a crowded left edge keeps the clock tick. Once placed, an edge label is
    never removed in favour of a tick.

    :param ticks: ``(percent, text)`` candidates, any order. Percents are 0..100.
    :param start_text: Window-start text (``HH:MM`` or ``dd/mm``).
    :param end_text: Window-end text, drawn at the right edge.
    :return: ``(percent, text, align)`` with ``align`` of ``start``, ``center`` or ``end``.
    """
    blocked: list[tuple[float, float]] = [_label_box(100.0, end_text, "end")]
    kept: list[tuple[float, str, str]] = []
    for percent, text in sorted(ticks):
        if not 0.0 < percent < 100.0:
            continue
        box = _label_box(percent, text, "center")
        if box[0] < 0.0 or box[1] > _PLOT_PX:
            continue
        if any(_overlaps(box, other) for other in blocked):
            continue
        kept.append((round(percent, 2), text, "center"))
        blocked.append(box)
    placed: list[tuple[float, str, str]] = []
    if start_text:
        start_box = _label_box(0.0, start_text, "start")
        if not any(_overlaps(start_box, other) for other in blocked):
            placed.append((0.0, start_text, "start"))
    placed.extend(kept)
    placed.append((100.0, end_text, "end"))
    return placed


def _cap_forced(ordered: Sequence[int], limit: int) -> list[int]:
    """Shrink a sorted forced index set down to ``limit``.

    :param ordered: Sorted unique indexes.
    :param limit: Maximum length (at least 1).
    :return: A subset that keeps the first and last indexes when possible.
    """
    if len(ordered) <= limit:
        return list(ordered)
    if limit == 1:
        return [ordered[-1]]
    last = len(ordered) - 1
    picked = {round(slot * last / (limit - 1)) for slot in range(limit)}
    return [ordered[index] for index in sorted(picked)]


def _take_evenly(pool: Sequence[int], slots: int, chosen: set[int]) -> None:
    """Add ``slots`` indexes from ``pool`` into ``chosen``, spread across it.

    :param pool: Remaining indexes, increasing.
    :param slots: How many to add.
    :param chosen: Set updated in place.
    """
    if slots <= 0 or not pool:
        return
    if slots >= len(pool):
        chosen.update(pool)
        return
    last = len(pool) - 1
    if slots == 1:
        chosen.add(pool[last // 2])
        return
    for slot in range(slots):
        chosen.add(pool[round(slot * last / (slots - 1))])


def _label_box(percent: float, text: str, align: str) -> tuple[float, float]:
    """Return one label's horizontal extent in plot pixels.

    :param percent: Anchor position, 0..100.
    :param text: Label text.
    :param align: ``start``, ``center`` or ``end``.
    :return: Left and right edges.
    """
    width = max(len(text), 1) * _CHAR_PX
    anchor = percent / 100.0 * _PLOT_PX
    if align == "end":
        return (anchor - width, anchor)
    if align == "start":
        return (anchor, anchor + width)
    return (anchor - width / 2.0, anchor + width / 2.0)


def _overlaps(box: tuple[float, float], other: tuple[float, float]) -> bool:
    """Tell whether two label boxes are closer than the gap budget.

    :param box: Left and right edges.
    :param other: Left and right edges.
    :return: ``True`` when they collide.
    """
    return box[1] + _LABEL_GAP_PX > other[0] and other[1] + _LABEL_GAP_PX > box[0]


def _tick_window(step: float, lo: float, hi: float) -> tuple[float, float, float] | None:
    """Return a snap-aligned window of height ``2 * step`` covering ``[lo, hi]``.

    :param step: Distance between ticks.
    :param lo: Padded minimum.
    :param hi: Padded maximum.
    :return: Min, mid and max, or ``None`` when no grid line fits.
    """
    lower = hi - 2 * step
    upper = lo
    if lower > upper + 1e-6:
        return None
    start = math.ceil((lower - 1e-9) / _SNAP) * _SNAP
    end = math.floor((upper + 1e-9) / _SNAP) * _SNAP
    if start > end + 1e-6:
        return None
    ideal = (lo + hi) / 2 - step
    count = int(round((end - start) / _SNAP))
    best = start
    best_distance = abs(start - ideal)
    for index in range(1, count + 1):
        candidate = start + index * _SNAP
        distance = abs(candidate - ideal)
        if distance < best_distance:
            best = candidate
            best_distance = distance
    tick_min = round(best, 4)
    if tick_min > lo + 1e-6 or tick_min + 2 * step < hi - 1e-6:
        return None
    return (tick_min, round(tick_min + step, 4), round(tick_min + 2 * step, 4))


def _clamp_percent(value: float, start: float, end: float) -> float:
    """Map ``value`` into a 0..100 percentage of ``[start, end]``.

    :param value: Sample position.
    :param start: Axis start.
    :param end: Axis end.
    :return: Clamped percentage.
    """
    span = end - start
    if span == 0:
        return 0.0
    raw = (value - start) / span * 100.0
    return min(100.0, max(0.0, raw))
