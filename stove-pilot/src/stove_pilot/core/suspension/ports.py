"""Outbound port of the suspension: what a programming feature must offer to be suspended."""

from typing import Any, Protocol


class Suspendable(Protocol):
    """A programming feature that can be switched off and restored (adapted by composition)."""

    name: str

    def is_active(self) -> bool:
        """Tell whether the feature currently drives the stove.

        :return: ``True`` when something is enabled.
        """
        ...

    def snapshot(self) -> dict[str, Any]:
        """Read what ``suspend`` would switch off, without writing anything.

        :return: JSON-safe snapshot, to pass to ``suspend`` and later to ``resume``.
        """
        ...

    def suspend(self, snapshot: dict[str, Any]) -> None:
        """Switch off exactly what the snapshot lists (read-before-write, idempotent).

        :param snapshot: Value returned by ``snapshot``.
        """
        ...

    def resume(self, snapshot: dict[str, Any]) -> None:
        """Re-enable only what the snapshot lists.

        :param snapshot: Value returned by ``snapshot``.
        """
        ...
