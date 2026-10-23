"""Toast messages: French texts for API problems, command outcomes and suspension warnings."""

from dataclasses import dataclass
from typing import Any, Final

from stove_pilot.service.ui.client import ApiProblem
from stove_pilot.service.ui.i18n import has_key, t

_ALERT_CLASSES: Final = {
    "success": "alert-success",
    "info": "alert-info",
    "warning": "alert-warning",
    "error": "alert-error",
}
_RATE_LIMIT_STATUS: Final = 429
_SOFT_PROBLEMS: Final = frozenset(
    {"command_rejected", "feature_conflict", "rate_limited", "validation_failed", "write_unknown"}
)
_STATE_REASON_PREFIX: Final = "state_"
_WARNING_SEPARATOR: Final = ":"


@dataclass(frozen=True, slots=True)
class Toast:
    """A short message shown in the toast area (``level``: success, info, warning, error)."""

    level: str
    text: str

    @property
    def css_class(self) -> str:
        """The daisyUI alert class of the level."""
        return _ALERT_CLASSES.get(self.level, "alert-info")


def problem_toast(problem: ApiProblem) -> Toast:
    """Turn an API problem into a French toast; raw server details are never shown.

    :param problem: The problem raised by the API client.
    :return: The toast.
    """
    code = "rate_limited" if problem.status == _RATE_LIMIT_STATUS else problem.code
    reason = problem.reason or ""
    if reason.startswith(_STATE_REASON_PREFIX):
        reason = "state"
    candidates = [f"problem.{code}.{reason}", f"problem.{code}"]
    key = next((candidate for candidate in candidates if has_key(candidate)), "problem.unknown")
    return Toast("warning" if code in _SOFT_PROBLEMS else "error", t(key))


def csrf_toast() -> Toast:
    """Build the toast shown when a form token is missing or expired.

    :return: The toast.
    """
    return Toast("warning", t("problem.csrf"))


def outcome_toast(command: str, outcome: dict[str, Any]) -> Toast:
    """Describe a start or stop outcome.

    :param command: ``start`` or ``stop``.
    :param outcome: ``CommandOutcomeDto`` payload.
    :return: The toast.
    """
    result = str(outcome.get("result", ""))
    if result == "applied":
        return Toast("success", t(f"controls.{command}_applied"))
    reason = outcome.get("reason") or ""
    for key in (f"outcome.{command}.{result}.{reason}", f"outcome.{command}.{result}"):
        if has_key(key):
            return Toast("info", t(key))
    return Toast("info", t("controls.unchanged"))


def change_toast(setting: str, change: dict[str, Any]) -> Toast:
    """Describe a setpoint or power change.

    :param setting: ``setpoint`` or ``power``.
    :param change: ``SettingChangeDto`` payload.
    :return: The toast.
    """
    if change.get("changed"):
        return Toast("success", t(f"controls.{setting}_changed"))
    return Toast("info", t("controls.unchanged"))


def suspend_toasts(outcome: dict[str, Any], done_key: str = "suspension.done") -> list[Toast]:
    """Describe a suspend or vacation outcome: the start result, then each warning.

    :param outcome: ``SuspendOutcomeDto`` payload.
    :param done_key: Catalog key of the success text.
    :return: Toasts in display order.
    """
    toasts = [Toast("success", t(done_key))]
    start = outcome.get("start_outcome")
    if isinstance(start, dict):
        toasts.append(outcome_toast("start", start))
    for warning in outcome.get("warnings", []):
        toasts.append(Toast("warning", _warning_text(str(warning))))
    return toasts


def _warning_text(warning: str) -> str:
    """Translate a suspension warning code (``code`` or ``code:reason``).

    :param warning: Warning code from the API.
    :return: French text.
    """
    code, _, reason = warning.partition(_WARNING_SEPARATOR)
    key = f"warning.{code}"
    if not has_key(key):
        return t("warning.unknown")
    detail_key = f"problem.command_rejected.{reason}"
    if reason and has_key(detail_key):
        return f"{t(key)} : {t(detail_key)}"
    return t(key)
