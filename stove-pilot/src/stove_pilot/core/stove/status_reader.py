"""Tiered status reads: the essential tier fails the read, the display and slow tiers degrade.

Essential (ambient, fumes, state, setpoint, power) is read on every poll and any failure is
raised. The display buffer (alarm and messages) is read on every poll too, but a failure keeps
the previous bytes and is counted. The slow tier (ENERGY SAVING offsets, live clock) is read at
most once per interval, and again right after a write that changes it.
"""

import logging
from collections.abc import Callable
from datetime import datetime, timedelta, tzinfo
from typing import Final

from stove_pilot.common.exceptions import GatewayError
from stove_pilot.core.stove.decoding import StatusPlan, build_status, status_tiers
from stove_pilot.core.stove.models import StoveProfile, StoveStatus
from stove_pilot.core.stove.ports import RegisterIO

DEFAULT_SLOW_INTERVAL_S: Final = 300.0
DEFAULT_DISPLAY_MAX_AGE_S: Final = 90.0
# Reason: three failed display reads in a row mean the alarm information can no longer be trusted.
DISPLAY_MAX_FAILURES: Final = 3
# Reason: a failing slow tier must not add its retry delays to every poll.
SLOW_RETRY_S: Final = 60.0

_LOGGER = logging.getLogger(__name__)


class StatusReader:
    """Reads the status tier by tier; callers must serialize ``read`` and ``invalidate_slow``."""

    def __init__(
        self,
        io: RegisterIO,
        profile: StoveProfile,
        clock: Callable[[], datetime],
        tz: tzinfo,
        *,
        slow_interval_s: float = DEFAULT_SLOW_INTERVAL_S,
        display_max_age_s: float = DEFAULT_DISPLAY_MAX_AGE_S,
    ) -> None:
        """Bind the register access and the tier limits.

        :param io: Register access.
        :param profile: Stove profile.
        :param clock: Returns the aware server time.
        :param tz: Local timezone of the stove clock.
        :param slow_interval_s: Minimum seconds between two reads of the slow tier.
        :param display_max_age_s: Age after which the last good display counts as stale.
        """
        self._io = io
        self._profile = profile
        self._tiers = status_tiers(profile)
        self._clock = clock
        self._tz = tz
        self._slow_interval = timedelta(seconds=slow_interval_s)
        self._display_max_age = timedelta(seconds=display_max_age_s)
        self._display_raw: dict[str, int] = {}
        self._display_at: datetime | None = None
        self._display_failures = 0
        self._slow_raw: dict[str, int] = {}
        self._slow_at: datetime | None = None
        self._slow_invalid = False
        self._slow_retry_at: datetime | None = None
        self._degraded = False

    @property
    def degraded(self) -> bool:
        """Whether the last read could not refresh the display, so the status must be stale."""
        return self._degraded

    def invalidate_slow(self) -> None:
        """Force the slow tier to be read again by the next status read."""
        self._slow_invalid = True
        self._slow_retry_at = None

    def read(self) -> StoveStatus:
        """Read one status snapshot.

        :return: The decoded status; ``polled_at`` is the middle of the essential read.
        :raises GatewayError: The essential tier could not be read.
        """
        raw, now_local = self._fetch(self._tiers.essential)
        self._read_display()
        self._read_slow()
        self._update_degraded()
        raw.update(self._display_raw)
        raw.update(self._slow_raw)
        return build_status(
            self._profile, raw, now_local=now_local, clock_read_at=self._slow_at
        )

    def _fetch(self, plan: StatusPlan) -> tuple[dict[str, int], datetime]:
        """Read one tier.

        :param plan: Entries of the tier.
        :return: Raw values keyed by entry key, and the aware local time at the middle of the read.
        :raises GatewayError: A chunk kept failing.
        """
        before = self._clock()
        values = self._io.read([(memory, address) for _, memory, address in plan])
        after = self._clock()
        raw = {key: value for (key, _, _), value in zip(plan, values, strict=True)}
        return raw, (before + (after - before) / 2).astimezone(self._tz)

    def _read_display(self) -> None:
        """Refresh the display bytes; a failure keeps the previous ones and is counted."""
        if not self._tiers.display:
            return
        try:
            self._display_raw, self._display_at = self._fetch(self._tiers.display)
        except GatewayError as exc:
            self._display_failures += 1
            _LOGGER.info(
                "Display read failed (%s); keeping the previous display (%d in a row)",
                type(exc).__name__,
                self._display_failures,
            )
            return
        self._display_failures = 0

    def _read_slow(self) -> None:
        """Refresh the slow tier when due; a failure keeps the previous values and retries later."""
        if not self._tiers.slow:
            return
        now = self._clock()
        due = (
            self._slow_invalid
            or self._slow_at is None
            or now - self._slow_at >= self._slow_interval
        )
        if not due or (self._slow_retry_at is not None and now < self._slow_retry_at):
            return
        try:
            self._slow_raw, self._slow_at = self._fetch(self._tiers.slow)
        except GatewayError as exc:
            _LOGGER.info("Slow tier read failed (%s); retrying later", type(exc).__name__)
            self._slow_retry_at = now + timedelta(seconds=SLOW_RETRY_S)
            if self._slow_invalid:
                # Reason: a write changed these registers, so the previous values are known wrong.
                self._slow_raw, self._slow_at = {}, None
            return
        self._slow_invalid = False
        self._slow_retry_at = None

    def _update_degraded(self) -> None:
        """Flag the status stale when the display is missing, too old or failed too often."""
        if not self._tiers.display:
            return
        age = None if self._display_at is None else self._clock() - self._display_at
        degraded = (
            self._display_failures >= DISPLAY_MAX_FAILURES
            or age is None
            or age > self._display_max_age
        )
        if degraded and not self._degraded:
            _LOGGER.warning(
                "Display unreadable (%d failures in a row): the status is flagged stale",
                self._display_failures,
            )
        elif self._degraded and not degraded:
            _LOGGER.info("Display read recovered: the status is no longer flagged stale")
        self._degraded = degraded
