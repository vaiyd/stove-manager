"""Micronova register item encoding for the Navel ``RWMSmaster`` protocol."""

from enum import StrEnum
from typing import Final

_EEPROM_FLAG: Final = 0x8000


class Memory(StrEnum):
    """Micronova board memory areas."""

    RAM = "ram"
    EEPROM = "eeprom"


def encode_item(memory: Memory, address: int) -> int:
    """Encode a register as a Navel item (RAM: address, EEPROM: 0x8000 | address).

    :param memory: Memory area.
    :param address: Register address (0-255).
    :return: Item value, identical for reads and writes.
    """
    if not 0 <= address <= 0xFF:
        raise ValueError(f"Register address out of range: {address}")
    return _EEPROM_FLAG | address if memory is Memory.EEPROM else address
