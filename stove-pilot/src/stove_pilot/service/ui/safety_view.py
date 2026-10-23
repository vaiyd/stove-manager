"""Securite card, built from safety payloads (no I/O)."""

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Final, Self

from stove_pilot.service.ui.formatting import hhmm, localize, parse_datetime, since_label
from stove_pilot.service.ui.i18n import t
from stove_pilot.service.ui.settings_view import STATE_ABSENT, STATE_OK

_REARM_STATUSES: Final = frozenset({"paused_manual", "failed", "waiting_retry"})
_FAILED_REASONS: Final = frozenset(
    {"alarm", "unknown", "attempts_exhausted", "ignition_failed"}
)
_PAUSED_REASONS: Final = frozenset({"app_stop", "panel_stop"})
_WARNING_CODE: Final = "threshold_not_below_setpoint"
# Reason: confirming is an in-progress check, so it shares the restarting colour.
_STATUS_TONE: Final = {
    "disabled": "neutral",
    "armed": "neutral",
    "confirming": "info",
    "restarting": "info",
    "waiting_retry": "warning",
    "failed": "error",
    "paused_manual": "warning",
    "suspended_vacation": "warning",
    "stale": "warning",
}


@dataclass(frozen=True, slots=True)
class SafetyView:
    """The Securite settings card (``error_text`` is set only after a rejected save)."""

    state: str
    enabled: bool = False
    threshold_c: int = 10
    max_attempts: int = 3
    retry_interval_min: int = 15
    threshold_min_c: int = 5
    threshold_max_c: int = 20
    max_attempts_min: int = 1
    max_attempts_max: int = 5
    retry_interval_min_min: int = 15
    retry_interval_max_min: int = 240
    status: str = ""
    status_text: str = ""
    status_tone: str = "neutral"
    show_rearm: bool = False
    warning_text: str = ""
    last_check_text: str = ""
    error_text: str = ""

    @classmethod
    def from_api(
        cls, payload: dict[str, Any] | None, now: datetime, state: str = STATE_OK
    ) -> Self:
        """Map a safety DTO.

        :param payload: ``GET`` or ``PUT /safety`` body, or ``None`` when the read failed.
        :param now: Local current time (status and last-check texts).
        :param state: Fetch state (``ok`` when omitted, ``absent`` on 404, ``error`` otherwise).
        :return: The view (absent when the payload is missing on an ok read).
        """
        if state != STATE_OK or not isinstance(payload, dict):
            return cls(state if state != STATE_OK else STATE_ABSENT)
        status = str(payload.get("status") or "")
        status_text, tone = _status_line(payload, status, now)
        return cls(
            state=STATE_OK,
            enabled=bool(payload.get("enabled")),
            threshold_c=_whole(payload.get("threshold_c"), 10),
            max_attempts=_whole(payload.get("max_attempts"), 3),
            retry_interval_min=_whole(payload.get("retry_interval_min"), 15),
            threshold_min_c=_whole(payload.get("threshold_min_c"), 5),
            threshold_max_c=_whole(payload.get("threshold_max_c"), 20),
            max_attempts_min=_whole(payload.get("max_attempts_min"), 1),
            max_attempts_max=_whole(payload.get("max_attempts_max"), 5),
            retry_interval_min_min=_whole(payload.get("retry_interval_min_min"), 15),
            retry_interval_max_min=_whole(payload.get("retry_interval_max_min"), 240),
            status=status,
            status_text=status_text,
            status_tone=tone,
            show_rearm=status in _REARM_STATUSES,
            warning_text=_warning_text(payload.get("warning")),
            last_check_text=_last_check_text(payload.get("last_check_at"), now),
        )


def _whole(value: Any, default: int) -> int:
    """Read a whole number, rejecting bools and fractions.

    :param value: JSON value.
    :param default: Value used when ``value`` is not an int.
    :return: The integer.
    """
    if isinstance(value, bool) or not isinstance(value, int):
        return default
    return value


def _status_line(payload: dict[str, Any], status: str, now: datetime) -> tuple[str, str]:
    """Build the French status line and its colour tone.

    :param payload: Safety DTO.
    :param status: ``status`` code.
    :param now: Local current time.
    :return: Text and tone (``neutral``, ``info``, ``warning`` or ``error``).
    """
    tone = _STATUS_TONE.get(status, "neutral")
    attempt = _whole(payload.get("attempts"), 0)
    maximum = _whole(payload.get("max_attempts"), 0)
    threshold = _whole(payload.get("threshold_c"), 0)
    if status == "disabled":
        return t("settings.safety.status.disabled"), tone
    if status == "armed":
        return t("settings.safety.status.armed", threshold=threshold), tone
    if status == "confirming":
        return t("settings.safety.status.confirming"), tone
    if status == "restarting":
        return t("settings.safety.status.restarting", attempt=attempt, maximum=maximum), tone
    if status == "waiting_retry":
        return _waiting_text(payload.get("next_attempt_at"), now, attempt, maximum), tone
    if status == "failed":
        return _failed_text(payload.get("failure_reason"), attempt, maximum), tone
    if status == "paused_manual":
        return _paused_text(payload.get("paused_reason")), tone
    if status == "suspended_vacation":
        return t("settings.safety.status.suspended_vacation"), tone
    if status == "stale":
        return t("settings.safety.status.stale"), tone
    return t("settings.safety.status.unknown"), tone


def _waiting_text(raw: Any, now: datetime, attempt: int, maximum: int) -> str:
    """Describe a retry that is waiting for its interval.

    :param raw: ``next_attempt_at`` ISO text.
    :param now: Local current time.
    :param attempt: Attempts already used.
    :param maximum: Configured maximum.
    :return: The French line, with ``HH:MM`` when the next attempt is known.
    """
    moment = _moment(raw)
    if moment is None:
        return t("settings.safety.status.waiting_retry_notime", attempt=attempt, maximum=maximum)
    clock = hhmm(localize(moment, now))
    return t(
        "settings.safety.status.waiting_retry", time=clock, attempt=attempt, maximum=maximum
    )


def _failed_text(reason: Any, attempt: int, maximum: int) -> str:
    """Describe a failed episode.

    :param reason: ``failure_reason`` code.
    :param attempt: Attempts already used.
    :param maximum: Configured maximum.
    :return: The French line.
    """
    code = reason if isinstance(reason, str) else ""
    if code == "attempts_exhausted":
        return t("settings.safety.status.failed.attempts_exhausted", attempt=attempt, maximum=maximum)
    if code in _FAILED_REASONS:
        return t(f"settings.safety.status.failed.{code}")
    return t("settings.safety.status.failed.other")


def _paused_text(reason: Any) -> str:
    """Describe a manual-stop pause.

    :param reason: ``paused_reason`` code.
    :return: The French line (application stop, stove stop, or a generic pause).
    """
    code = reason if isinstance(reason, str) and reason in _PAUSED_REASONS else "other"
    return t(f"settings.safety.status.paused.{code}")


def _warning_text(warning: Any) -> str:
    """Describe the informative setpoint warning.

    :param warning: Warning code, or ``None``.
    :return: The French text, or empty when there is nothing to say.
    """
    if warning != _WARNING_CODE:
        return ""
    return t("settings.safety.warning.threshold_not_below_setpoint")


def _last_check_text(raw: Any, now: datetime) -> str:
    """Describe the last successful safety check, including the never-checked case.

    :param raw: ``last_check_at`` ISO text.
    :param now: Local current time.
    :return: The French line.
    """
    moment = _moment(raw)
    if moment is None:
        return t("settings.safety.last_check_never")
    return t("settings.safety.last_check", when=since_label(moment, now))


def _moment(raw: Any) -> datetime | None:
    """Parse an ISO timestamp from a DTO field.

    :param raw: JSON value.
    :return: The datetime, or ``None`` when absent or malformed.
    """
    return parse_datetime(raw if isinstance(raw, str) else None)
