"""Feature registry: one ``FeatureSpec`` per optional programming feature."""

import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from flask import Blueprint, Flask

from stove_pilot.core.suspension.ports import Suspendable
from stove_pilot.service.api.routers.chrono import (
    chrono_participant,
    register_chrono_api,
    summarize_chrono,
)
from stove_pilot.service.api.schemas.summary import ProgrammingSummaryDto
from stove_pilot.service.composition import Container
from stove_pilot.service.ui.client import StovePilotApiClient
from stove_pilot.service.ui.routes.chrono import register_chrono_ui


@dataclass(frozen=True, slots=True)
class FeatureSpec:
    """How a feature plugs into the API and the process.

    ``register_api`` adds the feature routes to the API blueprint. ``start_threads`` starts
    background runners and returns them (the caller joins them on shutdown). ``summary`` returns
    the feature line of ``/summary``. ``participant`` adapts the feature to the suspension.
    ``register_ui`` adds the feature pages to the UI app, given a lazy getter of the API client
    (``None`` when the feature has no pages).
    """

    name: str
    label_fr: str
    register_api: Callable[[Blueprint, Container], None]
    start_threads: Callable[[Container, threading.Event], list[threading.Thread]] | None
    summary: Callable[[Container, datetime], ProgrammingSummaryDto] | None
    participant: Callable[[Container], Suspendable] | None
    register_ui: Callable[[Flask, Callable[[], StovePilotApiClient]], None] | None = None


# Reason: keys are the ``settings.features`` names.
FEATURES: dict[str, FeatureSpec] = {
    "chrono": FeatureSpec(
        "chrono",
        "Chrono poele",
        register_chrono_api,
        None,
        summarize_chrono,
        chrono_participant,
        register_chrono_ui,
    ),
}
