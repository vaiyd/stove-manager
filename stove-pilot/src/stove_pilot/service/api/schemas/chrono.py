"""Chrono DTOs: the configuration, its write request and the shared programming summary mapping."""

from typing import Final, Literal, Self

from pydantic import BaseModel, field_validator

from stove_pilot.common.exceptions import ValidationFailedError
from stove_pilot.core.chrono.models import (
    ChronoBlock,
    ChronoBlockKind,
    ChronoConfig,
    ChronoIssue,
    ChronoProgram,
    ChronoValidation,
    ProgrammingSummary,
)
from stove_pilot.service.api.schemas.common import RequestModel
from stove_pilot.service.api.schemas.summary import (
    CurrentProgramDto,
    NextProgramDto,
    ProgrammingSummaryDto,
)
from stove_pilot.utilities.weektime import Weekday, format_hhmm, parse_hhmm

DayKey = Literal["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
BlockKey = Literal["day", "week", "weekend"]

FEATURE_NAME: Final = "chrono"
FEATURE_LABEL: Final = "Chrono poele"
_ACTIONS: Final = frozenset({"start", "stop"})


def _day_keys(days: frozenset[Weekday]) -> list[str]:
    """List weekday keys in week order.

    :param days: Weekdays.
    :return: Keys such as ``"mon"``.
    """
    return [day.key for day in sorted(days)]


class ChronoProgramDto(BaseModel):
    """One program; ``start``/``stop`` are ``HH:MM`` or ``null`` (OFF); ``days`` is ``null`` for day."""

    index: int
    start: str | None
    stop: str | None
    days: list[str] | None
    setpoint_c: int
    power_level: int

    @classmethod
    def from_domain(cls, program: ChronoProgram) -> Self:
        """Map a program.

        :param program: Domain program.
        :return: The DTO.
        """
        return cls(
            index=program.index,
            start=None if program.start_min is None else format_hhmm(program.start_min),
            stop=None if program.stop_min is None else format_hhmm(program.stop_min),
            days=None if program.days is None else _day_keys(program.days),
            setpoint_c=program.setpoint_c,
            power_level=program.power_level,
        )


class ChronoBlockDto(BaseModel):
    """One chrono block with its enable flag and programs."""

    kind: str
    enabled: bool
    allowed_days: list[str]
    programs: list[ChronoProgramDto]

    @classmethod
    def from_domain(cls, block: ChronoBlock) -> Self:
        """Map a block.

        :param block: Domain block.
        :return: The DTO.
        """
        return cls(
            kind=block.kind.value,
            enabled=block.enabled,
            allowed_days=_day_keys(block.allowed_days),
            programs=[ChronoProgramDto.from_domain(p) for p in block.programs],
        )


class ChronoWarningDto(BaseModel):
    """A non-blocking finding (``block`` and ``index`` are ``null`` when not specific)."""

    code: str
    block: str | None
    index: int | None
    message: str

    @classmethod
    def from_domain(cls, issue: ChronoIssue) -> Self:
        """Map a warning.

        :param issue: Domain warning.
        :return: The DTO.
        """
        block = None if issue.block is None else issue.block.value
        return cls(code=issue.code, block=block, index=issue.program, message=issue.message)


class RangeDto(BaseModel):
    """An inclusive integer range."""

    min: int
    max: int


class ChronoCapabilitiesDto(BaseModel):
    """Value ranges of the stove and whether the layout can be written."""

    setpoint_range: RangeDto
    power_range: RangeDto
    writable: bool


class ChronoConfigDto(BaseModel):
    """The whole chrono (the ``ETag`` header carries its fingerprint)."""

    enabled: bool
    blocks: list[ChronoBlockDto]
    warnings: list[ChronoWarningDto]
    capabilities: ChronoCapabilitiesDto

    @classmethod
    def from_domain(
        cls,
        config: ChronoConfig,
        validation: ChronoValidation,
        *,
        setpoint_range: tuple[int, int],
        power_range: tuple[int, int],
        writable: bool,
    ) -> Self:
        """Map a configuration with its validation warnings.

        :param config: Domain configuration.
        :param validation: Validation result of ``config``.
        :param setpoint_range: Inclusive setpoint range.
        :param power_range: Inclusive power level range.
        :param writable: Whether the layout can be written.
        :return: The DTO.
        """
        return cls(
            enabled=config.enabled,
            blocks=[ChronoBlockDto.from_domain(block) for block in config.blocks],
            warnings=[ChronoWarningDto.from_domain(issue) for issue in validation.warnings],
            capabilities=ChronoCapabilitiesDto(
                setpoint_range=RangeDto(min=setpoint_range[0], max=setpoint_range[1]),
                power_range=RangeDto(min=power_range[0], max=power_range[1]),
                writable=writable,
            ),
        )


class ChronoProgramRequest(RequestModel):
    """A program to store; ``HH:MM`` times (``24:00`` is accepted here and refused by the core)."""

    index: int
    start: str | None
    stop: str | None
    days: list[DayKey] | None = None
    setpoint_c: int
    power_level: int

    @field_validator("start", "stop")
    @classmethod
    def _check_time(cls, value: str | None) -> str | None:
        if value is not None:
            parse_hhmm(value)
        return value

    def to_domain(self, kind: ChronoBlockKind) -> ChronoProgram:
        """Map to a domain program.

        :param kind: Block holding the program.
        :return: The domain program.
        """
        return ChronoProgram(
            block=kind,
            index=self.index,
            start_min=None if self.start is None else parse_hhmm(self.start),
            stop_min=None if self.stop is None else parse_hhmm(self.stop),
            days=None if self.days is None else frozenset(Weekday.from_key(k) for k in self.days),
            setpoint_c=self.setpoint_c,
            power_level=self.power_level,
        )


class ChronoBlockRequest(RequestModel):
    """A block to store; ``allowed_days`` is read-only (the layout owns it) and ignored."""

    kind: BlockKey
    enabled: bool
    allowed_days: list[DayKey] | None = None
    programs: list[ChronoProgramRequest]


class ChronoConfigRequest(RequestModel):
    """Body of ``PUT /chrono``: the config DTO without ``warnings``/``capabilities``."""

    enabled: bool | None = None
    blocks: list[ChronoBlockRequest]

    def to_domain(self, current: ChronoConfig) -> ChronoConfig:
        """Map to a domain configuration (``enabled`` is derived, ``allowed_days`` is the layout's).

        :param current: Configuration last read (supplies the allowed days and block set).
        :return: The configuration to save.
        :raises ValidationFailedError: Blocks or program indexes do not match the layout.
        """
        known = {block.kind: block for block in current.blocks}
        kinds = [ChronoBlockKind(block.kind) for block in self.blocks]
        if sorted(kinds) != sorted(known):
            wanted = ", ".join(kind.value for kind in known)
            raise ValidationFailedError([f"blocks must list each of {wanted} exactly once"])
        blocks = []
        for request, kind in zip(self.blocks, kinds, strict=True):
            layout_block = known[kind]
            programs = sorted(request.programs, key=lambda p: p.index)
            if [p.index for p in programs] != [p.index for p in layout_block.programs]:
                last = len(layout_block.programs)
                raise ValidationFailedError([f"{kind.value} programs must have indexes 1..{last}"])
            blocks.append(
                ChronoBlock(
                    kind,
                    request.enabled,
                    layout_block.allowed_days,
                    tuple(p.to_domain(kind) for p in programs),
                )
            )
        return ChronoConfig(tuple(blocks), current.fingerprint)


class BlockStateRequest(RequestModel):
    """Body of ``PUT /chrono/blocks/<kind>/state``."""

    enabled: bool


class FingerprintDto(BaseModel):
    """Cheap change detection token of the chrono."""

    fingerprint: str


def issue_text(issue: object) -> str:
    """Render a validation issue as one line (``code: message`` for chrono issues).

    :param issue: ``ChronoIssue`` or any object with a readable ``str``.
    :return: The text.
    """
    if isinstance(issue, ChronoIssue):
        return f"{issue.code}: {issue.message}"
    return str(issue)


def programming_summary_dto(summary: ProgrammingSummary) -> ProgrammingSummaryDto:
    """Map the chrono summary to the shared programming summary DTO.

    :param summary: Domain summary.
    :return: The DTO (label ``Chrono poele``).
    """
    current = None
    if summary.current is not None:
        start, stop, setpoint_c, power_level = summary.current
        current = CurrentProgramDto(
            start=start.strftime("%H:%M"),
            stop=start.strftime("%H:%M") if stop is None else stop.strftime("%H:%M"),
            setpoint_c=setpoint_c,
            power_level=power_level,
            open_ended=stop is None,
            stop_at=summary.current_until,
        )
    upcoming = None
    if summary.next_at is not None:
        action = summary.next_action if summary.next_action in _ACTIONS else "start"
        upcoming = NextProgramDto(at=summary.next_at, action=action)
    return ProgrammingSummaryDto(
        feature=FEATURE_NAME,
        label=FEATURE_LABEL,
        active=summary.active,
        current=current,
        next=upcoming,
        blocked_reason=summary.blocked_reason,
    )
