"""French UI copy: a flat ``fr.json`` catalog read through ``t``."""

import json
from functools import lru_cache
from importlib.resources import files
from typing import Any


@lru_cache(maxsize=1)
def _catalog() -> dict[str, str]:
    """Load the French catalog shipped with the package.

    :return: Flat mapping of dotted keys to texts.
    """
    raw = files("stove_pilot.service.ui.i18n").joinpath("fr.json").read_text(encoding="utf-8")
    catalog: dict[str, str] = json.loads(raw)
    return catalog


def has_key(key: str) -> bool:
    """Tell whether a key exists.

    :param key: Dotted catalog key.
    :return: ``True`` when the catalog holds the key.
    """
    return key in _catalog()


def t(key: str, **params: Any) -> str:
    """Translate a key and fill its ``{name}`` placeholders.

    :param key: Dotted catalog key.
    :param params: Values for the placeholders.
    :return: The French text.
    :raises KeyError: The key is not in the catalog.
    """
    text = _catalog()[key]
    return text.format(**params) if params else text
