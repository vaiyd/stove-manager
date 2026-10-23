"""Pure input validation and clock-sync timing rules for stove settings."""

from datetime import datetime, timedelta
from typing import Final

from stove_pilot.common.exceptions import ValidationFailedError

# Reason: mirrors the profile min/max of the ENERGY SAVING offsets; the gateway validates again.
ENERGY_OFFSET_MIN_C: Final = 1
ENERGY_OFFSET_MAX_C: Final = 3


def validate_energy_saving(stop_offset_c: int | None, start_offset_c: int | None) -> None:
    """Validate ENERGY SAVING offsets before any register is written.

    The START offset has no OFF encoding on this stove, so ``None`` is refused for it.

    :param stop_offset_c: STOP offset in degrees (1..3), or ``None`` for OFF.
    :param start_offset_c: START offset in degrees (1..3); OFF is unsupported.
    :raises ValidationFailedError: Out of range or unsupported OFF.
    """
    issues: list[str] = []
    if stop_offset_c is not None and not _in_range(stop_offset_c):
        issues.append(f"stop_offset_c must be {ENERGY_OFFSET_MIN_C}..{ENERGY_OFFSET_MAX_C} or None")
    if start_offset_c is None:
        issues.append("start_offset_c has no OFF value on this stove")
    elif not _in_range(start_offset_c):
        issues.append(f"start_offset_c must be {ENERGY_OFFSET_MIN_C}..{ENERGY_OFFSET_MAX_C}")
    if issues:
        raise ValidationFailedError(issues)


def validate_energy_stop(offset_c: int | None) -> None:
    """Validate the ENERGY SAVING STOP offset alone.

    :param offset_c: STOP offset in degrees (1..3), or ``None`` for OFF.
    :raises ValidationFailedError: Out of range.
    """
    if offset_c is not None and not _in_range(offset_c):
        raise ValidationFailedError(
            [f"stop_offset_c must be {ENERGY_OFFSET_MIN_C}..{ENERGY_OFFSET_MAX_C} or None"]
        )


def clock_sync_due(
    drift_s: float | None,
    *,
    tolerance_s: float,
    last_attempt_at: datetime | None,
    now: datetime,
    min_interval: timedelta,
) -> bool:
    """Tell whether an automatic clock sync should run now.

    :param drift_s: Stove minus server time in seconds (``None`` when unknown).
    :param tolerance_s: Maximum accepted absolute drift.
    :param last_attempt_at: Time of the last automatic attempt, if any.
    :param now: Current time (aware).
    :param min_interval: Minimum time between two automatic attempts.
    :return: ``True`` when the drift exceeds the tolerance and the interval has elapsed.
    """
    if drift_s is None or abs(drift_s) <= tolerance_s:
        return False
    return last_attempt_at is None or now - last_attempt_at >= min_interval


def _in_range(value: int) -> bool:
    return (
        isinstance(value, int)
        and not isinstance(value, bool)
        and ENERGY_OFFSET_MIN_C <= value <= ENERGY_OFFSET_MAX_C
    )
