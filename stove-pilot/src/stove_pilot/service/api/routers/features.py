"""``GET /features``: which registered features are enabled in the settings.

Route handlers are closures defined inside ``register_*`` functions (see ``health``).
"""

from collections.abc import Collection

from flask import Blueprint

from stove_pilot.common.settings import Settings


def register_features_routes(
    bp: Blueprint, settings: Settings, feature_names: Collection[str]
) -> None:
    """Add the features route.

    :param bp: API blueprint.
    :param settings: Application settings (``features`` holds the enabled names).
    :param feature_names: Names of every registered feature.
    """

    @bp.get("/features")
    def features() -> dict[str, bool]:
        return {name: name in settings.features for name in feature_names}
