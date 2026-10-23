"""Request parsing helpers: JSON bodies into DTOs and typed query parameters."""

import math
from datetime import UTC, datetime, tzinfo

from flask import request
from pydantic import BaseModel

from stove_pilot.common.exceptions import ValidationFailedError


def parse_body[ModelT: BaseModel](model: type[ModelT], *, optional: bool = False) -> ModelT:
    """Parse the JSON request body (any content type) into a DTO.

    :param model: Request DTO class.
    :param optional: Accept an empty body and return the DTO defaults.
    :return: The validated DTO.
    :raises ValidationFailedError: The body is missing, not a JSON object, or not valid JSON.
    :raises pydantic.ValidationError: The object does not match the DTO (mapped to 422).
    """
    if not request.get_data(cache=True).strip():
        if optional:
            return model()
        raise ValidationFailedError(["request body is required"])
    payload = request.get_json(force=True, silent=True)
    if not isinstance(payload, dict):
        raise ValidationFailedError(["request body must be a JSON object"])
    return model.model_validate(payload)


def query_float(name: str, *, minimum: float | None = None) -> float | None:
    """Read an optional finite float query parameter.

    :param name: Parameter name.
    :param minimum: Smallest accepted value.
    :return: The value, or ``None`` when absent.
    :raises ValidationFailedError: The value is not a finite number or is below ``minimum``.
    """
    raw = request.args.get(name)
    if raw is None or raw == "":
        return None
    try:
        value = float(raw)
    except ValueError:
        raise ValidationFailedError([f"{name} must be a number"]) from None
    if not math.isfinite(value) or (minimum is not None and value < minimum):
        bound = "" if minimum is None else f" >= {minimum:g}"
        raise ValidationFailedError([f"{name} must be a finite number{bound}"])
    return value


def query_int(name: str, *, default: int, minimum: int, maximum: int) -> int:
    """Read an integer query parameter with a default and bounds.

    :param name: Parameter name.
    :param default: Value used when absent.
    :param minimum: Smallest accepted value.
    :param maximum: Largest accepted value.
    :return: The value.
    :raises ValidationFailedError: Not an integer or out of bounds.
    """
    raw = request.args.get(name)
    if raw is None or raw == "":
        return default
    try:
        value = int(raw)
    except ValueError:
        raise ValidationFailedError([f"{name} must be an integer"]) from None
    if not minimum <= value <= maximum:
        raise ValidationFailedError([f"{name} must be between {minimum} and {maximum}"])
    return value


def query_optional_int(name: str, *, minimum: int, maximum: int) -> int | None:
    """Read an optional integer query parameter with bounds.

    :param name: Parameter name.
    :param minimum: Smallest accepted value.
    :param maximum: Largest accepted value.
    :return: The value, or ``None`` when absent.
    :raises ValidationFailedError: Not an integer or out of bounds.
    """
    if request.args.get(name, "") == "":
        return None
    return query_int(name, default=minimum, minimum=minimum, maximum=maximum)


def query_datetime(name: str, tz: tzinfo) -> datetime | None:
    """Read an optional ISO 8601 datetime query parameter (naive values use ``tz``).

    :param name: Parameter name.
    :param tz: Timezone applied to naive values.
    :return: Aware datetime, or ``None`` when absent.
    :raises ValidationFailedError: The value is not an ISO 8601 datetime.
    """
    raw = request.args.get(name)
    if raw is None or raw == "":
        return None
    try:
        value = datetime.fromisoformat(raw)
    except ValueError:
        raise ValidationFailedError([f"{name} must be an ISO 8601 datetime"]) from None
    aware = value if value.tzinfo is not None else value.replace(tzinfo=tz)
    try:
        # Reason: the stores compare in UTC; reject values that overflow that conversion.
        aware.astimezone(UTC)
    except OverflowError:
        raise ValidationFailedError([f"{name} is out of range"]) from None
    return aware
