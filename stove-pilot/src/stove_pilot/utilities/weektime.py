"""Pure week and time helpers: weekdays, HH:MM text, minute of week, BCD bytes."""

import re
from collections.abc import Iterable
from datetime import datetime
from enum import IntEnum
from typing import Final

MINUTES_PER_DAY: Final = 24 * 60
MINUTES_PER_WEEK: Final = 7 * MINUTES_PER_DAY
_HHMM_PATTERN: Final = re.compile(r"(\d{1,2}):(\d{2})")


class Weekday(IntEnum):
    """Day of the week, Monday first (matches ``datetime.weekday()``)."""

    MON = 0
    TUE = 1
    WED = 2
    THU = 3
    FRI = 4
    SAT = 5
    SUN = 6

    @property
    def key(self) -> str:
        """Lowercase three-letter key (``"mon"``)."""
        return self.name.lower()

    @classmethod
    def from_key(cls, key: str) -> "Weekday":
        """Build a weekday from its lowercase key.

        :param key: Key such as ``"mon"``.
        :return: The matching weekday.
        :raises ValueError: When the key is unknown.
        """
        try:
            return cls[key.upper()]
        except KeyError:
            raise ValueError(f"Unknown weekday key {key!r}") from None


def parse_hhmm(text: str) -> int:
    """Parse ``HH:MM`` into minutes since midnight (``"24:00"`` gives 1440).

    :param text: Time text, 00:00 to 24:00.
    :return: Minutes since midnight.
    :raises ValueError: When the text is not a valid time.
    """
    match = _HHMM_PATTERN.fullmatch(text.strip())
    if match is None:
        raise ValueError(f"Invalid time {text!r}, expected HH:MM")
    hours, minutes = int(match.group(1)), int(match.group(2))
    total = hours * 60 + minutes
    if minutes > 59 or total > MINUTES_PER_DAY:
        raise ValueError(f"Time out of range: {text!r}")
    return total


def format_hhmm(minutes: int) -> str:
    """Format minutes since midnight as ``HH:MM`` (1440 gives ``"24:00"``).

    :param minutes: Minutes since midnight, 0 to 1440.
    :return: Zero-padded time text.
    :raises ValueError: When the value is outside 0-1440.
    """
    if not 0 <= minutes <= MINUTES_PER_DAY:
        raise ValueError(f"Minutes out of range: {minutes}")
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def minute_of_week(local: datetime) -> int:
    """Return the minute index since Monday 00:00.

    :param local: Local wall-clock time.
    :return: Value from 0 to 10079.
    """
    return local.weekday() * MINUTES_PER_DAY + local.hour * 60 + local.minute


def days_to_mask(days: Iterable[Weekday]) -> int:
    """Encode weekdays as a bit mask (bit 0 = Monday ... bit 6 = Sunday).

    :param days: Weekdays to set.
    :return: Mask from 0 to 127.
    """
    mask = 0
    for day in days:
        mask |= 1 << day.value
    return mask


def days_from_mask(mask: int) -> frozenset[Weekday]:
    """Decode a weekday bit mask (bit 0 = Monday ... bit 6 = Sunday).

    :param mask: Mask from 0 to 127.
    :return: The weekdays whose bit is set.
    :raises ValueError: When a bit above bit 6 is set.
    """
    if not 0 <= mask <= 0x7F:
        raise ValueError(f"Day mask out of range: {mask}")
    return frozenset(day for day in Weekday if mask >> day.value & 1)


def to_bcd(value: int) -> int:
    """Encode 0-99 as one BCD byte.

    :param value: Decimal value.
    :return: BCD byte (``17`` gives ``0x17``).
    :raises ValueError: When the value is outside 0-99.
    """
    if not 0 <= value <= 99:
        raise ValueError(f"BCD value out of range: {value}")
    return (value // 10) << 4 | value % 10


def from_bcd(value: int) -> int | None:
    """Decode one BCD byte.

    :param value: BCD byte.
    :return: Decimal value, or ``None`` when a nibble is not a decimal digit.
    """
    high, low = value >> 4, value & 0x0F
    return high * 10 + low if high <= 9 and low <= 9 else None
