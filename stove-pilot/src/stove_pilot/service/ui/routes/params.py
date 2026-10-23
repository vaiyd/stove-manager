"""Query and form parameter parsing shared by the UI routes."""

from flask import request


def query_max_age() -> float | None:
    """Read the optional ``max_age_s`` query parameter set by ``refresh.js``.

    :return: Non-negative seconds, or ``None`` when absent or invalid.
    """
    value = request.args.get("max_age_s", type=float)
    return value if value is not None and value >= 0 else None


def query_flag(name: str) -> bool:
    """Read a ``1``/``true`` query flag.

    :param name: Parameter name.
    :return: ``True`` when set to ``1`` or ``true``.
    """
    return request.args.get(name, "").lower() in {"1", "true"}
