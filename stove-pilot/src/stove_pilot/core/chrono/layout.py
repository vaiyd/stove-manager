"""Chrono layout loader (TOML package data) plus pure decode and encode of the raw bytes."""

import hashlib
import re
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from importlib.resources import files
from typing import Any, Final

from stove_pilot.common.exceptions import ConfigurationError
from stove_pilot.core.chrono.models import (
    ChronoBlock,
    ChronoBlockKind,
    ChronoConfig,
    ChronoProgram,
)
from stove_pilot.utilities.weektime import Weekday, days_from_mask, days_to_mask

_LAYOUTS_PACKAGE: Final = "stove_pilot.core.chrono.layouts"
_NAME_PATTERN: Final = re.compile(r"[a-z0-9_]+")
_FINGERPRINT_LENGTH: Final = 16
_ALL_DAYS: Final = frozenset(Weekday)


@dataclass(frozen=True, slots=True)
class ProgramLayout:
    """EEPROM addresses of one program (``days`` is ``None`` for the day block)."""

    start: int
    stop: int
    power: int
    setpoint: int
    days: int | None = None

    def addresses(self) -> list[int]:
        """List the addresses of this program.

        :return: Addresses in field order.
        """
        found = [self.start, self.stop, self.power, self.setpoint]
        return found if self.days is None else [*found, self.days]


@dataclass(frozen=True, slots=True)
class BlockLayout:
    """EEPROM layout of one block (``allowed_days`` is resolved, never empty)."""

    kind: ChronoBlockKind
    enable: int
    allowed_days: frozenset[Weekday]
    programs: tuple[ProgramLayout, ...]
    verified: bool

    def addresses(self) -> list[int]:
        """List the addresses of this block.

        :return: Enable address followed by the program addresses.
        """
        return [self.enable, *(a for program in self.programs for a in program.addresses())]


@dataclass(frozen=True, slots=True)
class ChronoLayout:
    """Chrono layout of a stove model (``allow_unverified`` is enforced by the validation)."""

    time_unit_min: int
    unset_value: int
    blocks: tuple[BlockLayout, ...]
    allow_unverified: bool = False

    def all_addresses(self) -> list[int]:
        """List every EEPROM address of the layout (the always-1 bytes are not part of it).

        :return: Sorted addresses.
        """
        return sorted(a for block in self.blocks for a in block.addresses())


def _byte(value: Any, what: str) -> int:
    """Validate a single-byte integer.

    :param value: Value read from the TOML file.
    :param what: Description used in error messages.
    :return: The validated integer.
    """
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 0xFF:
        raise ConfigurationError(f"{what} must be an integer in 0-255, got {value!r}")
    return value


def _parse_program(what: str, data: Mapping[str, Any]) -> ProgramLayout:
    """Build a program layout.

    :param what: Description used in error messages.
    :param data: TOML inline table.
    :return: The program layout.
    """
    days = data.get("days")
    return ProgramLayout(
        start=_byte(data["start"], f"{what}.start"),
        stop=_byte(data["stop"], f"{what}.stop"),
        power=_byte(data["power"], f"{what}.power"),
        setpoint=_byte(data["setpoint"], f"{what}.setpoint"),
        days=None if days is None else _byte(days, f"{what}.days"),
    )


def _parse_block(data: Mapping[str, Any]) -> BlockLayout:
    """Build a block layout (an empty ``allowed_days`` means every day).

    :param data: TOML table of one block.
    :return: The block layout.
    """
    kind = ChronoBlockKind(data["kind"])
    allowed = frozenset(Weekday.from_key(key) for key in data.get("allowed_days", []))
    return BlockLayout(
        kind=kind,
        enable=_byte(data["enable"], f"blocks.{kind.value}.enable"),
        allowed_days=allowed or _ALL_DAYS,
        programs=tuple(
            _parse_program(f"blocks.{kind.value}.programs[{i}]", table)
            for i, table in enumerate(data["programs"])
        ),
        verified=bool(data.get("verified", False)),
    )


def _build_layout(raw: Mapping[str, Any], allow_unverified: bool) -> ChronoLayout:
    """Convert parsed TOML into a layout and check its consistency.

    :param raw: Parsed TOML document.
    :param allow_unverified: Recorded flag, not enforced here.
    :return: The chrono layout.
    """
    chrono = raw["chrono"]
    layout = ChronoLayout(
        time_unit_min=int(chrono["time_unit_min"]),
        unset_value=_byte(chrono["unset_value"], "chrono.unset_value"),
        blocks=tuple(_parse_block(block) for block in chrono["blocks"]),
        allow_unverified=allow_unverified,
    )
    addresses = [a for block in layout.blocks for a in block.addresses()]
    if len(addresses) != len(set(addresses)):
        raise ConfigurationError("Chrono layout reuses an address")
    kinds = [block.kind for block in layout.blocks]
    if len(kinds) != len(set(kinds)) or layout.time_unit_min < 1:
        raise ConfigurationError("Chrono layout has duplicate blocks or an invalid time unit")
    return layout


def load_chrono_layout(name: str, *, allow_unverified: bool = False) -> ChronoLayout:
    """Load a packaged chrono layout; ``verified = false`` blocks never raise.

    :param name: Layout name (file stem in ``core/chrono/layouts``).
    :param allow_unverified: Recorded on the layout, enforced by ``validate_chrono``.
    :return: The chrono layout.
    :raises ConfigurationError: Unknown name or invalid TOML.
    """
    if _NAME_PATTERN.fullmatch(name) is None:
        raise ConfigurationError(f"Invalid chrono layout name {name!r}")
    resource = files(_LAYOUTS_PACKAGE).joinpath(f"{name}.toml")
    if not resource.is_file():
        raise ConfigurationError(f"Unknown chrono layout {name!r}")
    try:
        # Reason: editors on Windows may save TOML files with a UTF-8 BOM, which tomllib rejects.
        raw = tomllib.loads(resource.read_text(encoding="utf-8-sig"))
        return _build_layout(raw, allow_unverified)
    except (tomllib.TOMLDecodeError, KeyError, TypeError, ValueError) as exc:
        raise ConfigurationError(f"Invalid chrono layout {name!r}: {exc!r}") from exc


def compute_fingerprint(layout: ChronoLayout, raw: Mapping[int, int]) -> str:
    """Hash the layout bytes: SHA-256 over ``address, value`` byte pairs sorted by address.

    :param layout: Chrono layout (the always-1 bytes are excluded).
    :param raw: EEPROM bytes keyed by address.
    :return: First 16 hex characters of the digest.
    """
    payload = b"".join(bytes((address, raw[address])) for address in layout.all_addresses())
    return hashlib.sha256(payload).hexdigest()[:_FINGERPRINT_LENGTH]


def _decode_time(layout: ChronoLayout, value: int, what: str) -> int | None:
    """Decode a start or stop byte.

    :param layout: Chrono layout.
    :param value: Raw byte.
    :param what: Description used in error messages.
    :return: Minutes since midnight, or ``None`` when unset.
    """
    if value == layout.unset_value:
        return None
    if value > layout.unset_value:
        raise ValueError(f"{what} has an invalid time byte {value}")
    return value * layout.time_unit_min


def _decode_program(
    layout: ChronoLayout,
    block: BlockLayout,
    index: int,
    program: ProgramLayout,
    raw: Mapping[int, int],
) -> ChronoProgram:
    """Decode one program.

    :param layout: Chrono layout.
    :param block: Block layout holding the program.
    :param index: 1-based program index.
    :param program: Program layout.
    :param raw: EEPROM bytes keyed by address.
    :return: The decoded program.
    """
    what = f"{block.kind.value} P{index}"
    return ChronoProgram(
        block=block.kind,
        index=index,
        start_min=_decode_time(layout, raw[program.start], f"{what} start"),
        stop_min=_decode_time(layout, raw[program.stop], f"{what} stop"),
        days=None if program.days is None else days_from_mask(raw[program.days]),
        setpoint_c=raw[program.setpoint],
        power_level=raw[program.power],
    )


def decode_config(layout: ChronoLayout, raw: Mapping[int, int]) -> ChronoConfig:
    """Decode the EEPROM bytes of the chrono.

    :param layout: Chrono layout.
    :param raw: EEPROM bytes keyed by address (extra addresses are ignored).
    :return: The chrono configuration with its fingerprint.
    :raises ConfigurationError: When a layout address is missing from ``raw``.
    :raises ValueError: When a byte is not a valid byte, flag, time, or day mask.
    """
    missing = [a for a in layout.all_addresses() if a not in raw]
    if missing:
        raise ConfigurationError(
            "Chrono bytes missing from raw: " + ", ".join(f"0x{a:02X}" for a in missing)
        )
    if any(not 0 <= raw[a] <= 0xFF for a in layout.all_addresses()):
        raise ValueError("Chrono raw values must be bytes in 0-255")
    blocks = []
    for block in layout.blocks:
        flag = raw[block.enable]
        if flag not in (0, 1):
            raise ValueError(f"{block.kind.value} enable byte must be 0 or 1, got {flag}")
        programs = tuple(
            _decode_program(layout, block, index, program, raw)
            for index, program in enumerate(block.programs, start=1)
        )
        blocks.append(ChronoBlock(block.kind, flag == 1, block.allowed_days, programs))
    return ChronoConfig(tuple(blocks), compute_fingerprint(layout, raw))


def _encode_time(layout: ChronoLayout, minutes: int | None, what: str) -> int:
    """Encode a start or stop time.

    :param layout: Chrono layout.
    :param minutes: Minutes since midnight, or ``None`` for unset.
    :param what: Description used in error messages.
    :return: The raw byte.
    :raises ValueError: When off the time grid or when the byte would collide with unset.
    """
    if minutes is None:
        return layout.unset_value
    if minutes < 0 or minutes % layout.time_unit_min:
        raise ValueError(f"{what} {minutes} min is not a positive multiple of the time unit")
    value = minutes // layout.time_unit_min
    # Reason: 24:00 would encode as 144, which is the unset marker, so it cannot be stored.
    if value >= layout.unset_value:
        raise ValueError(f"{what} {minutes} min cannot be encoded (collides with unset)")
    return value


def _encode_program(
    layout: ChronoLayout, program: ProgramLayout, config: ChronoProgram, out: dict[int, int]
) -> None:
    """Write one program into the byte map.

    :param layout: Chrono layout.
    :param program: Program layout.
    :param config: Program values.
    :param out: Byte map to fill.
    """
    what = f"{config.block.value} P{config.index}"
    if (program.days is None) != (config.days is None):
        raise ValueError(f"{what} days do not match the layout")
    out[program.start] = _encode_time(layout, config.start_min, f"{what} start")
    out[program.stop] = _encode_time(layout, config.stop_min, f"{what} stop")
    out[program.setpoint] = _byte_value(config.setpoint_c, f"{what} setpoint")
    out[program.power] = _byte_value(config.power_level, f"{what} power")
    if program.days is not None and config.days is not None:
        out[program.days] = days_to_mask(config.days)


def _byte_value(value: int, what: str) -> int:
    """Check a value fits in one byte.

    :param value: Value to store.
    :param what: Description used in error messages.
    :return: The same value.
    """
    if not 0 <= value <= 0xFF:
        raise ValueError(f"{what} {value} does not fit in one byte")
    return value


def encode_config(layout: ChronoLayout, config: ChronoConfig) -> dict[int, int]:
    """Encode a configuration into EEPROM bytes (layout fields only, no I/O).

    :param layout: Chrono layout.
    :param config: Chrono configuration.
    :return: Bytes keyed by address.
    :raises ValueError: When the config does not match the layout or a value cannot be stored.
    """
    by_kind = {block.kind: block for block in config.blocks}
    if set(by_kind) != {block.kind for block in layout.blocks}:
        raise ValueError("Config blocks do not match the layout")
    out: dict[int, int] = {}
    for block in layout.blocks:
        source = by_kind[block.kind]
        if len(source.programs) != len(block.programs):
            raise ValueError(f"{block.kind.value} program count does not match the layout")
        out[block.enable] = 1 if source.enabled else 0
        for program, values in zip(block.programs, source.programs, strict=True):
            _encode_program(layout, program, values, out)
    return out
