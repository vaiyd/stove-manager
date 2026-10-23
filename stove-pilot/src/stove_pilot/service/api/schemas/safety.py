"""Safety settings and episode DTOs."""

import math
from datetime import datetime
from typing import Annotated, Self

from pydantic import BaseModel, BeforeValidator, StrictBool

from stove_pilot.core.safety.models import SafetyConfig, SafetyReport
from stove_pilot.service.api.schemas.common import RequestModel


def _whole_number(value: object) -> int:
    """Accept an int or a whole float. Reject bools, strings and fractions.

    :param value: Raw JSON value.
    :return: The integer value.
    :raises ValueError: The value is not a whole number.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("must be a whole number")
    if isinstance(value, float):
        if not math.isfinite(value) or not value.is_integer():
            raise ValueError("must be a whole number")
        return int(value)
    return value


WholeNumber = Annotated[int, BeforeValidator(_whole_number)]


class SafetyUpdateRequest(RequestModel):
    """Body of ``PUT /safety``. Ranges are checked by the safety repository."""

    enabled: StrictBool
    threshold_c: WholeNumber
    max_attempts: WholeNumber
    retry_interval_min: WholeNumber


class SafetyDto(BaseModel):
    """Safety card, bounds, and the derived episode status."""

    enabled: bool
    threshold_c: int
    threshold_min_c: int
    threshold_max_c: int
    status: str
    attempts: int
    max_attempts: int
    max_attempts_min: int
    max_attempts_max: int
    retry_interval_min: int
    retry_interval_min_min: int
    retry_interval_max_min: int
    last_attempt_at: datetime | None
    next_attempt_at: datetime | None
    failure_reason: str | None
    paused_reason: str | None
    last_check_at: datetime | None
    warning: str | None

    @classmethod
    def from_domain(cls, report: SafetyReport) -> Self:
        """Map a safety report.

        :param report: Domain report.
        :return: The DTO.
        """
        config = report.config
        state = report.state
        return cls(
            enabled=config.enabled,
            threshold_c=config.threshold_c,
            threshold_min_c=report.threshold_min_c,
            threshold_max_c=report.threshold_max_c,
            status=report.status.value,
            attempts=state.attempts,
            max_attempts=config.max_attempts,
            max_attempts_min=report.max_attempts_min,
            max_attempts_max=report.max_attempts_max,
            retry_interval_min=config.retry_interval_min,
            retry_interval_min_min=report.retry_interval_min_min,
            retry_interval_max_min=report.retry_interval_max_min,
            last_attempt_at=state.last_attempt_at,
            next_attempt_at=state.next_attempt_at,
            failure_reason=state.failure_reason,
            paused_reason=state.paused_reason,
            last_check_at=state.last_check_at,
            warning=report.warning,
        )


def safety_config(body: SafetyUpdateRequest) -> SafetyConfig:
    """Copy a validated request into a safety card.

    :param body: Parsed request body.
    :return: The card to store.
    """
    return SafetyConfig(
        enabled=body.enabled,
        threshold_c=body.threshold_c,
        max_attempts=body.max_attempts,
        retry_interval_min=body.retry_interval_min,
    )
