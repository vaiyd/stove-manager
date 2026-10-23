"""Pure encoding of the Micronova stove clock (RAM live bytes, EEPROM BCD bytes)."""

from collections.abc import Mapping
from datetime import datetime
from typing import Final

from stove_pilot.utilities.weektime import from_bcd, to_bcd

SET_FIELDS: Final = ("weekday", "hour", "minute", "day", "month", "year")
_BASE_YEAR: Final = 2000


def encode_weekday(moment: datetime) -> int:
    """Encode the weekday as the board does (Sunday = 1, Monday = 2, Thursday = 5).

    :param moment: Local date.
    :return: Weekday code 1-7.
    """
    return (moment.weekday() + 1) % 7 + 1


def decode_live_clock(raw: Mapping[str, int]) -> datetime | None:
    """Build the naive stove time from the RAM live clock bytes.

    :param raw: Bytes keyed by ``second``, ``hour``, ``minute``, ``day``, ``month``, ``year``.
    :return: Stove time, or ``None`` when a field is missing or invalid.
    """
    try:
        return datetime(
            _BASE_YEAR + raw["year"],
            raw["month"],
            raw["day"],
            raw["hour"],
            raw["minute"],
            raw["second"],
        )
    except (KeyError, ValueError):
        return None


def encode_set_clock(local: datetime) -> dict[str, int]:
    """Encode a local time as the EEPROM clock-setting bytes (BCD).

    :param local: Local wall-clock time.
    :return: Byte per field of ``SET_FIELDS``.
    :raises ValueError: When the year is outside 2000-2099.
    """
    return {
        "weekday": encode_weekday(local),
        "hour": to_bcd(local.hour),
        "minute": to_bcd(local.minute),
        "day": to_bcd(local.day),
        "month": to_bcd(local.month),
        "year": to_bcd(local.year - _BASE_YEAR),
    }


def decode_set_clock(raw: Mapping[str, int]) -> datetime | None:
    """Decode the EEPROM clock-setting bytes (seconds are set to 0).

    :param raw: Bytes keyed by ``hour``, ``minute``, ``day``, ``month``, ``year``.
    :return: Naive time, or ``None`` when a field is missing or invalid.
    """
    try:
        year, month, day, hour, minute = (
            from_bcd(raw[key]) for key in ("year", "month", "day", "hour", "minute")
        )
        if None in (year, month, day, hour, minute):
            return None
        return datetime(_BASE_YEAR + year, month, day, hour, minute)
    except (KeyError, ValueError, TypeError):
        return None
