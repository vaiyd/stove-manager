"""Pure computation of the programming summary from a chrono configuration."""

from dataclasses import dataclass
from datetime import datetime, time, timedelta
from typing import Final, Literal

from stove_pilot.core.chrono.models import (
    ChronoBlockKind,
    ChronoConfig,
    ChronoProgram,
    CurrentProgram,
    ProgrammingSummary,
)
from stove_pilot.core.chrono.validation import effective_week
from stove_pilot.utilities.weektime import MINUTES_PER_DAY, MINUTES_PER_WEEK, minute_of_week

NEXT_HORIZON_DAYS: Final = 8

type _Action = Literal["start", "stop"]

_ACTION_RANK: Final[dict[_Action, int]] = {"stop": 0, "start": 1}
_BLOCK_RANK: Final[dict[ChronoBlockKind, int]] = {
    ChronoBlockKind.DAY: 0,
    ChronoBlockKind.WEEK: 1,
    ChronoBlockKind.WEEKEND: 2,
}


@dataclass(frozen=True, slots=True)
class _Event:
    """One start or stop (``at`` is the minute since Monday 00:00)."""

    at: int
    action: _Action
    program: ChronoProgram


@dataclass(frozen=True, slots=True)
class _Window:
    """An on-span ``[start_at, stop_at)``; ``stop_at`` may pass the end of the week."""

    start_at: int
    stop_at: int
    program: ChronoProgram
    open_ended: bool


def _program(config: ChronoConfig, kind: ChronoBlockKind, index: int) -> ChronoProgram:
    """Find a program by block kind and 1-based index.

    :param config: Chrono configuration.
    :param kind: Block kind.
    :param index: 1-based program index.
    :return: The program.
    """
    block = next(block for block in config.blocks if block.kind == kind)
    return block.programs[index - 1]


def _event_key(event: _Event) -> tuple[int, int, int, int]:
    """Sort key: time, then stop before start, then block and program index.

    :param event: Week event.
    :return: The sort key.
    """
    return (
        event.at,
        _ACTION_RANK[event.action],
        _BLOCK_RANK[event.program.block],
        event.program.index,
    )


def _events(config: ChronoConfig) -> list[_Event]:
    """List start and stop events of enabled blocks (disabled blocks are absent).

    :param config: Chrono configuration.
    :return: Events sorted by :func:`_event_key`.
    """
    events: list[_Event] = []
    for day, entries in effective_week(config).items():
        base = int(day) * MINUTES_PER_DAY
        for start, stop, kind, index in entries:
            program = _program(config, kind, index)
            if stop is not None:
                events.append(_Event(base + stop, "stop", program))
            if start is not None:
                events.append(_Event(base + start, "start", program))
    events.sort(key=_event_key)
    return events


def _windows(events: list[_Event]) -> list[_Window]:
    """Build on-spans the way the editor preview does.

    A start opens a span only when the previous cyclic event is a stop, and the span runs
    until the next stop. Starts with no stop anywhere stay on for the whole week.

    :param events: Week events sorted by :func:`_event_key`.
    :return: On-spans; empty when nothing turns the stove on.
    """
    if not events:
        return []
    first_start = next((event for event in events if event.action == "start"), None)
    if first_start is None:
        return []
    if all(event.action != "stop" for event in events):
        return [
            _Window(first_start.at, first_start.at + MINUTES_PER_WEEK, first_start.program, True)
        ]
    windows: list[_Window] = []
    count = len(events)
    for index, opener in enumerate(events):
        previous = events[(index - 1) % count]
        if opener.action != "start" or previous.action != "stop":
            continue
        step = 1
        while events[(index + step) % count].action != "stop":
            step += 1
        closer = events[(index + step) % count]
        stop_at = closer.at + (MINUTES_PER_WEEK if index + step >= count else 0)
        windows.append(_Window(opener.at, stop_at, opener.program, False))
    return windows


def _covers(window: _Window, now_min: int) -> bool:
    """Tell whether a minute of the week is inside the half-open span.

    :param window: On-span.
    :param now_min: Minute since Monday 00:00.
    :return: ``True`` when the minute is covered.
    """
    if window.stop_at <= MINUTES_PER_WEEK:
        return window.start_at <= now_min < window.stop_at
    end = window.stop_at - MINUTES_PER_WEEK
    return now_min >= window.start_at or now_min < end


def _elapsed(start_at: int, now_min: int) -> int:
    """Minutes since a start, wrapping across Monday.

    :param start_at: Start minute of the week.
    :param now_min: Current minute of the week.
    :return: Elapsed minutes.
    """
    if start_at <= now_min:
        return now_min - start_at
    return now_min + MINUTES_PER_WEEK - start_at


def _covering(windows: list[_Window], now_min: int) -> _Window | None:
    """Pick the window covering ``now_min`` (the one that started earliest wins).

    :param windows: On-spans of the enabled programs.
    :param now_min: Minute of the week.
    :return: The winning window, or ``None``.
    """
    chosen: _Window | None = None
    chosen_key: tuple[int, int, int] | None = None
    for window in windows:
        if not _covers(window, now_min):
            continue
        key = (
            -_elapsed(window.start_at, now_min),
            _BLOCK_RANK[window.program.block],
            window.program.index,
        )
        if chosen_key is None or key < chosen_key:
            chosen = window
            chosen_key = key
    return chosen


def _clock(minute: int) -> time:
    """Convert a minute index to a time of day.

    :param minute: Minute since Monday 00:00 (reduced modulo one day).
    :return: The time of day.
    """
    minute_of_day = minute % MINUTES_PER_DAY
    return time(minute_of_day // 60, minute_of_day % 60)


def _week_origin(local: datetime) -> datetime:
    """Return Monday 00:00 of the week containing ``local``.

    :param local: Local time (timezone is preserved).
    :return: Monday at midnight.
    """
    midnight = local.replace(hour=0, minute=0, second=0, microsecond=0)
    return midnight - timedelta(days=midnight.weekday())


def _window_end(local: datetime, window: _Window) -> datetime | None:
    """Return the aware end of the window that covers ``local``.

    :param local: Aware local time inside the window.
    :param window: Covering on-span.
    :return: The stop instant, or ``None`` when the window has no stop.
    """
    if window.open_ended:
        return None
    origin = _week_origin(local)
    if window.stop_at <= MINUTES_PER_WEEK or minute_of_week(local) >= window.start_at:
        return origin + timedelta(minutes=window.stop_at)
    return origin + timedelta(minutes=window.stop_at - MINUTES_PER_WEEK)


def _current_program(window: _Window) -> CurrentProgram:
    """Map a window to the summary tuple (stop is ``None`` when nothing turns the stove off).

    :param window: Covering on-span.
    :return: Start clock, stop clock, setpoint and power of the program that opened the span.
    """
    start = _clock(window.start_at)
    stop = None if window.open_ended else _clock(window.stop_at)
    return start, stop, float(window.program.setpoint_c), window.program.power_level


def _next_event(events: list[_Event], local: datetime) -> tuple[datetime, _Action] | None:
    """Find the first event strictly after ``local`` within the horizon.

    A stop sorts before a start that shares its minute.

    :param events: Week events.
    :param local: Aware local time.
    :return: Aware instant and ``start`` or ``stop``, or ``None``.
    """
    if not events:
        return None
    origin = _week_origin(local)
    horizon = local + timedelta(days=NEXT_HORIZON_DAYS)
    best_at: datetime | None = None
    best_action: _Action = "start"
    best_rank = len(_ACTION_RANK)
    for week in range(2):
        base = origin + timedelta(weeks=week)
        for event in events:
            candidate = base + timedelta(minutes=event.at)
            if candidate <= local or candidate > horizon:
                continue
            rank = _ACTION_RANK[event.action]
            earlier = best_at is None or candidate < best_at
            same_minute_stop = candidate == best_at and rank < best_rank
            if earlier or same_minute_stop:
                best_at = candidate
                best_action = event.action
                best_rank = rank
    if best_at is None:
        return None
    return best_at, best_action


def compute_summary(
    config: ChronoConfig, local: datetime, blocked_reason: str | None
) -> ProgrammingSummary:
    """Summarize what the chrono does at ``local``.

    Enabled blocks only. A set start or stop is an event on each of its days, repeated
    every week. The stove is on from a start that follows a stop until the next stop; if
    any start exists and no stop does, it stays on all week. Overlapping windows keep the
    one that started earliest. The next event is the first start or stop strictly after
    ``local``. No event at all means nothing is programmed.

    A stop belongs to the days selected for its own program: it never fires on an
    unselected day, so an overnight program limited to some days can stay on until the
    next selected stop. This differs from ``validation._slots_by_day`` (overnight split into
    two day halves); the event reading is the accepted one for the stove.

    :param config: Chrono configuration.
    :param local: Aware time in the local timezone.
    :param blocked_reason: Machine-readable reason programming is blocked, if any.
    :return: Current window, next event and the blocked reason.
    """
    events = _events(config)
    window = _covering(_windows(events), minute_of_week(local))
    upcoming = _next_event(events, local)
    return ProgrammingSummary(
        active=window is not None,
        current=None if window is None else _current_program(window),
        next_at=None if upcoming is None else upcoming[0],
        blocked_reason=blocked_reason,
        next_action=None if upcoming is None else upcoming[1],
        current_until=None if window is None else _window_end(local, window),
    )
