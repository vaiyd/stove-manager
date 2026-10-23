"""UI menu registry: which entries exist and when they are shown."""

from collections.abc import Collection
from dataclasses import dataclass
from typing import Final


@dataclass(frozen=True, slots=True)
class NavEntry:
    """One menu entry.

    ``endpoint`` is the Flask endpoint of its page. ``feature`` names the API feature that must
    be enabled in ``GET /features`` (``None`` for the common base).
    """

    key: str
    label_key: str
    endpoint: str
    icon: str
    feature: str | None = None


NAV_ENTRIES: Final[tuple[NavEntry, ...]] = (
    NavEntry("dashboard", "nav.dashboard", "dashboard", "home"),
    NavEntry("chrono", "nav.chrono", "chrono.index", "calendar", feature="chrono"),
    NavEntry("history", "nav.history", "history.index", "chart"),
    NavEntry("settings", "nav.settings", "settings.index", "cog"),
)


def visible_entries(
    features: dict[str, bool], registered_endpoints: Collection[str]
) -> list[NavEntry]:
    """Pick the entries to show: the page must exist, and its feature must be enabled.

    :param features: Enabled flags from ``GET /features``.
    :param registered_endpoints: Endpoint names registered on the UI app (``view_functions``).
    :return: Entries in menu order.
    """
    return [
        entry
        for entry in NAV_ENTRIES
        if entry.endpoint in registered_endpoints
        and (entry.feature is None or features.get(entry.feature, False))
    ]
