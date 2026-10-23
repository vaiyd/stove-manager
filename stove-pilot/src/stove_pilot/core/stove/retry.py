"""Retry and read-back policy for the unreliable Navel module (single home of the limits)."""

import logging
from collections.abc import Callable, Sequence
from typing import Final

from stove_pilot.common.exceptions import (
    GatewayError,
    GatewayProtocolError,
    GatewayUnavailableError,
    WriteOutcomeUnknownError,
)

# Reason: the module returns blank replies for larger batches (long request URLs); this is the
# default maximum, lowered at runtime by ``batching.AdaptiveBatch``.
CHUNK_SIZE: Final = 8
READ_MAX_ATTEMPTS: Final = 3
READ_BACKOFF_S: Final = (0.5, 1.0)
# Reason: 1 initial write plus at most 2 rewrites gives 3 write attempts overall.
MAX_REWRITES: Final = 2

_LOGGER = logging.getLogger(__name__)


def retry_read(
    operation: Callable[[], list[int]],
    *,
    sleep: Callable[[float], None],
    delays: Sequence[float] = READ_BACKOFF_S,
    max_attempts: int = READ_MAX_ATTEMPTS,
    fallback: Callable[[], list[int]] | None = None,
) -> list[int]:
    """Run an idempotent read, retrying transient transport and protocol errors.

    A NACK is not retried here: the client already re-identifies once.

    :param operation: One read attempt.
    :param sleep: Sleep function used between attempts.
    :param delays: Delay after the first, second, ... failed attempt.
    :param max_attempts: Total attempts.
    :param fallback: Next attempt after a protocol error (for example a split read); final.
    :return: The values of the first successful attempt.
    :raises GatewayError: The last error after the final attempt.
    """
    for attempt in range(1, max_attempts + 1):
        try:
            return operation()
        except (GatewayUnavailableError, GatewayProtocolError) as exc:
            _LOGGER.warning(
                "Read attempt %d/%d failed (%s)", attempt, max_attempts, type(exc).__name__
            )
            if attempt == max_attempts:
                raise
            if attempt <= len(delays):
                sleep(delays[attempt - 1])
            if fallback is not None and isinstance(exc, GatewayProtocolError):
                return fallback()
    raise AssertionError("unreachable: max_attempts must be at least 1")


def write_with_readback(
    write_once: Callable[[], None],
    is_applied: Callable[[], bool],
    *,
    label: str,
    max_rewrites: int = MAX_REWRITES,
) -> None:
    """Write once; after an ambiguous failure read back and rewrite only if not applied.

    :param write_once: One write attempt, raising ``WriteOutcomeUnknownError`` when ambiguous.
    :param is_applied: Reads the target back and tells whether it holds the target value.
    :param label: Register or field name used in logs and errors.
    :param max_rewrites: Rewrites allowed after the first attempt.
    :raises WriteOutcomeUnknownError: Still not applied, or the read-back itself failed.
    """
    attempts = 1 + max_rewrites
    for attempt in range(1, attempts + 1):
        try:
            write_once()
            return
        except WriteOutcomeUnknownError as exc:
            last = exc
        try:
            applied = is_applied()
        except GatewayError as exc:
            # Reason: without a read-back, a rewrite would be blind.
            raise WriteOutcomeUnknownError(
                f"{label}: write outcome unknown and the read-back failed ({type(exc).__name__})"
            ) from exc
        if applied:
            _LOGGER.warning("Write of %s had an unknown outcome but was applied", label)
            return
        _LOGGER.warning("Write of %s not applied (attempt %d/%d)", label, attempt, attempts)
    raise WriteOutcomeUnknownError(
        f"{label}: write not confirmed after {attempts} attempts; check the stove state"
    ) from last
