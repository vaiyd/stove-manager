"""Chrono routes, summary line and suspension participant (the only router that knows the chrono).

Route handlers are closures defined inside ``register_*`` functions (see ``health``).
"""

import logging
from datetime import datetime
from typing import Any

from flask import Blueprint, Response, jsonify, request
from werkzeug.http import unquote_etag

from stove_pilot.common.exceptions import (
    ConfigurationError,
    GatewayError,
    ValidationFailedError,
)
from stove_pilot.core.chrono.models import ChronoBlockKind, ChronoConfig
from stove_pilot.core.chrono.repository import ChronoRepository
from stove_pilot.core.stove.models import StoveProfile
from stove_pilot.core.suspension.ports import Suspendable
from stove_pilot.service.api.errors import ApiProblemError
from stove_pilot.service.api.parsing import parse_body, query_float
from stove_pilot.service.api.schemas.chrono import (
    FEATURE_LABEL,
    FEATURE_NAME,
    BlockStateRequest,
    ChronoConfigDto,
    ChronoConfigRequest,
    FingerprintDto,
    issue_text,
    programming_summary_dto,
)
from stove_pilot.service.api.schemas.common import dump
from stove_pilot.service.api.schemas.summary import ProgrammingSummaryDto
from stove_pilot.service.composition import Container

_LOGGER = logging.getLogger(__name__)

_UNVERIFIED_CODE = "unverified_layout"
_UNAVAILABLE_REASON = "gateway_unavailable"


class ChronoParticipant:
    """Adapts the chrono repository to the suspension ``Suspendable`` protocol."""

    name = FEATURE_NAME

    def __init__(self, repository: ChronoRepository) -> None:
        """Bind the chrono repository.

        :param repository: Chrono repository.
        """
        self._repository = repository

    def is_active(self) -> bool:
        """Tell whether any chrono block is enabled (cached read).

        :return: ``True`` when at least one block is enabled.
        """
        return self._repository.is_enabled()

    def snapshot(self) -> dict[str, Any]:
        """Read the enabled blocks (no write).

        :return: Snapshot listing the blocks that are enabled.
        """
        return self._repository.snapshot()

    def suspend(self, snapshot: dict[str, Any]) -> None:
        """Disable the blocks listed in the snapshot.

        :param snapshot: Value returned by ``snapshot``.
        """
        self._repository.suspend(snapshot)

    def resume(self, snapshot: dict[str, Any]) -> None:
        """Re-enable the blocks listed in the snapshot.

        :param snapshot: Value returned by ``snapshot``.
        """
        self._repository.resume(snapshot)


def chrono_participant(container: Container) -> Suspendable:
    """Build the suspension participant of the chrono.

    :param container: Composed repositories (``chrono`` must be built).
    :return: The participant.
    :raises ConfigurationError: The chrono is not composed.
    """
    return ChronoParticipant(_require_chrono(container))


def summarize_chrono(container: Container, now: datetime) -> ProgrammingSummaryDto:
    """Build the chrono line of ``/summary`` (a failed read degrades to an inactive line).

    :param container: Composed repositories.
    :param now: Aware local time.
    :return: The shared summary DTO labelled ``Chrono poele``.
    """
    try:
        return programming_summary_dto(_require_chrono(container).summary(now))
    except GatewayError as exc:
        _LOGGER.warning("Chrono summary unavailable (%s)", type(exc).__name__)
        return ProgrammingSummaryDto(
            feature=FEATURE_NAME,
            label=FEATURE_LABEL,
            active=False,
            current=None,
            next=None,
            blocked_reason=_UNAVAILABLE_REASON,
        )


def register_chrono_api(bp: Blueprint, container: Container) -> None:
    """Add the chrono routes.

    :param bp: API blueprint.
    :param container: Composed repositories (``chrono`` must be built).
    """
    repository = _require_chrono(container)

    @bp.get("/chrono")
    def get_chrono() -> Response:
        max_age_s = query_float("max_age_s", minimum=0)
        # Reason: an editor must show the stove as it is now, so the cache is opt-in here.
        return _config_response(container, repository.get(0 if max_age_s is None else max_age_s))

    @bp.get("/chrono/fingerprint")
    def get_fingerprint() -> dict[str, Any]:
        max_age_s = query_float("max_age_s", minimum=0)
        return dump(FingerprintDto(fingerprint=repository.fingerprint(max_age_s)))

    @bp.put("/chrono")
    def put_chrono() -> Response:
        expected = _expected_fingerprint()
        wanted = parse_body(ChronoConfigRequest).to_domain(repository.get())
        try:
            saved = repository.save(wanted, expected_fingerprint=expected)
        except ValidationFailedError as exc:
            raise _readable(exc) from None
        return _config_response(container, saved)

    @bp.put("/chrono/blocks/<kind>/state")
    def put_block_state(kind: str) -> Response:
        try:
            block_kind = ChronoBlockKind(kind)
        except ValueError:
            raise ApiProblemError(
                404, "unknown_block", "Unknown block", "The block must be day, week or weekend."
            ) from None
        body = parse_body(BlockStateRequest)
        try:
            saved = repository.set_block_enabled(block_kind, body.enabled)
        except ValidationFailedError as exc:
            raise _readable(exc) from None
        return _config_response(container, saved)


def _require_chrono(container: Container) -> ChronoRepository:
    """Return the chrono repository of the container.

    :param container: Composed repositories.
    :return: The chrono repository.
    :raises ConfigurationError: The chrono is not composed.
    """
    if container.chrono is None:
        raise ConfigurationError("The chrono feature is not composed")
    return container.chrono


def _expected_fingerprint() -> str:
    """Read the strong ``If-Match`` ETag of the request.

    :return: The fingerprint the client last saw (empty for a weak tag, which never matches).
    :raises ApiProblemError: The header is missing (428).
    """
    header = request.headers.get("If-Match", "").strip()
    if not header:
        raise ApiProblemError(
            428,
            "precondition_required",
            "Precondition required",
            "An If-Match header with the ETag of the chrono is required.",
        )
    value, weak = unquote_etag(header)
    return "" if weak or value is None else value


def _readable(exc: ValidationFailedError) -> ValidationFailedError:
    """Rewrite chrono issues as ``code: message`` strings for the problem response.

    :param exc: Validation error raised by the chrono repository.
    :return: The same issues as readable text.
    """
    return ValidationFailedError([issue_text(issue) for issue in exc.issues])


def _range(profile: StoveProfile, name: str) -> tuple[int, int]:
    """Read an inclusive integer range from a profile register.

    :param profile: Stove profile.
    :param name: Register name.
    :return: ``(min, max)``.
    :raises ConfigurationError: The register has no range.
    """
    register = profile.register(name)
    if register.min_value is None or register.max_value is None:
        raise ConfigurationError(f"Profile register {name!r} has no min/max")
    return int(register.min_value), int(register.max_value)


def _config_response(container: Container, config: ChronoConfig) -> Response:
    """Render a configuration with its ``ETag`` (the fingerprint).

    :param container: Composed repositories.
    :param config: Configuration to render.
    :return: JSON response carrying the strong ETag.
    """
    repository = _require_chrono(container)
    validation = repository.validate(config)
    writable = all(issue.code != _UNVERIFIED_CODE for issue in validation.issues)
    dto = ChronoConfigDto.from_domain(
        config,
        validation,
        setpoint_range=_range(container.profile, "setpoint"),
        power_range=_range(container.profile, "power_level"),
        writable=writable,
    )
    response = jsonify(dump(dto))
    response.set_etag(config.fingerprint)
    return response
