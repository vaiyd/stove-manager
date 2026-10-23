"""Pure chrono validation and the effective week preview."""

from collections.abc import Iterator
from dataclasses import dataclass
from typing import Final

from stove_pilot.core.chrono.layout import ChronoLayout
from stove_pilot.core.chrono.models import (
    ChronoBlock,
    ChronoBlockKind,
    ChronoConfig,
    ChronoIssue,
    ChronoProgram,
    ChronoValidation,
)
from stove_pilot.utilities.weektime import MINUTES_PER_DAY, Weekday, format_hhmm

MIN_GAP_MIN: Final = 120

type WeekEntry = tuple[int | None, int | None, ChronoBlockKind, int]


@dataclass(frozen=True, slots=True)
class _Slot:
    """A fully set program span within one day (an overnight program gives two)."""

    start: int
    stop: int
    kind: ChronoBlockKind
    index: int


def _program_days(block: ChronoBlock, program: ChronoProgram) -> frozenset[Weekday]:
    """Return the days a program runs on.

    :param block: Block holding the program.
    :param program: The program.
    :return: Its own days, or the block's allowed days for the day block.
    """
    return block.allowed_days if program.days is None else program.days


def _is_set(program: ChronoProgram) -> bool:
    """Tell whether a program has at least one time.

    :param program: The program.
    :return: ``False`` when both times are unset.
    """
    return program.start_min is not None or program.stop_min is not None


def effective_week(config: ChronoConfig) -> dict[Weekday, list[WeekEntry]]:
    """List what runs each day (enabled blocks only, sorted by start time).

    :param config: Chrono configuration.
    :return: Per weekday, tuples ``(start_min, stop_min, block kind, 1-based program index)``.
    """
    week: dict[Weekday, list[WeekEntry]] = {day: [] for day in Weekday}
    for block in config.blocks:
        if not block.enabled:
            continue
        for program in block.programs:
            if not _is_set(program):
                continue
            entry = (program.start_min, program.stop_min, block.kind, program.index)
            for day in _program_days(block, program):
                week[day].append(entry)
    for entries in week.values():
        entries.sort(key=lambda e: (e[0] if e[0] is not None else (e[1] or 0), e[3]))
    return week


def _label(program: ChronoProgram) -> str:
    """Name a program in messages.

    :param program: The program.
    :return: Text such as ``week P1``.
    """
    return f"{program.block.value} P{program.index}"


def _issue(code: str, program: ChronoProgram, message: str) -> ChronoIssue:
    """Build an issue attached to a program.

    :param code: Issue code.
    :param program: The program concerned.
    :param message: Human-readable message.
    :return: The issue.
    """
    return ChronoIssue(code, program.block, program.index, message)


def _time_issues(program: ChronoProgram, unit: int) -> Iterator[ChronoIssue]:
    """Check times: range, granularity, and start different from stop.

    :param program: The program.
    :param unit: Time unit in minutes.
    :return: The issues found.
    """
    for name, value in (("start", program.start_min), ("stop", program.stop_min)):
        if value is None:
            continue
        if not 0 <= value < MINUTES_PER_DAY:
            yield _issue(
                "time_range",
                program,
                f"{_label(program)} {name} must be 00:00-23:50 (24:00 cannot be stored)",
            )
        elif value % unit:
            yield _issue(
                "granularity", program, f"{_label(program)} {name} must be a multiple of {unit} min"
            )
    # Reason: start and stop are independent daily events; start > stop is a valid overnight program.
    if program.start_min is not None and program.start_min == program.stop_min:
        yield _issue("order", program, f"{_label(program)} start and stop must differ")


def _value_issues(
    program: ChronoProgram, setpoint_range: tuple[int, int], power_range: tuple[int, int]
) -> Iterator[ChronoIssue]:
    """Check setpoint and power ranges.

    :param program: The program.
    :param setpoint_range: Inclusive ``(min, max)`` setpoint.
    :param power_range: Inclusive ``(min, max)`` power level.
    :return: The issues found.
    """
    if not setpoint_range[0] <= program.setpoint_c <= setpoint_range[1]:
        yield _issue(
            "setpoint_range",
            program,
            f"{_label(program)} setpoint {program.setpoint_c} outside {setpoint_range}",
        )
    if not power_range[0] <= program.power_level <= power_range[1]:
        yield _issue(
            "power_range",
            program,
            f"{_label(program)} power {program.power_level} outside {power_range}",
        )


def _day_issues(block: ChronoBlock, program: ChronoProgram) -> Iterator[ChronoIssue]:
    """Check the program days against the block's allowed days.

    :param block: Block holding the program.
    :param program: The program.
    :return: The issues found.
    """
    if program.days is None:
        return
    extra = sorted(program.days - block.allowed_days)
    if extra:
        names = ",".join(day.key for day in extra)
        yield _issue("days_not_allowed", program, f"{_label(program)} days not allowed: {names}")


def _program_slots(
    day: Weekday, start: int, stop: int, kind: ChronoBlockKind, index: int
) -> Iterator[tuple[Weekday, _Slot]]:
    """Split a fully set program starting on ``day`` into per-day slots.

    :param day: Day the program starts on.
    :param start: Start minute.
    :param stop: Stop minute; before ``start`` means the stop is on the next day.
    :param kind: Block kind.
    :param index: 1-based program index.
    :return: ``(day, slot)`` pairs; an overnight program also yields ``(next day, 0..stop)``.
    """
    if start < stop:
        yield day, _Slot(start, stop, kind, index)
        return
    yield day, _Slot(start, MINUTES_PER_DAY, kind, index)
    if stop > 0:
        yield Weekday((day + 1) % len(Weekday)), _Slot(0, stop, kind, index)


def _slots_by_day(config: ChronoConfig) -> dict[Weekday, list[_Slot]]:
    """Collect the fully set programs of enabled blocks per day (overnight ones split at midnight).

    :param config: Chrono configuration.
    :return: Slots per weekday, sorted by start.
    """
    slots: dict[Weekday, list[_Slot]] = {day: [] for day in Weekday}
    for block in config.blocks:
        if not block.enabled:
            continue
        for program in block.programs:
            start, stop = program.start_min, program.stop_min
            if start is None or stop is None or start == stop:
                continue
            for day in _program_days(block, program):
                for slot_day, slot in _program_slots(day, start, stop, block.kind, program.index):
                    slots[slot_day].append(slot)
    for day_slots in slots.values():
        day_slots.sort(key=lambda s: (s.start, s.stop))
    return slots


def _program_key(slot: _Slot) -> tuple[ChronoBlockKind, int]:
    """Identify the program a slot belongs to.

    :param slot: A slot.
    :return: ``(block kind, program index)``.
    """
    return slot.kind, slot.index


def _overlap_issues(config: ChronoConfig) -> Iterator[ChronoIssue]:
    """Detect overlapping programs of enabled blocks on the same day.

    :param config: Chrono configuration.
    :return: One issue per overlapping pair, attached to the later program.
    """
    seen: set[frozenset[tuple[ChronoBlockKind, int]]] = set()
    for day, slots in _slots_by_day(config).items():
        for i, first in enumerate(slots):
            for second in slots[i + 1 :]:
                if second.start >= first.stop:
                    break
                pair = frozenset((_program_key(first), _program_key(second)))
                # Reason: the two halves of an overnight program never conflict with themselves.
                if len(pair) == 1 or pair in seen:
                    continue
                seen.add(pair)
                yield ChronoIssue(
                    "overlap",
                    second.kind,
                    second.index,
                    f"{second.kind.value} P{second.index} overlaps "
                    f"{first.kind.value} P{first.index} (first on {day.key})",
                )


def _gap_warnings(config: ChronoConfig) -> Iterator[ChronoIssue]:
    """Warn when a stop and the next start on the same day are less than 2 h apart.

    :param config: Chrono configuration.
    :return: One warning per program pair.
    """
    seen: set[tuple[tuple[ChronoBlockKind, int], tuple[ChronoBlockKind, int]]] = set()
    for day, slots in _slots_by_day(config).items():
        for first, second in zip(slots, slots[1:], strict=False):
            gap = second.start - first.stop
            pair = (_program_key(first), _program_key(second))
            if 0 <= gap < MIN_GAP_MIN and pair not in seen:
                seen.add(pair)
                yield ChronoIssue(
                    "gap_short",
                    second.kind,
                    second.index,
                    f"Only {gap} min between {first.kind.value} P{first.index} stop and "
                    f"{second.kind.value} P{second.index} start (first on {day.key})",
                )



def _overnight_warnings(block: ChronoBlock) -> Iterator[ChronoIssue]:
    """Warn about programs that stop on the day after they start.

    :param block: An enabled block.
    :return: One warning per overnight program.
    """
    for program in block.programs:
        start, stop = program.start_min, program.stop_min
        if start is None or stop is None or not 0 <= stop < start < MINUTES_PER_DAY:
            continue
        yield _issue(
            "overnight",
            program,
            f"{_label(program)} runs past midnight: on at {format_hhmm(start)}, "
            f"off at {format_hhmm(stop)} the next day",
        )


def _off_warnings(block: ChronoBlock) -> Iterator[ChronoIssue]:
    """Warn about programs with only a start or only a stop.

    :param block: An enabled block.
    :return: The warnings found.
    """
    for program in block.programs:
        if program.start_min is None and program.stop_min is not None:
            yield _issue("start_off", program, f"{_label(program)} start is OFF")
        elif program.stop_min is None and program.start_min is not None:
            yield _issue("stop_off", program, f"{_label(program)} stop is OFF")


def validate_chrono(
    config: ChronoConfig,
    layout: ChronoLayout,
    setpoint_range: tuple[int, int],
    power_range: tuple[int, int],
) -> ChronoValidation:
    """Validate a configuration; programs with both times unset are ignored.

    :param config: Chrono configuration.
    :param layout: Chrono layout (time unit, verification flags, ``allow_unverified``).
    :param setpoint_range: Inclusive ``(min, max)`` setpoint.
    :param power_range: Inclusive ``(min, max)`` power level.
    :return: Blocking issues and non-blocking warnings.
    """
    issues: list[ChronoIssue] = []
    warnings: list[ChronoIssue] = []
    if not layout.allow_unverified:
        issues.extend(
            ChronoIssue(
                "unverified_layout", block.kind, None, f"{block.kind.value} layout is not verified"
            )
            for block in layout.blocks
            if not block.verified
        )
    for block in config.blocks:
        for program in filter(_is_set, block.programs):
            issues.extend(_time_issues(program, layout.time_unit_min))
            issues.extend(_value_issues(program, setpoint_range, power_range))
            issues.extend(_day_issues(block, program))
        if block.enabled:
            warnings.extend(_off_warnings(block))
            warnings.extend(_overnight_warnings(block))
    issues.extend(_overlap_issues(config))
    warnings.extend(_gap_warnings(config))
    return ChronoValidation(tuple(issues), tuple(warnings))
