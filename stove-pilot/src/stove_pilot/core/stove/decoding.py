"""Pure decoding of raw register bytes into stove domain objects (no I/O)."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Final

from stove_pilot.core.stove.clock import decode_live_clock
from stove_pilot.core.stove.messages import ALARMS, parse_display
from stove_pilot.core.stove.models import (
    EnergySavingStatus,
    MaintenanceInfo,
    RegisterDef,
    StoveProfile,
    StoveStatus,
)
from stove_pilot.lib.navel.rwms import Memory

StatusPlan = list[tuple[str, Memory, int]]

SERVICE_INTERVAL_H: Final = 2000
_ESSENTIAL_REGISTERS: Final = (
    "ambient_temp",
    "fumes_temp",
    "state",
    "setpoint",
    "power_level",
)
SLOW_REGISTERS: Final = ("energy_stop_offset", "energy_start_offset")
_COUNTER_FIELDS: Final = {
    "ignition_count": "ignitions",
    "total_hours": "total_hours",
    "partial_hours": "partial_hours",
}
_PRINTABLE_MIN: Final = 32
_PRINTABLE_MAX: Final = 126


@dataclass(frozen=True, slots=True)
class StatusTiers:
    """The registers of one status read, split by how often and how strictly they are read."""

    essential: StatusPlan
    display: StatusPlan
    slow: StatusPlan


def _named_plan(profile: StoveProfile, names: Sequence[str]) -> StatusPlan:
    """List the mapped registers among ``names``.

    :param profile: Stove profile.
    :param names: Register names, in read order.
    :return: ``(name, memory, address)`` entries (unmapped names are skipped).
    """
    registers = (profile.registers.get(name) for name in names)
    return [(reg.name, reg.memory, reg.address) for reg in registers if reg is not None]


def status_tiers(profile: StoveProfile) -> StatusTiers:
    """Split the status registers into the essential, display and slow tiers.

    :param profile: Stove profile.
    :return: ``(key, memory, address)`` entries per tier; keys are register names,
        ``display:<i>`` and ``clock:<field>``.
    """
    display: StatusPlan = []
    if profile.display is not None:
        spec = profile.display
        display = [(f"display:{i}", spec.memory, spec.start + i) for i in range(spec.length)]
    slow = _named_plan(profile, SLOW_REGISTERS)
    if profile.clock is not None:
        live = profile.clock.live
        slow.extend((f"clock:{field}", Memory.RAM, address) for field, address in live.items())
    return StatusTiers(_named_plan(profile, _ESSENTIAL_REGISTERS), display, slow)


def _display_text(raw: Mapping[str, int], length: int) -> str:
    """Render display bytes as text (non-printable bytes become spaces, whitespace collapsed).

    :param raw: Raw values keyed by ``display:<i>``.
    :param length: Display buffer length.
    :return: Display text.
    """
    chars = (raw.get(f"display:{index}", 0) for index in range(length))
    text = "".join(chr(b) if _PRINTABLE_MIN <= b <= _PRINTABLE_MAX else " " for b in chars)
    return " ".join(text.split())


def _offset_c(register: RegisterDef, raw: int) -> int | None:
    """Decode an ENERGY SAVING offset (``None`` for OFF or an unknown encoding).

    :param register: Offset register.
    :param raw: Raw byte.
    :return: Offset in degrees, or ``None``.
    """
    if register.off_value is not None and raw == register.off_value:
        return None
    value = register.decode(raw)
    if register.min_value is not None and value < register.min_value:
        return None
    return int(value)


def _energy_saving(
    profile: StoveProfile, raw: Mapping[str, int], setpoint_c: float
) -> EnergySavingStatus | None:
    """Build the ENERGY SAVING status when its registers are mapped.

    :param profile: Stove profile.
    :param raw: Raw values keyed by register name.
    :param setpoint_c: Decoded setpoint.
    :return: Status, or ``None`` when no offset register is mapped or none was read yet.
    """
    stop_reg = profile.registers.get("energy_stop_offset")
    start_reg = profile.registers.get("energy_start_offset")
    mapped = [reg for reg in (stop_reg, start_reg) if reg is not None]
    if not mapped or any(reg.name not in raw for reg in mapped):
        return None
    stop = None if stop_reg is None else _offset_c(stop_reg, raw[stop_reg.name])
    start = None if start_reg is None else _offset_c(start_reg, raw[start_reg.name])
    return EnergySavingStatus(
        enabled=stop is not None or start is not None,
        stop_offset_c=stop,
        start_offset_c=start,
        restart_below_c=None if start is None else setpoint_c - start,
    )


def _decode_named(profile: StoveProfile, raw: Mapping[str, int], name: str) -> float:
    """Decode a mapped register, or ``0.0`` when the profile does not map it.

    :param profile: Stove profile.
    :param raw: Raw values keyed by register name.
    :param name: Register name.
    :return: Physical value.
    """
    register = profile.registers.get(name)
    return 0.0 if register is None else register.decode(raw[name])


def build_status(
    profile: StoveProfile,
    raw: Mapping[str, int],
    *,
    now_local: datetime,
    clock_read_at: datetime | None = None,
) -> StoveStatus:
    """Assemble a status snapshot from the raw values of ``status_tiers``.

    :param profile: Stove profile.
    :param raw: Raw values keyed like ``status_tiers``; display, ENERGY SAVING and clock keys
        may be missing (their fields are then ``None``).
    :param now_local: Aware server time of the read (becomes ``polled_at``).
    :param clock_read_at: Aware time the clock bytes were read (defaults to ``now_local``).
    :return: The status snapshot.
    """
    state_code = raw["state"]
    state = profile.decode_state(state_code)
    setpoint_c = _decode_named(profile, raw, "setpoint")
    display_text = None
    message = None
    if profile.display is not None and "display:0" in raw:
        display_text = _display_text(raw, profile.display.length)
        message = parse_display(display_text)
    alarm = ALARMS.get(message.code) if message is not None else None
    stove_time = decode_live_clock(
        {key.removeprefix("clock:"): val for key, val in raw.items() if key.startswith("clock:")}
    )
    drift = None
    if stove_time is not None:
        at = clock_read_at or now_local
        drift = (stove_time - at.replace(tzinfo=None)).total_seconds()
        # Reason: the clock bytes may be older than the poll; the stove clock ran as long.
        stove_time += now_local - at
    return StoveStatus(
        state=state.state,
        raw_state_code=state_code,
        state_label=state.label,
        ambient_temp_c=_decode_named(profile, raw, "ambient_temp"),
        fumes_temp_c=_decode_named(profile, raw, "fumes_temp"),
        setpoint_c=setpoint_c,
        power_level=int(_decode_named(profile, raw, "power_level")),
        display_text=display_text,
        display_message=message,
        alarm_code=None if alarm is None else alarm.code,
        alarm=alarm,
        energy_saving=_energy_saving(profile, raw, setpoint_c),
        stove_time=stove_time,
        clock_drift_s=drift,
        polled_at=now_local,
    )


def counter_targets(profile: StoveProfile) -> list[tuple[Memory, int]]:
    """List every counter byte, counter by counter (profile order).

    :param profile: Stove profile.
    :return: ``(memory, address)`` per byte.
    """
    return [
        (counter.memory, counter.address + index)
        for counter in profile.counters.values()
        for index in range(counter.bytes)
    ]


def decode_counters(profile: StoveProfile, values: Sequence[int]) -> MaintenanceInfo:
    """Decode the values read for ``counter_targets``.

    :param profile: Stove profile.
    :param values: One byte per target of ``counter_targets``.
    :return: Maintenance counters (unmapped counters stay ``None``).
    """
    decoded: dict[str, int] = {}
    position = 0
    for name, counter in profile.counters.items():
        chunk = values[position : position + counter.bytes]
        position += counter.bytes
        field = _COUNTER_FIELDS.get(name)
        if field is not None:
            decoded[field] = int.from_bytes(bytes(chunk), counter.endian)
    partial = decoded.get("partial_hours")
    if partial is not None:
        decoded["hours_to_service"] = max(0, SERVICE_INTERVAL_H - partial)
    return MaintenanceInfo(**decoded)
