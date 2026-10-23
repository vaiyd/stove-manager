"""Pure date and time formatting for the UI (ASCII French, local wall-clock times)."""

from datetime import datetime

from stove_pilot.service.ui.i18n import t


def parse_datetime(value: str | None) -> datetime | None:
    """Parse an ISO 8601 text.

    :param value: ISO text, or ``None``.
    :return: The datetime, or ``None`` when absent or malformed.
    """
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def localize(value: datetime, now: datetime) -> datetime:
    """Express an aware datetime in the timezone of ``now`` (naive values are kept as they are).

    :param value: Datetime to convert.
    :param now: Reference local time.
    :return: The datetime in the local timezone.
    """
    if value.tzinfo is None or now.tzinfo is None:
        return value
    return value.astimezone(now.tzinfo)


def hhmm(value: datetime) -> str:
    """Format a time of day.

    :param value: Datetime.
    :return: ``HH:MM``.
    """
    return f"{value:%H:%M}"


def day_label(value: datetime, now: datetime) -> str:
    """Name a day relative to now: today, tomorrow, otherwise the weekday.

    :param value: Local datetime.
    :param now: Local current time.
    :return: French label without accents.
    """
    delta_days = (value.date() - now.date()).days
    if delta_days == 0:
        return t("day.today")
    if delta_days == 1:
        return t("day.tomorrow")
    return t(f"weekday.{value.weekday()}")


def when_label(value: datetime, now: datetime) -> str:
    """Format a future moment as a day label and a time.

    :param value: Datetime to describe.
    :param now: Local current time.
    :return: For example ``demain 06:30``.
    """
    local = localize(value, now)
    return f"{day_label(local, now)} {hhmm(local)}"


def since_label(value: datetime, now: datetime) -> str:
    """Format a past or far moment: time only today, ``dd/mm HH:MM`` otherwise.

    :param value: Datetime to describe.
    :param now: Local current time.
    :return: The short label.
    """
    local = localize(value, now)
    if local.date() == now.date():
        return hhmm(local)
    return f"{local:%d/%m} {hhmm(local)}"
