"""Chrono EEPROM adapter: raw reads of the layout bytes and read-back guarded byte writes."""

from collections.abc import Mapping
from functools import partial

from stove_pilot.core.chrono.layout import ChronoLayout
from stove_pilot.core.stove.models import WriteOp
from stove_pilot.core.stove.ports import RegisterIO
from stove_pilot.core.stove.retry import write_with_readback
from stove_pilot.lib.navel.rwms import Memory


class ChronoGateway:
    """Reads and writes the chrono bytes of one layout (the clock is not handled here)."""

    def __init__(self, io: RegisterIO, layout: ChronoLayout) -> None:
        """Bind the register access and the layout.

        :param io: Register access.
        :param layout: Chrono layout (only its addresses are used).
        """
        self._io = io
        self._layout = layout

    def read_raw(self) -> dict[int, int]:
        """Read every EEPROM byte of the layout.

        :return: Byte values keyed by address.
        :raises GatewayError: A read kept failing.
        """
        addresses = self._layout.all_addresses()
        values = self._io.read([(Memory.EEPROM, address) for address in addresses])
        return dict(zip(addresses, values, strict=True))

    def write_bytes(self, changes: Mapping[int, int]) -> None:
        """Write EEPROM bytes, reading back after an ambiguous failure.

        Order: enable bytes going to 0, then program bytes, then enable bytes going to 1, so a
        partial failure never leaves an enabled block with half-written programs.

        :param changes: New byte values keyed by address (only changed bytes).
        :raises WriteOutcomeUnknownError: A byte could not be confirmed.
        """
        for address in sorted(changes, key=lambda a: self._phase(a, changes[a])):
            op = WriteOp(Memory.EEPROM, address, changes[address])
            write_with_readback(
                partial(self._io.write, [op]),
                partial(self._is_applied, op),
                label=f"chrono 0x{address:02X}",
            )

    def _phase(self, address: int, value: int) -> tuple[int, int]:
        """Sort key of a write: disable first, programs next, enable last, then by address.

        :param address: Byte address.
        :param value: Byte value to write.
        :return: Sort key.
        """
        if address not in {block.enable for block in self._layout.blocks}:
            return 1, address
        return (0 if value == 0 else 2), address

    def _is_applied(self, op: WriteOp) -> bool:
        """Read a byte back and compare it with the written value.

        :param op: The write to check.
        :return: ``True`` when the byte holds the value.
        """
        return self._io.read([(op.memory, op.address)])[0] == op.value
