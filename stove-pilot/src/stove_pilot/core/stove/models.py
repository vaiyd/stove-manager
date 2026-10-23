"""Stove domain models: live status, command outcomes and the stove profile."""

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Literal

from stove_pilot.common.exceptions import ConfigurationError
from stove_pilot.core.stove.messages import AlarmInfo, DisplayMessage
from stove_pilot.lib.navel.rwms import Memory


class StoveState(StrEnum):
    """Normalized stove operating state."""

    OFF = "OFF"
    IGNITION = "IGNITION"
    RUNNING = "RUNNING"
    CLEANING = "CLEANING"
    SHUTDOWN = "SHUTDOWN"
    STANDBY = "STANDBY"
    ALARM = "ALARM"
    UNKNOWN = "UNKNOWN"


class CommandResult(StrEnum):
    """Outcome category of a stove command."""

    APPLIED = "APPLIED"
    NO_OP = "NO_OP"
    DEFERRED = "DEFERRED"


class EventSource(StrEnum):
    """Origin of a stove command or event."""

    USER = "USER"
    CHRONO = "CHRONO"
    POLLER = "POLLER"
    SYSTEM = "SYSTEM"


@dataclass(frozen=True, slots=True)
class EnergySavingStatus:
    """ENERGY SAVING mode as read from the board (``None`` offset = OFF)."""

    enabled: bool
    stop_offset_c: int | None
    start_offset_c: int | None
    restart_below_c: float | None


@dataclass(frozen=True, slots=True)
class StoveStatus:
    """One consistent snapshot of the stove (optional fields are ``None`` when unavailable)."""

    state: StoveState
    raw_state_code: int
    state_label: str
    ambient_temp_c: float
    fumes_temp_c: float
    setpoint_c: float
    power_level: int
    display_text: str | None = None
    display_message: DisplayMessage | None = None
    alarm_code: str | None = None
    alarm: AlarmInfo | None = None
    energy_saving: EnergySavingStatus | None = None
    stove_time: datetime | None = None
    clock_drift_s: float | None = None
    # Reason: keyword-only keeps the documented field order while staying required.
    polled_at: datetime = field(kw_only=True)


@dataclass(frozen=True, slots=True)
class ClockStatus:
    """Stove clock compared with the server clock."""

    stove_time: datetime
    server_time: datetime
    drift_s: float
    auto_sync: bool
    last_sync_at: datetime | None = None
    drift_tolerance_s: float = 0.0
    auto_sync_writable: bool = False


@dataclass(frozen=True, slots=True)
class MaintenanceInfo:
    """Maintenance counters (``None``/empty when the counter is not mapped)."""

    total_hours: int | None = None
    partial_hours: int | None = None
    ignitions: int | None = None
    hours_to_service: int | None = None
    last_alarms: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class CommandOutcome:
    """Result of a stove command with the resulting status when known."""

    result: CommandResult
    reason: str | None = None
    status: StoveStatus | None = None


@dataclass(frozen=True, slots=True)
class PendingCommand:
    """A deferred start or stop waiting for the stove to allow it."""

    command: str
    source: EventSource
    requested_at: datetime
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class StoveSettings:
    """User-adjustable stove settings (``None`` when unmapped) with their writable flags."""

    energy_saving: EnergySavingStatus | None
    energy_stop_writable: bool
    energy_start_writable: bool
    display_eco: bool | None
    display_eco_writable: bool


@dataclass(frozen=True, slots=True)
class StoveCapabilities:
    """Which writes are currently allowed (verified, or unverified writes enabled)."""

    start: bool
    stop: bool
    setpoint: bool
    power: bool
    energy_saving: bool
    display_eco: bool
    clock_sync: bool


@dataclass(frozen=True, slots=True)
class RegisterDef:
    """A single-byte stove register (``min_value``/``max_value`` are in decoded units)."""

    name: str
    memory: Memory
    address: int
    scale: float = 1.0
    offset: float = 0.0
    writable: bool = False
    min_value: float | None = None
    max_value: float | None = None
    verified: bool = False
    off_value: int | None = None

    def decode(self, raw: int) -> float:
        """Convert a raw byte to a physical value.

        :param raw: Raw register value.
        :return: ``raw * scale + offset``.
        """
        return raw * self.scale + self.offset

    def encode(self, value: float) -> int:
        """Convert a physical value to a raw byte (inverse of ``decode``).

        :param value: Physical value, checked against ``min_value``/``max_value``.
        :return: Rounded raw register value (0-255).
        :raises ValueError: When the value is out of range.
        """
        if self.min_value is not None and value < self.min_value:
            raise ValueError(f"{self.name}: {value} is below the minimum {self.min_value}")
        if self.max_value is not None and value > self.max_value:
            raise ValueError(f"{self.name}: {value} is above the maximum {self.max_value}")
        raw = round((value - self.offset) / self.scale)
        if not 0 <= raw <= 0xFF:
            raise ValueError(f"{self.name}: raw value {raw} does not fit in one byte")
        return raw


@dataclass(frozen=True, slots=True)
class WriteOp:
    """One single-byte write."""

    memory: Memory
    address: int
    value: int


@dataclass(frozen=True, slots=True)
class ClockWrite:
    """One planned clock byte write, named after its ``SET_FIELDS`` field."""

    name: str
    op: WriteOp


@dataclass(frozen=True, slots=True)
class CommandRecipe:
    """Ordered writes that perform a command (for example start or stop)."""

    writes: tuple[WriteOp, ...]
    verified: bool = False


@dataclass(frozen=True, slots=True)
class StateDef:
    """Mapping of a raw state code to a normalized state."""

    code: int
    state: StoveState
    label: str


@dataclass(frozen=True, slots=True)
class DisplayDef:
    """Location of the panel display buffer."""

    memory: Memory
    start: int
    length: int


@dataclass(frozen=True, slots=True)
class ClockDef:
    """Clock addresses: ``live`` on RAM (binary), ``set`` on EEPROM (BCD)."""

    live: Mapping[str, int]
    set: Mapping[str, int]
    set_verified: bool = False


@dataclass(frozen=True, slots=True)
class CounterDef:
    """A multi-byte counter stored in consecutive addresses."""

    memory: Memory
    address: int
    bytes: int
    endian: Literal["little", "big"]


@dataclass(frozen=True, slots=True)
class StoveProfile:
    """Register map, state table and command recipes of one stove model."""

    name: str
    registers: Mapping[str, RegisterDef]
    states: Mapping[int, StateDef]
    commands: Mapping[str, CommandRecipe]
    display: DisplayDef | None = None
    clock: ClockDef | None = None
    counters: Mapping[str, CounterDef] = field(default_factory=dict)
    allow_unverified: bool = False

    def register(self, name: str) -> RegisterDef:
        """Return a mapped register.

        :param name: Register name.
        :return: The register definition.
        :raises ConfigurationError: When the register is not mapped.
        """
        try:
            return self.registers[name]
        except KeyError:
            raise ConfigurationError(
                f"Register {name!r} is not mapped in profile {self.name!r}"
            ) from None

    def has_register(self, name: str) -> bool:
        """Tell whether a register is mapped.

        :param name: Register name.
        :return: ``True`` when mapped.
        """
        return name in self.registers

    def decode_state(self, code: int) -> StateDef:
        """Decode a raw state code (unknown codes give ``UNKNOWN``).

        :param code: Raw state code.
        :return: The matching state definition.
        """
        known = self.states.get(code)
        if known is not None:
            return known
        return StateDef(code, StoveState.UNKNOWN, f"Unknown state ({code})")
