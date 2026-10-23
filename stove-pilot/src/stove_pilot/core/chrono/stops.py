"""Enabled chrono stop instants and whether one explains a panel stop.

Days follow :func:`stove_pilot.core.chrono.validation.effective_week`: enabled
blocks only, ``program.days`` when set, otherwise the block ``allowed_days``.
An overnight program (start later than stop) stops on the next calendar day,
as in ``validation._slots_by_day``. A stop-only program stops on its own days.
No stop minute, or a start equal to the stop, yields nothing.
"""

from collections.abc import Iterable
from datetime import date, datetime, timedelta
from typing import Final

from stove_pilot.core.chrono.models import ChronoConfig
from stove_pilot.core.chrono.validation import effective_week
from stove_pilot.utilities.weektime import MINUTES_PER_DAY, Weekday

# Reason: the poll that sees the stop may be a minute early, and the previous poll a minute late.
_EDGE_MIN: Final = 1


def _wall(moment: datetime) -> datetime:
    """Drop timezone info and keep the wall clock.

    :param moment: Aware or naive datetime.
    :return: The same clock fields without timezone info.
    """
    if moment.tzinfo is None:
        return moment
    return moment.replace(tzinfo=None)


def _previous_weekday(day: Weekday) -> Weekday:
    """Return the weekday before ``day`` (Monday wraps to Sunday).

    :param day: Weekday.
    :return: The previous weekday.
    """
    return Weekday((int(day) - 1) % len(Weekday))


def _dates_from(start: date, end: date) -> list[date]:
    """List calendar dates from ``start`` through ``end`` inclusive.

    :param start: First date.
    :param end: Last date.
    :return: The dates in order, or an empty list when ``end`` is before ``start``.
    """
    dates: list[date] = []
    day = start
    while day <= end:
        dates.append(day)
        day += timedelta(days=1)
    return dates


def _stop_at(day: date, stop_min: int | None) -> datetime | None:
    """Build a naive stop instant when the minute is a clock time.

    :param day: Calendar date of the stop.
    :param stop_min: Minutes since midnight, or ``None``.
    :return: The instant, or ``None`` when the minute cannot be stored.
    """
    # Reason: 24:00 cannot be stored, and a minute outside one day is not a stop.
    if stop_min is None or not 0 <= stop_min < MINUTES_PER_DAY:
        return None
    return datetime(day.year, day.month, day.day, stop_min // 60, stop_min % 60)


def _is_overnight(start_min: int | None, stop_min: int | None) -> bool:
    """Tell whether the stop falls on the day after the program day.

    :param start_min: Start minute, or ``None``.
    :param stop_min: Stop minute, or ``None``.
    :return: ``True`` when the start is later than the stop.
    """
    return start_min is not None and stop_min is not None and start_min > stop_min


def _is_same_day_stop(start_min: int | None, stop_min: int | None) -> bool:
    """Tell whether the stop falls on the program day.

    :param start_min: Start minute, or ``None``.
    :param stop_min: Stop minute, or ``None``.
    :return: ``True`` for a stop-only program or a start earlier than the stop.
    """
    if stop_min is None:
        return False
    # Reason: start == stop is rejected by validation; start > stop is overnight.
    if start_min is not None and start_min >= stop_min:
        return False
    return True


def _collect(config: ChronoConfig, dates: Iterable[date]) -> list[datetime]:
    """Collect enabled stop instants whose calendar date is in ``dates``.

    :param config: Chrono configuration.
    :param dates: Calendar dates to include.
    :return: Sorted naive instants. Two programs that share a minute both appear.
    """
    week = effective_week(config)
    found: list[datetime] = []
    for day in dates:
        weekday = Weekday(day.weekday())
        previous = _previous_weekday(weekday)
        for start_min, stop_min, _kind, _index in week[weekday]:
            if _is_same_day_stop(start_min, stop_min):
                instant = _stop_at(day, stop_min)
                if instant is not None:
                    found.append(instant)
        for start_min, stop_min, _kind, _index in week[previous]:
            if _is_overnight(start_min, stop_min):
                instant = _stop_at(day, stop_min)
                if instant is not None:
                    found.append(instant)
    found.sort()
    return found


def stop_instants(config: ChronoConfig, around: datetime) -> list[datetime]:
    """List enabled stop instants on the day before, of, and after ``around``.

    Results are naive and keep the wall clock of ``around``.

    :param config: Chrono configuration.
    :param around: Reference instant.
    :return: Sorted stop instants in that three-day window.
    """
    anchor = _wall(around).date()
    return _collect(config, _dates_from(anchor - timedelta(days=1), anchor + timedelta(days=1)))


def stop_matches(
    config: ChronoConfig, previous: datetime, current: datetime, window_min: int
) -> bool:
    """Tell whether an enabled chrono stop explains a transition to stopped.

    True only when a stop ``S`` has ``S - 1 min <= current <= S + window_min``
    and ``previous <= S + 1 min``. The previous poll must not already be past
    the stop. ``previous >= current`` or ``window_min < 1`` is false.

    :param config: Chrono configuration.
    :param previous: Wall clock of the last poll that still saw the stove running.
    :param current: Wall clock of the first poll that shows it stopped.
    :param window_min: Minutes after the stop that still count.
    :return: ``True`` when the chrono explains the stop.
    """
    if window_min < 1:
        return False
    # Reason: callers pass the stove-local wall clock; tzinfo must not change the match.
    previous_wall = _wall(previous)
    current_wall = _wall(current)
    if previous_wall >= current_wall:
        return False
    earliest = current_wall - timedelta(minutes=window_min)
    latest = current_wall + timedelta(minutes=_EDGE_MIN)
    edge = timedelta(minutes=_EDGE_MIN)
    span = timedelta(minutes=window_min)
    for stop in _collect(config, _dates_from(earliest.date(), latest.date())):
        in_window = stop - edge <= current_wall <= stop + span
        previous_not_past = previous_wall <= stop + edge
        if in_window and previous_not_past:
            return True
    return False
