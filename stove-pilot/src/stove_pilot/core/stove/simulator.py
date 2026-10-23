"""In-memory stove board for development: register arrays, state machine and clock."""

import json
import threading
from collections import deque
from collections.abc import Callable, Sequence
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Final

from stove_pilot.common.exceptions import ConfigurationError
from stove_pilot.core.stove.clock import (
    SET_FIELDS,
    decode_set_clock,
    encode_set_clock,
    encode_weekday,
)
from stove_pilot.core.stove.models import RegisterDef, StoveProfile, WriteOp
from stove_pilot.core.stove.profile import load_profile
from stove_pilot.lib.navel.rwms import Memory

_ADDRESS_SPACE: Final = 0x100
_STATE_OFF: Final = 0
_STATE_IGNITION_CHECK: Final = 1
_STATE_RUNNING: Final = 4
_STATE_CLEANING: Final = 6
_STATE_STANDBY: Final = 7
# Reason: observed durations (design 1.2): state 1 lasts 30 s, state 2 7 min, state 3 2 min.
_IGNITION_PLAN: Final = (
    (1, timedelta(seconds=30)),
    (2, timedelta(minutes=7)),
    (3, timedelta(minutes=2)),
)
_STOP_PLAN: Final = ((_STATE_CLEANING, timedelta(minutes=4)),)
_PANEL_TEXT: Final = {
    0: "OFF",
    1: "CONTROLE",
    2: "ALLUMAGE PHASE 1",
    3: "ALLUMAGE PHASE 2",
    4: "ENS OK",
    6: "NETTOYAGE BRASERO",
    7: "ENERGY OK",
}
# Reason: the real stove clock runs about 20 s per hour fast.
_CLOCK_SPEED: Final = 1 + 20 / 3600
_STEP: Final = timedelta(seconds=60)
_HEAT_C_PER_MIN: Final = 0.1
_COOL_C_PER_MIN: Final = 0.05
_COOL_FLOOR_C: Final = 15.0
_DEFAULT_AMBIENT_C: Final = 20.0
_DEFAULT_SETPOINT_C: Final = 20.0


class SimulatedRegisterIO:
    """A ``RegisterIO`` backed by RAM and EEPROM byte arrays and a simple stove model.

    The stove clock starts equal to the injected clock and then runs 20 s per hour fast; the
    seed clock bytes are ignored. The display buffer keeps the seed bytes until the state changes.
    Without a seed everything starts at zero except the defaults (OFF, 20 C ambient, 20 C
    setpoint, power 1, display text OFF). The fumes temperature and counters stay static.
    """

    def __init__(
        self,
        seed: Path | None,
        clock: Callable[[], datetime],
        *,
        profile: StoveProfile | None = None,
    ) -> None:
        """Build the board.

        :param seed: JSON dump ``{"ram": {...}, "eeprom": {...}}`` with ``0xNN`` keys, or ``None``.
        :param clock: Returns the aware simulated server time (injectable to advance time).
        :param profile: Stove profile (defaults to ``superior_maira``).
        """
        self._profile = profile or load_profile("superior_maira")
        self._clock = clock
        self._lock = threading.RLock()
        self._memory = {memory: bytearray(_ADDRESS_SPACE) for memory in Memory}
        self._plan: deque[tuple[int, timedelta]] = deque()
        self._final = _STATE_OFF
        self._due: datetime | None = None
        now = clock()
        self._last = now
        self._stove_anchor = now
        self._stove_base = now.replace(tzinfo=None)
        if seed is None:
            self._apply_defaults()
        else:
            self._load_seed(seed)
        self._ambient = self._reg("ambient_temp").decode(self._get(self._reg("ambient_temp")))

    def set_ram(self, address: int, value: int) -> None:
        """Set a RAM byte directly (verification scripts).

        :param address: RAM address.
        :param value: Byte value.
        """
        with self._lock:
            self._check(address, value)
            self._memory[Memory.RAM][address] = value
            if address == self._reg("state").address:
                self._cancel_plan()
                self._show_panel(value)
            if address == self._reg("ambient_temp").address:
                self._ambient = self._reg("ambient_temp").decode(value)

    def read(self, targets: Sequence[tuple[Memory, int]]) -> list[int]:
        """Read registers after advancing the simulation to the injected clock.

        :param targets: Memory area and address of each register.
        :return: One byte per target, in order.
        """
        with self._lock:
            self._advance()
            live = self._live_clock()
            values = []
            for memory, address in targets:
                self._check(address, 0)
                if memory is Memory.RAM and address in live:
                    values.append(live[address])
                else:
                    values.append(self._memory[memory][address])
            return values

    def write(self, ops: Sequence[WriteOp]) -> None:
        """Apply writes; a start or stop on the state register launches its sequence.

        :param ops: Single-byte writes, applied in order.
        """
        with self._lock:
            self._advance()
            clock_written = False
            for op in ops:
                self._check(op.address, op.value)
                previous = self._memory[op.memory][op.address]
                self._memory[op.memory][op.address] = op.value
                if op.memory is Memory.RAM and op.address == self._reg("state").address:
                    self._on_state_write(previous, op.value)
                clock = self._profile.clock
                if clock is not None and op.memory is Memory.EEPROM:
                    clock_written |= op.address in clock.set.values()
            if clock_written:
                self._resync_stove_clock()

    def _reg(self, name: str) -> RegisterDef:
        return self._profile.register(name)

    def _get(self, register: RegisterDef) -> int:
        return self._memory[register.memory][register.address]

    @staticmethod
    def _check(address: int, value: int) -> None:
        if not 0 <= address < _ADDRESS_SPACE or not 0 <= value <= 0xFF:
            raise ValueError(f"Invalid simulator address or value: {address}, {value}")

    def _load_seed(self, path: Path) -> None:
        """Load the nested dump (``null`` values stay 0).

        :param path: JSON file written by the ``dump`` command.
        """
        try:
            data: Any = json.loads(path.read_text(encoding="utf-8"))
            for memory in Memory:
                for key, value in (data.get(memory.value) or {}).items():
                    if value is not None:
                        address = int(key, 0)
                        self._check(address, value)
                        self._memory[memory][address] = value
        except (OSError, ValueError, AttributeError, TypeError) as exc:
            raise ConfigurationError(f"Invalid simulator seed {path}: {exc!r}") from exc

    def _apply_defaults(self) -> None:
        """Fill the defaults used when no seed is given."""
        ambient = self._reg("ambient_temp")
        self._memory[ambient.memory][ambient.address] = ambient.encode(_DEFAULT_AMBIENT_C)
        setpoint = self._profile.registers.get("setpoint")
        if setpoint is not None:
            self._memory[setpoint.memory][setpoint.address] = setpoint.encode(_DEFAULT_SETPOINT_C)
        power = self._profile.registers.get("power_level")
        if power is not None:
            self._memory[power.memory][power.address] = 1
        clock = self._profile.clock
        if clock is not None:
            encoded = encode_set_clock(self._stove_base)
            for name in SET_FIELDS:
                if name in clock.set:
                    self._memory[Memory.EEPROM][clock.set[name]] = encoded[name]
        self._show_panel(_STATE_OFF)

    def _state(self) -> int:
        return self._get(self._reg("state"))

    def _stove_now(self) -> datetime:
        return self._stove_base + (self._clock() - self._stove_anchor) * _CLOCK_SPEED

    def _live_clock(self) -> dict[int, int]:
        """Compute the RAM live clock bytes keyed by address.

        :return: Empty when the profile has no clock.
        """
        clock = self._profile.clock
        if clock is None:
            return {}
        now = self._stove_now()
        fields = {
            "second": now.second,
            "weekday": encode_weekday(now),
            "hour": now.hour,
            "minute": now.minute,
            "day": now.day,
            "month": now.month,
            "year": now.year - 2000,
        }
        return {address: fields[name] for name, address in clock.live.items() if name in fields}

    def _resync_stove_clock(self) -> None:
        """Re-anchor the stove clock on the EEPROM clock-setting bytes (RAM follows at once)."""
        clock = self._profile.clock
        if clock is None:
            return
        raw = {name: self._memory[Memory.EEPROM][addr] for name, addr in clock.set.items()}
        moment = decode_set_clock(raw)
        if moment is not None:
            self._stove_base = moment
            self._stove_anchor = self._clock()

    def _show_panel(self, code: int) -> None:
        """Write the panel text of a state into the display buffer.

        :param code: State code.
        """
        display = self._profile.display
        if display is None:
            return
        text = _PANEL_TEXT.get(code, "").ljust(display.length)[: display.length]
        buffer = self._memory[display.memory]
        for index, char in enumerate(text):
            buffer[display.start + index] = ord(char)

    def _enter(self, code: int) -> None:
        self._memory[Memory.RAM][self._reg("state").address] = code
        self._show_panel(code)

    def _cancel_plan(self) -> None:
        self._plan.clear()
        self._due = None

    def _begin(self, plan: Sequence[tuple[int, timedelta]], final: int) -> None:
        """Start a timed state sequence at the current simulation time.

        :param plan: ``(state code, duration)`` phases.
        :param final: State entered after the last phase.
        """
        self._plan = deque(plan)
        self._final = final
        self._step_plan(self._last)

    def _step_plan(self, at: datetime) -> None:
        if self._plan:
            code, duration = self._plan.popleft()
            self._enter(code)
            self._due = at + duration
        else:
            self._enter(self._final)
            self._due = None

    def _on_state_write(self, previous: int, value: int) -> None:
        """React to a write on the state register (1 starts from OFF, 6 stops).

        :param previous: State code held before the write.
        :param value: Written state code.
        """
        if value == _STATE_IGNITION_CHECK and previous == _STATE_OFF:
            self._begin(_IGNITION_PLAN, _STATE_RUNNING)
        elif value == _STATE_CLEANING and previous not in (_STATE_OFF, _STATE_CLEANING):
            self._begin(_STOP_PLAN, _STATE_OFF)
        else:
            self._cancel_plan()
            self._show_panel(value)

    def _integrate(self, until: datetime) -> None:
        """Move the ambient temperature and the time marker up to ``until``.

        :param until: Target simulation time.
        """
        minutes = (until - self._last).total_seconds() / 60
        state = self._state()
        if state == _STATE_RUNNING:
            self._ambient += _HEAT_C_PER_MIN * minutes
        elif state in (_STATE_OFF, _STATE_CLEANING, _STATE_STANDBY):
            cooled = self._ambient - _COOL_C_PER_MIN * minutes
            self._ambient = max(cooled, min(self._ambient, _COOL_FLOOR_C))
        self._last = until

    def _thermostat(self) -> None:
        """Go to standby above the setpoint; re-ignite from standby below it."""
        setpoint = self._profile.registers.get("setpoint")
        if setpoint is None:
            return
        target = setpoint.decode(self._get(setpoint))
        state = self._state()
        if state == _STATE_RUNNING and self._ambient > target:
            self._cancel_plan()
            self._enter(_STATE_STANDBY)
        elif state == _STATE_STANDBY and self._ambient < target:
            self._begin(_IGNITION_PLAN, _STATE_RUNNING)

    def _advance(self) -> None:
        """Run the simulation up to the injected clock in steps of at most one minute."""
        now = self._clock()
        while self._last < now:
            target = min(now, self._last + _STEP)
            if self._due is not None and self._last <= self._due < target:
                target = self._due
            self._integrate(target)
            if self._due is not None and self._due <= self._last:
                self._step_plan(self._due)
            self._thermostat()
        self._store_ambient()

    def _store_ambient(self) -> None:
        register = self._reg("ambient_temp")
        raw = round((self._ambient - register.offset) / register.scale)
        self._memory[register.memory][register.address] = max(0, min(0xFF, raw))
