"""Chrono models: blocks, programs, configuration, and validation results."""

from dataclasses import dataclass, field
from datetime import datetime, time
from enum import StrEnum
from typing import Literal

from stove_pilot.utilities.weektime import Weekday


class ChronoBlockKind(StrEnum):
    """The three independent chrono blocks of the stove."""

    DAY = "day"
    WEEK = "week"
    WEEKEND = "weekend"


@dataclass(frozen=True, slots=True)
class ChronoProgram:
    """One program; ``index`` is 1-based (P1, P2), ``None`` times mean unset (panel OFF)."""

    block: ChronoBlockKind
    index: int
    start_min: int | None
    stop_min: int | None
    days: frozenset[Weekday] | None
    setpoint_c: int
    power_level: int


@dataclass(frozen=True, slots=True)
class ChronoBlock:
    """One block with its enable flag and programs (``allowed_days`` is never empty)."""

    kind: ChronoBlockKind
    enabled: bool
    allowed_days: frozenset[Weekday]
    programs: tuple[ChronoProgram, ...]


@dataclass(frozen=True, slots=True)
class ChronoConfig:
    """The whole chrono; ``fingerprint`` identifies the raw bytes it was decoded from."""

    blocks: tuple[ChronoBlock, ...]
    fingerprint: str

    @property
    def enabled(self) -> bool:
        """Whether any block is enabled."""
        return any(block.enabled for block in self.blocks)


@dataclass(frozen=True, slots=True)
class ChronoIssue:
    """One validation finding; ``block`` and ``program`` are ``None`` when not specific."""

    code: str
    block: ChronoBlockKind | None
    program: int | None
    message: str


@dataclass(frozen=True, slots=True)
class ChronoValidation:
    """Blocking issues and non-blocking warnings."""

    issues: tuple[ChronoIssue, ...] = field(default_factory=tuple)
    warnings: tuple[ChronoIssue, ...] = field(default_factory=tuple)

    @property
    def ok(self) -> bool:
        """Whether there is no blocking issue."""
        return not self.issues


# Reason: wall-clock start, wall-clock stop (None when no stop exists), setpoint Celsius, power.
type CurrentProgram = tuple[time, time | None, float, int]


@dataclass(frozen=True, slots=True)
class ProgrammingSummary:
    """What the native chrono does now and next.

    ``next_at`` and ``current_until`` are aware, in the local timezone.
    ``next_action`` is ``start`` or ``stop`` when ``next_at`` is set.
    """

    active: bool
    current: CurrentProgram | None
    next_at: datetime | None
    blocked_reason: str | None
    next_action: Literal["start", "stop"] | None = None
    current_until: datetime | None = None
