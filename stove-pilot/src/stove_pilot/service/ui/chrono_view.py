"""Chrono page view models built from API payloads (no I/O, French texts via ``t``)."""

import re
from dataclasses import dataclass
from typing import Any, Final, Self

from stove_pilot.service.ui.i18n import has_key, t

BLOCK_ORDER: Final = ("day", "week", "weekend")
DAY_KEYS: Final = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
_ISSUE_PATTERN: Final = re.compile(r"^([a-z_]+): (.*)$", re.DOTALL)
_LABEL_PATTERN: Final = re.compile(r"^(day|week|weekend) P(\d+)")


def program_label(block: str, index: int) -> str:
    """Name a program for a message.

    :param block: Block kind.
    :param index: 1-based program index.
    :return: For example ``Semaine P1``.
    """
    return f"{t(f'chrono.block.{block}')} P{index}"


def _describe(
    code: str, block: str | None, index: int | None, values: dict[str, str] | None = None
) -> str:
    """Build the French text of an issue or warning code.

    :param code: Issue code (unknown codes get a generic text).
    :param block: Block kind, if the issue targets a program.
    :param index: Program index, if the issue targets a program.
    :param values: Placeholder values of the text (for example the times of ``overnight``).
    :return: The French sentence.
    """
    key = f"chrono.issue.{code}"
    text = (t(key, **(values or {})) if has_key(key) else t("chrono.issue.unknown"))
    if block in BLOCK_ORDER and index is not None:
        return f"{program_label(str(block), index)} : {text}"
    return text[:1].upper() + text[1:]


def issue_text_fr(raw: str) -> str:
    """Translate a ``code: message`` issue string of a ``422`` answer.

    :param raw: Issue text from the API.
    :return: The French sentence.
    """
    match = _ISSUE_PATTERN.match(raw)
    code, message = (match.group(1), match.group(2)) if match else ("", raw)
    label = _LABEL_PATTERN.match(message)
    if label is None:
        return _describe(code, None, None)
    return _describe(code, label.group(1), int(label.group(2)))


@dataclass(frozen=True, slots=True)
class DayChip:
    """One day chip of a program."""

    key: str
    label: str
    selected: bool


@dataclass(frozen=True, slots=True)
class ProgramView:
    """One program row (``chips`` is empty for the day block)."""

    index: int
    title: str
    start: str
    stop: str
    chips: tuple[DayChip, ...]
    setpoint: int
    power: int
    setpoint_options: tuple[int, ...]
    power_options: tuple[int, ...]

    @property
    def no_start(self) -> bool:
        """Whether the start is unset (OFF)."""
        return not self.start

    @property
    def no_stop(self) -> bool:
        """Whether the stop is unset (OFF)."""
        return not self.stop


@dataclass(frozen=True, slots=True)
class BlockView:
    """One block card."""

    kind: str
    title: str
    enabled: bool
    programs: tuple[ProgramView, ...]


def _options(value: int, bounds: dict[str, Any]) -> tuple[int, ...]:
    """List the select values: the inclusive range plus the current value.

    :param value: Current value (kept even when out of range, so it is not silently changed).
    :param bounds: ``RangeDto`` payload.
    :return: Sorted values.
    """
    return tuple(sorted({*range(int(bounds["min"]), int(bounds["max"]) + 1), value}))


def _program(payload: dict[str, Any], allowed: list[str], caps: dict[str, Any]) -> ProgramView:
    """Map a program payload.

    :param payload: ``ChronoProgramDto`` payload.
    :param allowed: Day keys of the block (chips are built only for blocks with a day mask).
    :param caps: ``capabilities`` payload.
    :return: The view.
    """
    days = payload.get("days")
    chips = ()
    if days is not None:
        chips = tuple(DayChip(key, t(f"chrono.day.{key}"), key in days) for key in allowed)
    setpoint, power = int(payload["setpoint_c"]), int(payload["power_level"])
    return ProgramView(
        index=int(payload["index"]),
        title=t("chrono.program", n=payload["index"]),
        start=payload.get("start") or "",
        stop=payload.get("stop") or "",
        chips=chips,
        setpoint=setpoint,
        power=power,
        setpoint_options=_options(setpoint, caps["setpoint_range"]),
        power_options=_options(power, caps["power_range"]),
    )


@dataclass(frozen=True, slots=True)
class ChronoView:
    """The whole chrono editor."""

    etag: str
    blocks: tuple[BlockView, ...]
    warnings: tuple[str, ...]
    writable: bool
    suspended: bool
    day_choices: tuple[tuple[str, str], ...]

    @classmethod
    def from_api(cls, payload: dict[str, Any], etag: str | None, suspended: bool) -> Self:
        """Map a ``ChronoConfigDto`` payload.

        :param payload: Chrono payload.
        :param etag: ``ETag`` header of the payload (the loaded version).
        :param suspended: Whether programming is suspended.
        :return: The view (blocks in day, week, weekend order).
        """
        caps = payload["capabilities"]
        by_kind = {block["kind"]: block for block in payload["blocks"]}
        blocks = tuple(
            BlockView(
                kind=kind,
                title=t(f"chrono.block.{kind}"),
                enabled=bool(by_kind[kind]["enabled"]),
                programs=tuple(
                    _program(program, by_kind[kind]["allowed_days"], caps)
                    for program in by_kind[kind]["programs"]
                ),
            )
            for kind in BLOCK_ORDER
            if kind in by_kind
        )
        times = {
            (block["kind"], program["index"]): {
                "start": program.get("start") or "",
                "stop": program.get("stop") or "",
            }
            for block in payload["blocks"]
            for program in block["programs"]
        }
        warnings = tuple(
            _describe(
                str(w["code"]),
                w.get("block"),
                w.get("index"),
                times.get((w.get("block"), w.get("index"))),
            )
            for w in payload["warnings"]
        )
        return cls(
            etag=etag or "",
            blocks=blocks,
            warnings=warnings,
            writable=bool(caps["writable"]),
            suspended=suspended,
            day_choices=tuple((key, t(f"chrono.day.{key}")) for key in DAY_KEYS),
        )


def js_texts() -> dict[str, Any]:
    """Build the French texts used by ``chrono-editor.js`` and ``chrono-preview.js``.

    :return: JSON-serializable mapping.
    """
    return {
        "blocks": {kind: t(f"chrono.block.{kind}") for kind in BLOCK_ORDER},
        "days": {key: t(f"chrono.day.{key}") for key in DAY_KEYS},
        "gap_short": t("chrono.warn.gap_short", first="{first}", second="{second}", gap="{gap}"),
        "start_off": t("chrono.warn.start_off", label="{label}"),
        "stop_off": t("chrono.warn.stop_off", label="{label}"),
        "overnight": t("chrono.warn.overnight", label="{label}", start="{start}", stop="{stop}"),
        "overlap": t("chrono.warn.overlap", first="{first}", second="{second}"),
        "preview_empty": t("chrono.preview_empty"),
        "preview_tip": t(
            "chrono.preview_tip",
            start="{start}",
            stop="{stop}",
            block="{block}",
            setpoint="{setpoint}",
            power="{power}",
        ),
        "preview_open_end": t("chrono.preview_open_end"),
        "preview_range": t("chrono.preview_range", begin="{begin}", end="{end}"),
        "preview_and": t("chrono.preview_and"),
        "preview_day_on": t("chrono.preview_day_on", day="{day}", ranges="{ranges}"),
        "preview_day_full": t("chrono.preview_day_full", day="{day}"),
        "preview_day_off": t("chrono.preview_day_off", day="{day}"),
        "preview_overlap": t("chrono.preview_overlap", programs="{programs}"),
        "preview_overlap_aria": t("chrono.preview_overlap_aria", ranges="{ranges}"),
    }
