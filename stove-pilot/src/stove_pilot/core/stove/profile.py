"""Stove profile loader (TOML package data read through ``importlib.resources``)."""

import re
import tomllib
from collections.abc import Mapping
from importlib.resources import files
from types import MappingProxyType
from typing import Any, Final

from stove_pilot.common.exceptions import ConfigurationError
from stove_pilot.core.stove.models import (
    ClockDef,
    CommandRecipe,
    CounterDef,
    DisplayDef,
    RegisterDef,
    StateDef,
    StoveProfile,
    StoveState,
    WriteOp,
)
from stove_pilot.lib.navel.rwms import Memory

_PROFILES_PACKAGE: Final = "stove_pilot.core.stove.profiles"
_NAME_PATTERN: Final = re.compile(r"[a-z0-9_]+")
_REQUIRED_REGISTERS: Final = ("ambient_temp", "state")
_CLOCK_META_KEYS: Final = frozenset({"memory", "verified"})


def _byte(value: Any, what: str) -> int:
    """Validate a single-byte integer.

    :param value: Value read from the TOML file.
    :param what: Description used in error messages.
    :return: The validated integer.
    """
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 0xFF:
        raise ConfigurationError(f"{what} must be an integer in 0-255, got {value!r}")
    return value


def _parse_register(name: str, data: Mapping[str, Any]) -> RegisterDef:
    """Build a register definition from its TOML table.

    :param name: Register name.
    :param data: TOML table.
    :return: The register definition.
    """
    off_value = data.get("off_value")
    return RegisterDef(
        name=name,
        memory=Memory(data["memory"]),
        address=_byte(data["address"], f"registers.{name}.address"),
        scale=float(data.get("scale", 1.0)),
        offset=float(data.get("offset", 0.0)),
        writable=bool(data.get("writable", False)),
        min_value=data.get("min"),
        max_value=data.get("max"),
        verified=bool(data.get("verified", False)),
        off_value=None if off_value is None else _byte(off_value, f"registers.{name}.off_value"),
    )


def _parse_states(data: Mapping[str, Any]) -> dict[int, StateDef]:
    """Build the state table.

    :param data: ``[states]`` TOML table keyed by raw code.
    :return: State definitions keyed by raw code.
    """
    return {
        int(code): StateDef(int(code), StoveState(entry["state"]), str(entry["label"]))
        for code, entry in data.items()
    }


def _parse_command(name: str, data: Mapping[str, Any]) -> CommandRecipe:
    """Build a command recipe.

    :param name: Command name.
    :param data: TOML table.
    :return: The command recipe.
    """
    writes = tuple(
        WriteOp(
            Memory(write["memory"]),
            _byte(write["address"], f"commands.{name}.address"),
            _byte(write["value"], f"commands.{name}.value"),
        )
        for write in data["writes"]
    )
    return CommandRecipe(writes=writes, verified=bool(data.get("verified", False)))


def _parse_clock_addresses(
    section: str, data: Mapping[str, Any], memory: Memory
) -> Mapping[str, int]:
    """Read the address entries of one clock table.

    :param section: Section name used in error messages.
    :param data: TOML table.
    :param memory: Memory area the table must declare.
    :return: Read-only mapping of clock field to address.
    """
    if Memory(data["memory"]) is not memory:
        raise ConfigurationError(f"{section}.memory must be {memory.value!r}")
    return MappingProxyType(
        {
            key: _byte(value, f"{section}.{key}")
            for key, value in data.items()
            if key not in _CLOCK_META_KEYS
        }
    )


def _parse_clock(data: Mapping[str, Any]) -> ClockDef:
    """Build the clock definition.

    :param data: ``[clock]`` TOML table.
    :return: The clock definition.
    """
    return ClockDef(
        live=_parse_clock_addresses("clock.live", data["live"], Memory.RAM),
        set=_parse_clock_addresses("clock.set", data["set"], Memory.EEPROM),
        set_verified=bool(data["set"].get("verified", False)),
    )


def _parse_counter(name: str, data: Mapping[str, Any]) -> CounterDef:
    """Build a counter definition.

    :param name: Counter name.
    :param data: TOML table.
    :return: The counter definition.
    """
    endian = data["endian"]
    if endian not in ("little", "big"):
        raise ConfigurationError(f"counters.{name}.endian must be 'little' or 'big'")
    byte_count = data["bytes"]
    if isinstance(byte_count, bool) or not isinstance(byte_count, int) or byte_count < 1:
        raise ConfigurationError(f"counters.{name}.bytes must be a positive integer")
    return CounterDef(
        memory=Memory(data["memory"]),
        address=_byte(data["address"], f"counters.{name}.address"),
        bytes=byte_count,
        endian=endian,
    )


def _build_profile(name: str, raw: Mapping[str, Any], allow_unverified: bool) -> StoveProfile:
    """Convert parsed TOML into a profile.

    :param name: Requested profile name.
    :param raw: Parsed TOML document.
    :param allow_unverified: Recorded flag, not enforced here.
    :return: The stove profile.
    """
    registers = {
        reg_name: _parse_register(reg_name, table)
        for reg_name, table in raw.get("registers", {}).items()
    }
    for required in _REQUIRED_REGISTERS:
        if required not in registers:
            raise ConfigurationError(f"Profile {name!r} must define registers.{required}")
    display = raw.get("display")
    clock = raw.get("clock")
    return StoveProfile(
        name=str(raw.get("profile", {}).get("name", name)),
        registers=MappingProxyType(registers),
        states=MappingProxyType(_parse_states(raw.get("states", {}))),
        commands=MappingProxyType(
            {cmd: _parse_command(cmd, table) for cmd, table in raw.get("commands", {}).items()}
        ),
        display=(
            None
            if display is None
            else DisplayDef(
                Memory(display["memory"]),
                _byte(display["start"], "display.start"),
                int(display["length"]),
            )
        ),
        clock=None if clock is None else _parse_clock(clock),
        counters=MappingProxyType(
            {c: _parse_counter(c, table) for c, table in raw.get("counters", {}).items()}
        ),
        allow_unverified=allow_unverified,
    )


def load_profile(name: str, *, allow_unverified: bool = False) -> StoveProfile:
    """Load a packaged stove profile; ``verified = false`` entries never raise.

    :param name: Profile name (file stem in ``core/stove/profiles``).
    :param allow_unverified: Recorded on the profile, enforced at write time by the gateway.
    :return: The stove profile.
    :raises ConfigurationError: Unknown name, invalid TOML, or missing required registers.
    """
    if _NAME_PATTERN.fullmatch(name) is None:
        raise ConfigurationError(f"Invalid profile name {name!r}")
    resource = files(_PROFILES_PACKAGE).joinpath(f"{name}.toml")
    if not resource.is_file():
        raise ConfigurationError(f"Unknown stove profile {name!r}")
    try:
        # Reason: editors on Windows may save TOML files with a UTF-8 BOM, which tomllib rejects.
        raw = tomllib.loads(resource.read_text(encoding="utf-8-sig"))
        return _build_profile(name, raw, allow_unverified)
    except (tomllib.TOMLDecodeError, KeyError, TypeError, ValueError) as exc:
        raise ConfigurationError(f"Invalid stove profile {name!r}: {exc!r}") from exc
