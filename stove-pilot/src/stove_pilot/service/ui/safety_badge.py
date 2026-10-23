"""Discreet glance badge, built from the summary safety member (no I/O)."""

from dataclasses import dataclass
from typing import Any, Final, Self

from stove_pilot.service.ui.i18n import t

_BADGE_INFO: Final = frozenset({"restarting", "waiting_retry"})
_BADGE_MUTED: Final = frozenset({"paused_manual", "suspended_vacation", "stale"})
_BADGE_ERROR: Final = frozenset({"failed"})
_BADGE_STATUSES: Final = frozenset(
    {"armed", "confirming"} | _BADGE_INFO | _BADGE_MUTED | _BADGE_ERROR
)


@dataclass(frozen=True, slots=True)
class SafetyBadge:
    """Discreet glance badge, present only while safety is enabled."""

    tone: str
    label: str
    threshold_text: str
    muted: bool

    @classmethod
    def from_summary(cls, raw: Any) -> Self | None:
        """Map the summary ``safety`` member.

        :param raw: ``safety`` object (``enabled``, ``status``, ``threshold_c``), or anything else.
        :return: The badge, or ``None`` when safety is off or the member is unusable.
        """
        if not isinstance(raw, dict) or raw.get("enabled") is not True:
            return None
        status = raw.get("status")
        threshold = raw.get("threshold_c")
        if not isinstance(status, str) or status not in _BADGE_STATUSES:
            return None
        if isinstance(threshold, bool) or not isinstance(threshold, int):
            return None
        if status in _BADGE_INFO:
            tone = "info"
        elif status in _BADGE_ERROR:
            tone = "error"
        else:
            tone = "neutral"
        return cls(
            tone=tone,
            label=t(f"safety.badge.{status}", threshold=threshold),
            threshold_text=str(threshold),
            muted=status in _BADGE_MUTED,
        )
