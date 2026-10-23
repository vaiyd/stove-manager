"""Suspension and vacation view model with its banner texts (no I/O, French texts via ``t``)."""

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Final, Self

from stove_pilot.service.ui.formatting import localize, parse_datetime, since_label
from stove_pilot.service.ui.i18n import t

_VACATION_KIND: Final = "vacation"
_RECOVERY_PHASES: Final = frozenset({"activating", "restoring"})


@dataclass(frozen=True, slots=True)
class SuspensionView:
    """The suspension state and its banner text."""

    suspended: bool
    banner: str
    thermostat_mode: bool
    vacation: bool = False
    recovering: bool = False

    @classmethod
    def from_api(cls, payload: dict[str, Any], now: datetime) -> Self:
        """Map a ``SuspensionDto`` payload.

        :param payload: Suspension payload.
        :param now: Local current time.
        :return: The view.
        """
        suspended = bool(payload.get("suspended", False))
        thermostat = bool(payload.get("thermostat_mode", False))
        since = parse_datetime(payload.get("since"))
        until = parse_datetime(payload.get("until"))
        vacation = suspended and payload.get("kind") == _VACATION_KIND
        if suspended and payload.get("phase") in _RECOVERY_PHASES:
            return cls(True, t("suspension.recovering"), thermostat, vacation, recovering=True)
        if vacation:
            return cls(True, _vacation_banner(payload, until, now), thermostat, vacation=True)
        banner = ""
        if suspended:
            banner = t(
                "suspension.banner",
                since="" if since is None else since_label(since, now),
                until="" if until is None else t("suspension.until", until=since_label(until, now)),
                mode=t("suspension.thermostat") if thermostat else "",
            )
        return cls(suspended, banner, thermostat)


def _degrees_text(value: Any) -> str:
    """Format a setpoint of the vacation payload.

    :param value: Setpoint in degrees Celsius, or ``None``.
    :return: The number without trailing zeros, or ``--`` when unknown.
    """
    return t("common.none") if value is None else f"{float(value):g}"


def _vacation_banner(payload: dict[str, Any], until: datetime | None, now: datetime) -> str:
    """Build the vacation banner.

    :param payload: Suspension payload of a vacation.
    :param until: Automatic end time, or ``None`` for a manual end.
    :param now: Local current time.
    :return: The French banner text.
    """
    return t(
        "vacation.banner",
        until="" if until is None else t("vacation.until", until=f"{localize(until, now):%d/%m}"),
        frost=_degrees_text(payload.get("frost_setpoint_c")),
        previous=_degrees_text(payload.get("previous_setpoint_c")),
    )
