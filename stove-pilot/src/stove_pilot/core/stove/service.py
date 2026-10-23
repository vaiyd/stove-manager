"""Stove I/O: the Navel register adapter and the gateway with its safety gates."""

import logging
import time
from collections.abc import Callable, Sequence
from datetime import datetime, tzinfo
from functools import partial
from itertools import batched

from stove_pilot.common.exceptions import (
    ConfigurationError,
    GatewayProtocolError,
    GatewayUnavailableError,
    UnverifiedRegisterError,
    ValidationFailedError,
    WriteOutcomeUnknownError,
)
from stove_pilot.core.stove.batching import AdaptiveBatch
from stove_pilot.core.stove.clock import SET_FIELDS, encode_set_clock
from stove_pilot.core.stove.decoding import SLOW_REGISTERS, counter_targets, decode_counters
from stove_pilot.core.stove.models import (
    ClockWrite,
    EnergySavingStatus,
    MaintenanceInfo,
    RegisterDef,
    StoveCapabilities,
    StoveProfile,
    StoveSettings,
    StoveStatus,
    WriteOp,
)
from stove_pilot.core.stove.ports import RegisterIO
from stove_pilot.core.stove.retry import (
    CHUNK_SIZE,
    MAX_REWRITES,
    READ_BACKOFF_S,
    retry_read,
    write_with_readback,
)
from stove_pilot.core.stove.status_reader import (
    DEFAULT_DISPLAY_MAX_AGE_S,
    DEFAULT_SLOW_INTERVAL_S,
    StatusReader,
)
from stove_pilot.lib.navel import Memory, NavelClient, encode_item

_LOGGER = logging.getLogger(__name__)


class NavelRegisterIO:
    """``RegisterIO`` over a ``NavelClient``: adaptive chunks, retried reads, single-shot writes."""

    def __init__(
        self,
        client: NavelClient,
        *,
        sleep: Callable[[float], None] = time.sleep,
        read_backoff_s: Sequence[float] = READ_BACKOFF_S,
        chunk_size: int = CHUNK_SIZE,
    ) -> None:
        """Wrap a client.

        :param client: Navel client (closed by ``close``).
        :param sleep: Sleep function used between read attempts.
        :param read_backoff_s: Delays after the first and second failed read attempt.
        :param chunk_size: Configured (maximum) items per request.
        """
        self._client = client
        self._sleep = sleep
        self._read_backoff_s = tuple(read_backoff_s)
        self._batch = AdaptiveBatch(chunk_size)

    @property
    def batch_size(self) -> int:
        """Current adaptive batch size (diagnostics)."""
        return self._batch.size

    def read(self, targets: Sequence[tuple[Memory, int]]) -> list[int]:
        """Read registers in chunks of the current batch size, retrying each chunk.

        A chunk that gets a protocol error is retried as two halves (recursively), and the batch
        size is lowered to the largest request size that then succeeded.

        :param targets: Memory area and address of each register.
        :return: One byte value per target, in order.
        :raises GatewayError: The last error of a chunk that kept failing.
        """
        values: list[int] = []
        position = 0
        while position < len(targets):
            chunk = targets[position : position + self._batch.size]
            ok_sizes: list[int] = []
            values.extend(self._read_piece(chunk, ok_sizes))
            if ok_sizes != [len(chunk)]:
                # Reason: only a split read proves which smaller size the module still accepts.
                self._batch.lower_to(max(ok_sizes))
            position += len(chunk)
        return values

    def write(self, ops: Sequence[WriteOp]) -> None:
        """Write registers in chunks of the current batch size; never retried here.

        :param ops: Single-byte writes, applied in order.
        :raises WriteOutcomeUnknownError: No valid reply, so the write may have been applied.
        :raises GatewayNackError: The module explicitly refused the write.
        """
        for chunk in batched(ops, self._batch.write_size):
            items = [encode_item(op.memory, op.address) for op in chunk]
            try:
                self._client.write(items, [op.value for op in chunk])
            except (GatewayUnavailableError, GatewayProtocolError) as exc:
                raise WriteOutcomeUnknownError(
                    f"Write of {len(chunk)} register(s) has an unknown outcome: {exc}"
                ) from exc

    def close(self) -> None:
        """Release the underlying client."""
        self._client.close()

    def _read_piece(self, chunk: Sequence[tuple[Memory, int]], ok_sizes: list[int]) -> list[int]:
        """Read one chunk with bounded attempts; the second attempt after a protocol error splits.

        :param chunk: Targets of one request (at least one).
        :param ok_sizes: Collects the size of every request that succeeded.
        :return: One byte value per target.
        """
        items = [encode_item(memory, address) for memory, address in chunk]
        fallback = partial(self._read_halves, chunk, ok_sizes) if len(chunk) > 1 else None
        return retry_read(
            partial(self._read_request, items, ok_sizes),
            sleep=self._sleep,
            delays=self._read_backoff_s,
            fallback=fallback,
        )

    def _read_halves(self, chunk: Sequence[tuple[Memory, int]], ok_sizes: list[int]) -> list[int]:
        """Read a chunk as two halves, each with its own bounded attempts.

        :param chunk: Targets to split (at least two).
        :param ok_sizes: Collects the size of every request that succeeded.
        :return: One byte value per target.
        """
        middle = (len(chunk) + 1) // 2
        return self._read_piece(chunk[:middle], ok_sizes) + self._read_piece(
            chunk[middle:], ok_sizes
        )

    def _read_request(self, items: Sequence[int], ok_sizes: list[int]) -> list[int]:
        """Send one read request and report its outcome to the batch policy.

        :param items: Encoded items.
        :param ok_sizes: Collects the size of every request that succeeded.
        :return: One byte value per item.
        """
        try:
            values = self._client.read(items)
        except (GatewayUnavailableError, GatewayProtocolError):
            self._batch.read_failed()
            raise
        ok_sizes.append(len(items))
        self._batch.read_succeeded(len(items))
        return values


class StoveGateway:
    """Status reads and gated writes for one stove profile."""

    def __init__(
        self,
        io: RegisterIO,
        profile: StoveProfile,
        clock: Callable[[], datetime],
        tz: tzinfo,
        *,
        allow_unverified: bool = False,
        slow_interval_s: float = DEFAULT_SLOW_INTERVAL_S,
        display_max_age_s: float = DEFAULT_DISPLAY_MAX_AGE_S,
    ) -> None:
        """Build the gateway.

        :param io: Register access.
        :param profile: Stove profile.
        :param clock: Returns the aware server time.
        :param tz: Local timezone of the stove clock.
        :param allow_unverified: Allow writes to unverified registers and recipes (simulator).
        :param slow_interval_s: Minimum seconds between two reads of the slow status tier.
        :param display_max_age_s: Age after which the last good display counts as stale.
        """
        self._io = io
        self._profile = profile
        self._tz = tz
        self._allow_unverified = allow_unverified
        self._reader = StatusReader(
            io,
            profile,
            clock,
            tz,
            slow_interval_s=slow_interval_s,
            display_max_age_s=display_max_age_s,
        )

    def read_status(self, *, fresh_slow: bool = False) -> StoveStatus:
        """Read one tiered status snapshot (reads are never gated).

        :param fresh_slow: Read the slow tier (ENERGY SAVING, live clock) now, not from its cache.
        :return: The decoded status, with ``polled_at`` at the middle of the essential read.
        :raises GatewayError: The essential tier could not be read.
        """
        if fresh_slow:
            self._reader.invalidate_slow()
        return self._reader.read()

    def status_degraded(self) -> bool:
        """Tell whether the last status read could not refresh the alarm display.

        :return: ``True`` when the status must be reported stale although it was read.
        """
        return self._reader.degraded

    def read_counters(self) -> MaintenanceInfo | None:
        """Read the maintenance counters.

        :return: The counters, or ``None`` when the profile maps none.
        """
        if not self._profile.counters:
            return None
        return decode_counters(self._profile, self._io.read(counter_targets(self._profile)))

    def read_register(self, name: str) -> float | None:
        """Read one mapped register.

        :param name: Register name.
        :return: The decoded value, or ``None`` when the register is not mapped.
        """
        register = self._profile.registers.get(name)
        if register is None:
            return None
        return register.decode(self._io.read([(register.memory, register.address)])[0])

    def can_write(self, name: str) -> bool:
        """Tell whether a register can be written now (mapped, writable and allowed).

        :param name: Register name.
        :return: ``True`` when ``write_register`` would not refuse it for its type or status.
        """
        register = self._profile.registers.get(name)
        if register is None:
            return False
        return register.writable and (register.verified or self._allow_unverified)

    def capabilities(self) -> StoveCapabilities:
        """Tell which writes would not be refused for an unverified register, recipe or clock.

        :return: Capability flags (no stove I/O).
        """
        clock = self._profile.clock
        return StoveCapabilities(
            start=self._can_execute("start"),
            stop=self._can_execute("stop"),
            setpoint=self.can_write("setpoint"),
            power=self.can_write("power_level"),
            energy_saving=self.can_write("energy_stop_offset")
            and self.can_write("energy_start_offset"),
            display_eco=self.can_write("display_eco"),
            clock_sync=clock is not None and (clock.set_verified or self._allow_unverified),
        )

    def read_settings(self, energy_saving: EnergySavingStatus | None) -> StoveSettings:
        """Read DISPLAY ECO and flag which adjustable settings can be written.

        :param energy_saving: ENERGY SAVING offsets from the status (``None`` when unmapped).
        :return: ENERGY SAVING and DISPLAY ECO values (``None`` when unmapped).
        """
        eco = self.read_register("display_eco")
        return StoveSettings(
            energy_saving=energy_saving,
            energy_stop_writable=self.can_write("energy_stop_offset"),
            energy_start_writable=self.can_write("energy_start_offset"),
            display_eco=None if eco is None else bool(eco),
            display_eco_writable=self.can_write("display_eco"),
        )

    def _can_execute(self, command: str) -> bool:
        """Tell whether a command recipe is mapped and verified (or unverified is allowed).

        :param command: Recipe name.
        :return: ``True`` when ``execute`` would not refuse it.
        """
        recipe = self._profile.commands.get(command)
        return recipe is not None and (recipe.verified or self._allow_unverified)

    def write_clock(self, local: datetime) -> list[str]:
        """Write only the BCD clock fields that differ.

        :param local: Local wall-clock time to set (aware values are converted to ``tz``).
        :return: Names of the written fields.
        :raises UnverifiedRegisterError: The clock layout is unverified and not allowed.
        :raises ValidationFailedError: The time cannot be encoded.
        :raises WriteOutcomeUnknownError: A field write could not be confirmed.
        """
        return self.apply_clock_writes(self.plan_clock_write(local))

    def plan_clock_write(self, local: datetime) -> list[ClockWrite]:
        """Read the BCD clock fields and list the writes that differ (nothing is written).

        :param local: Local wall-clock time to set (aware values are converted to ``tz``).
        :return: The changed fields in ``SET_FIELDS`` order.
        :raises UnverifiedRegisterError: The clock layout is unverified and not allowed.
        :raises ValidationFailedError: The time cannot be encoded.
        """
        clock = self._profile.clock
        if clock is None:
            raise ConfigurationError(f"Profile {self._profile.name!r} has no clock")
        if not clock.set_verified and not self._allow_unverified:
            raise UnverifiedRegisterError("clock")
        wanted = local.astimezone(self._tz) if local.tzinfo is not None else local
        try:
            encoded = encode_set_clock(wanted)
        except ValueError as exc:
            raise ValidationFailedError([f"clock: {exc}"]) from None
        fields = [name for name in SET_FIELDS if name in clock.set]
        current = self._io.read([(Memory.EEPROM, clock.set[name]) for name in fields])
        return [
            ClockWrite(name, WriteOp(Memory.EEPROM, clock.set[name], encoded[name]))
            for name, value in zip(fields, current, strict=True)
            if value != encoded[name]
        ]

    def apply_clock_writes(
        self, writes: Sequence[ClockWrite], *, max_rewrites: int = MAX_REWRITES
    ) -> list[str]:
        """Write planned clock bytes in order, one single-byte write each.

        :param writes: Writes from ``plan_clock_write`` (all, or a subset in the same order).
        :param max_rewrites: Rewrites allowed after an ambiguous write that the read-back refutes.
        :return: Names of the written fields.
        :raises WriteOutcomeUnknownError: A field write could not be confirmed.
        """
        if not writes:
            return []
        clock = self._profile.clock
        if clock is not None and not clock.set_verified:
            _LOGGER.warning("Writing the unverified clock layout (simulator mode)")
        self._reader.invalidate_slow()
        for write in writes:
            self._write_checked(write.op, f"clock.{write.name}", max_rewrites=max_rewrites)
        return [write.name for write in writes]

    def write_register(self, name: str, value: float | None) -> bool:
        """Write a register after reading it.

        :param name: Mapped register name.
        :param value: Physical value, or ``None`` for OFF on registers with an ``off_value``.
        :return: ``True`` when written, ``False`` when the register already held the value.
        :raises ValidationFailedError: Not writable, out of range, or no OFF encoding.
        :raises UnverifiedRegisterError: The register is unverified and not allowed.
        :raises WriteOutcomeUnknownError: The write could not be confirmed.
        """
        register = self._profile.register(name)
        raw = self._encode(register, value)
        if not register.verified and not self._allow_unverified:
            raise UnverifiedRegisterError(name)
        if self._io.read([(register.memory, register.address)])[0] == raw:
            return False
        if not register.verified:
            _LOGGER.warning("Writing unverified register %s (simulator mode)", name)
        if name in SLOW_REGISTERS:
            self._reader.invalidate_slow()
        self._write_checked(WriteOp(register.memory, register.address, raw), name)
        return True

    def execute(self, command: str) -> None:
        """Run a command recipe once: it is never resent, even after an ambiguous failure.

        On ``WriteOutcomeUnknownError`` nothing is read back or retried here; the caller must
        read the status and decide.

        :param command: Recipe name (for example ``start`` or ``stop``).
        :raises ConfigurationError: Unknown command.
        :raises UnverifiedRegisterError: The recipe is unverified and not allowed.
        :raises WriteOutcomeUnknownError: The outcome is unknown.
        """
        recipe = self._profile.commands.get(command)
        if recipe is None:
            raise ConfigurationError(f"Command {command!r} is not mapped")
        if not recipe.verified:
            if not self._allow_unverified:
                raise UnverifiedRegisterError(command)
            _LOGGER.warning("Executing unverified command %s (simulator mode)", command)
        try:
            self._io.write(recipe.writes)
        except WriteOutcomeUnknownError:
            _LOGGER.warning("Command %s has an unknown outcome and is not retried", command)
            raise
        _LOGGER.info("Command %s sent", command)

    @staticmethod
    def _encode(register: RegisterDef, value: float | None) -> int:
        """Validate a value and convert it to the raw byte (no I/O).

        :param register: Target register.
        :param value: Physical value or ``None`` for OFF.
        :return: Raw byte.
        """
        if not register.writable:
            raise ValidationFailedError([f"{register.name} is not writable"])
        if value is None:
            if register.off_value is None:
                raise ValidationFailedError([f"{register.name} has no OFF value"])
            return register.off_value
        try:
            raw = register.encode(value)
        except ValueError as exc:
            raise ValidationFailedError([str(exc)]) from None
        if abs(register.decode(raw) - value) > 1e-9:
            raise ValidationFailedError([f"{register.name}: {value} is not representable"])
        return raw

    def _write_checked(self, op: WriteOp, label: str, *, max_rewrites: int = MAX_REWRITES) -> None:
        """Write one byte, reading back after an ambiguous failure (see ``retry``).

        :param op: The write.
        :param label: Register or field name for logs and errors.
        :param max_rewrites: Rewrites allowed after an ambiguous write that the read-back refutes.
        """
        write_with_readback(
            partial(self._io.write, [op]),
            partial(self._is_applied, op),
            label=label,
            max_rewrites=max_rewrites,
        )

    def _is_applied(self, op: WriteOp) -> bool:
        """Read a register back and compare it with the written value.

        :param op: The write to check.
        :return: ``True`` when the register holds the value.
        """
        return self._io.read([(op.memory, op.address)])[0] == op.value
