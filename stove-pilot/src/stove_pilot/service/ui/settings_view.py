"""Reglages page view models built from API payloads (no I/O, French texts via ``t``)."""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Final, Self

from stove_pilot.service.ui.formatting import hhmm, parse_datetime, since_label
from stove_pilot.service.ui.i18n import t
from stove_pilot.service.ui.viewmodels import DRIFT_BADGE_THRESHOLD_S

SERVICE_INTERVAL_H: Final = 2000
MAX_ALARMS: Final = 5
OFFSET_CHOICES: Final = (1, 2, 3)
DEFAULT_STOP_C: Final = 1
DEFAULT_START_C: Final = 1
STATE_OK: Final = "ok"
STATE_ABSENT: Final = "absent"
STATE_ERROR: Final = "error"
_SECONDS_PER_MINUTE: Final = 60
_SYNC_DRIFT_MIN_S: Final = 60


@dataclass(frozen=True, slots=True)
class EnergyView:
    """The energy saving form (``stop`` is ``None`` when the automatic stop is OFF).

    ``locked`` is set while a vacation owns the registers.
    """

    state: str
    stop: int | None = None
    start: int = DEFAULT_START_C
    writable: bool = False
    locked: bool = False

    @property
    def enabled(self) -> bool:
        """Whether the stove stops by itself above the setpoint."""
        return self.stop is not None

    @property
    def offsets(self) -> tuple[int, ...]:
        """The offsets the stove accepts."""
        return OFFSET_CHOICES

    @property
    def sentence(self) -> str:
        """The explanation of the current values."""
        return energy_sentence(self.stop, self.start)

    @classmethod
    def from_api(cls, payload: dict[str, Any] | None, state: str) -> Self:
        """Map the ``energy_saving`` member of ``GET /stove/settings``.

        :param payload: The member, or ``None`` when the registers are not mapped.
        :param state: Fetch state of the settings answer.
        :return: The view (absent when the registers are not mapped).
        """
        if state != STATE_OK or payload is None:
            return cls(state if state != STATE_OK else STATE_ABSENT)
        stop, start = payload.get("stop_offset_c"), payload.get("start_offset_c")
        return cls(
            STATE_OK,
            stop if isinstance(stop, int) else None,
            start if isinstance(start, int) else DEFAULT_START_C,
            bool(payload.get("writable", False)),
        )


def energy_sentence(stop: int | None, start: int) -> str:
    """Explain the energy saving offsets.

    :param stop: Stop offset above the setpoint, or ``None`` for OFF.
    :param start: Restart offset below the setpoint.
    :return: The French sentence.
    """
    if stop is None:
        return t("settings.energy.sentence_off")
    return t("settings.energy.sentence", stop=stop, start=start)


@dataclass(frozen=True, slots=True)
class DisplayEcoView:
    """The display eco toggle."""

    state: str
    enabled: bool = False
    writable: bool = False

    @classmethod
    def from_api(cls, payload: dict[str, Any] | None, state: str) -> Self:
        """Map the ``display_eco`` member of ``GET /stove/settings``.

        :param payload: The member, or ``None`` when the register is not mapped.
        :param state: Fetch state of the settings answer.
        :return: The view (absent when the register is not mapped).
        """
        if state != STATE_OK or payload is None:
            return cls(state if state != STATE_OK else STATE_ABSENT)
        return cls(STATE_OK, bool(payload.get("enabled", False)), bool(payload.get("writable")))


@dataclass(frozen=True, slots=True)
class ClockCardView:
    """The stove clock card."""

    state: str
    time_text: str = ""
    drift_text: str = ""
    drift_warn: bool = False
    auto_sync: bool = False
    auto_sync_writable: bool = False
    auto_sync_text: str = ""
    last_sync_text: str = ""

    @classmethod
    def from_api(cls, payload: dict[str, Any] | None, state: str, now: datetime) -> Self:
        """Map a ``ClockStatusDto`` payload.

        :param payload: Clock payload, or ``None`` when unavailable.
        :param state: Fetch state of the answer.
        :param now: Local current time.
        :return: The view.
        """
        if state != STATE_OK or payload is None:
            return cls(state if state != STATE_OK else STATE_ERROR)
        stove_time = parse_datetime(payload.get("stove_time"))
        drift_s = float(payload.get("drift_s", 0))
        last_sync = parse_datetime(payload.get("last_sync_at"))
        auto_sync = bool(payload.get("auto_sync"))
        return cls(
            STATE_OK,
            hhmm(stove_time) if stove_time else t("common.none"),
            _drift_text(drift_s),
            abs(drift_s) > DRIFT_BADGE_THRESHOLD_S,
            auto_sync,
            bool(payload.get("auto_sync_writable")),
            _auto_sync_text(auto_sync, payload.get("drift_tolerance_s")),
            t("settings.clock.never") if last_sync is None else since_label(last_sync, now),
        )


def _auto_sync_text(auto_sync: bool, tolerance_s: Any) -> str:
    """Describe the auto-sync rule with the tolerance reported by the API.

    :param auto_sync: Whether the automatic sync is on.
    :param tolerance_s: ``drift_tolerance_s`` of the clock payload (seconds).
    :return: The French sentence (the off text, or the rule with the tolerance in minutes).
    """
    if not auto_sync:
        return t("settings.clock.auto_off")
    if isinstance(tolerance_s, bool) or not isinstance(tolerance_s, int | float):
        return t("settings.clock.auto_toggle")
    minutes = f"{tolerance_s / _SECONDS_PER_MINUTE:g}".replace(".", ",")
    return t("settings.clock.auto_on", minutes=minutes)


def _drift_text(drift_s: float) -> str:
    """Describe the stove clock drift.

    :param drift_s: Stove minus server time in seconds.
    :return: ``a l'heure`` or a signed number of minutes.
    """
    if abs(drift_s) < _SYNC_DRIFT_MIN_S:
        return t("settings.clock.in_sync")
    return t("settings.clock.drift", minutes=f"{round(drift_s / _SECONDS_PER_MINUTE):+d}")


@dataclass(frozen=True, slots=True)
class MaintenanceView:
    """The maintenance counters and the last alarms."""

    state: str
    total_text: str = ""
    partial_text: str = ""
    ignitions_text: str = ""
    progress: int | None = None
    progress_max: int = SERVICE_INTERVAL_H
    remaining_text: str = ""
    alarms: list[str] = field(default_factory=list)

    @classmethod
    def from_api(cls, payload: dict[str, Any] | None, state: str) -> Self:
        """Map a ``MaintenanceDto`` payload.

        :param payload: Maintenance payload, or ``None`` when not mapped.
        :param state: Fetch state of the answer.
        :return: The view (absent without payload).
        """
        if state != STATE_OK or payload is None:
            return cls(state if state != STATE_OK else STATE_ABSENT)
        partial = _count(payload.get("partial_hours"))
        remaining = _count(payload.get("hours_to_service"))
        if partial is None and remaining is not None:
            partial = max(0, SERVICE_INTERVAL_H - remaining)
        if remaining is None and partial is not None:
            remaining = max(0, SERVICE_INTERVAL_H - partial)
        alarms = [str(item) for item in payload.get("last_alarms") or []][:MAX_ALARMS]
        return cls(
            state=STATE_OK,
            total_text=_hours_text(_count(payload.get("total_hours"))),
            partial_text=_hours_text(partial),
            ignitions_text=_number_text(_count(payload.get("ignitions"))),
            progress=None if partial is None else min(partial, SERVICE_INTERVAL_H),
            remaining_text=(
                "" if remaining is None else t("settings.maintenance.remaining", hours=remaining)
            ),
            alarms=alarms,
        )


def _count(value: Any) -> int | None:
    """Read a non-negative integer counter.

    :param value: JSON value.
    :return: The integer, or ``None`` when missing or invalid.
    """
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def _number_text(value: int | None) -> str:
    """Format a counter, with ``--`` when unknown.

    :param value: Counter value.
    :return: The text.
    """
    return t("common.none") if value is None else str(value)


def _hours_text(value: int | None) -> str:
    """Format a number of hours, with ``--`` when unknown.

    :param value: Hours.
    :return: The text.
    """
    return t("common.none") if value is None else t("settings.maintenance.hours", hours=value)
