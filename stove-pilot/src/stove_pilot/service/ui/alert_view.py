"""History alert card: one row per display-message code, independent of the range tabs."""

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Final

from stove_pilot.service.ui.formatting import parse_datetime, since_label
from stove_pilot.service.ui.i18n import has_key, t

# Reason: badge tones follow AlertView (viewmodels._ALERT_CLASSES); info stays a warning outline.
_SEVERITY_BADGES: Final = {
    "info": "badge-outline badge-warning border-warning text-base-content",
    "warning": "badge-warning",
    "error": "badge-error",
}
_DEFAULT_BADGE: Final = "badge-outline badge-warning border-warning text-base-content"
_STATE_BADGES: Final = {"active": "badge-warning", "cleared": "badge-ghost"}
_CLEARED_BADGE: Final = "badge-ghost"


@dataclass(frozen=True, slots=True)
class AlertRow:
    """One line of the alert catalog."""

    title: str
    severity_label: str
    severity_class: str
    occurrences_text: str
    last_seen: str
    state_label: str
    state_class: str


def alert_rows(payload: list[dict[str, Any]], now: datetime) -> tuple[AlertRow, ...]:
    """Map alert payloads into card rows (the API order is kept).

    :param payload: ``HistoryAlertDto`` payloads, most recently seen first.
    :param now: Local current time.
    :return: Rows that have a title and a usable last-seen time.
    """
    rows: list[AlertRow] = []
    for raw in payload:
        row = _row(raw, now)
        if row is not None:
            rows.append(row)
    return tuple(rows)


def _row(raw: Any, now: datetime) -> AlertRow | None:
    """Map one payload object.

    :param raw: One JSON value.
    :param now: Local current time.
    :return: The row, or ``None`` when the value cannot be shown.
    """
    if not isinstance(raw, dict):
        return None
    seen_at = raw.get("last_seen_at")
    seen = parse_datetime(seen_at if isinstance(seen_at, str) else None)
    title = str(raw.get("title") or raw.get("code") or "")
    if seen is None or not title:
        return None
    severity = str(raw.get("severity", ""))
    state = str(raw.get("state", ""))
    return AlertRow(
        title,
        _label("history.alert.severity", severity),
        _SEVERITY_BADGES.get(severity, _DEFAULT_BADGE),
        t("history.alert.times", count=_count(raw.get("occurrences"))),
        since_label(seen, now),
        _label("history.alert.state", state),
        _STATE_BADGES.get(state, _CLEARED_BADGE),
    )


def _label(prefix: str, value: str) -> str:
    """Translate a catalog suffix, falling back to the unknown key.

    :param prefix: Key prefix (``history.alert.severity`` or ``history.alert.state``).
    :param value: Suffix stored by the API.
    :return: French text.
    """
    key = f"{prefix}.{value}"
    return t(key) if has_key(key) else t(f"{prefix}.unknown")


def _count(value: Any) -> int:
    """Read an occurrence count (anything else becomes 0).

    :param value: JSON value.
    :return: A non-negative integer.
    """
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return 0
    return value
