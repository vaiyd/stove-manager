"""Problem+json error responses and the mapping of domain exceptions to HTTP statuses."""

import json
import logging
from collections.abc import Mapping
from http import HTTPStatus
from typing import Any, Final

from flask import Flask, Response
from pydantic import ValidationError as PydanticValidationError
from werkzeug.exceptions import HTTPException

from stove_pilot.common.exceptions import (
    CommandRejectedError,
    ConfigurationError,
    FeatureConflictError,
    GatewayError,
    StovePilotError,
    UnverifiedRegisterError,
    ValidationFailedError,
    VersionConflictError,
    WriteOutcomeUnknownError,
)

_LOGGER = logging.getLogger(__name__)

PROBLEM_MIMETYPE: Final = "application/problem+json"
_TYPE_PREFIX: Final = "urn:stove-pilot:problem:"
_INTERNAL_DETAIL: Final = "An internal error occurred."


class ApiProblemError(Exception):
    """An error a router raises directly, rendered as a problem+json response."""

    def __init__(
        self,
        status: int,
        code: str,
        title: str,
        detail: str,
        *,
        headers: Mapping[str, str] | None = None,
    ) -> None:
        """Store the problem fields.

        :param status: HTTP status code.
        :param code: Machine-readable error code.
        :param title: Short human-readable title.
        :param detail: Explanation for the caller.
        :param headers: Extra response headers (for example ``Retry-After``).
        """
        super().__init__(detail)
        self.status = status
        self.code = code
        self.title = title
        self.detail = detail
        self.headers = dict(headers or {})


def problem_response(
    status: int,
    code: str,
    title: str,
    detail: str,
    *,
    headers: Mapping[str, str] | None = None,
    **extra: Any,
) -> Response:
    """Build a problem+json response.

    :param status: HTTP status code.
    :param code: Machine-readable error code.
    :param title: Short human-readable title.
    :param detail: Explanation for the caller.
    :param headers: Extra response headers.
    :param extra: Additional members (``issues``, ``reason``), ``None`` values are dropped.
    :return: The response.
    """
    body: dict[str, Any] = {
        "type": f"{_TYPE_PREFIX}{code}",
        "title": title,
        "status": status,
        "detail": detail,
        "code": code,
    }
    body.update({key: value for key, value in extra.items() if value is not None})
    response = Response(
        json.dumps(body, separators=(",", ":")), status=status, mimetype=PROBLEM_MIMETYPE
    )
    for name, value in (headers or {}).items():
        response.headers[name] = value
    return response


def format_pydantic_issues(exc: PydanticValidationError) -> list[str]:
    """Flatten Pydantic errors into ``field: message`` strings (input values are never echoed).

    :param exc: Pydantic validation error.
    :return: One string per error.
    """
    issues = []
    for error in exc.errors(include_url=False, include_context=False, include_input=False):
        location = ".".join(str(part) for part in error["loc"])
        issues.append(f"{location}: {error['msg']}" if location else str(error["msg"]))
    return issues


def _handle_stove_error(exc: StovePilotError) -> Response:
    """Map a domain exception to its problem response (most specific type first).

    :param exc: Domain exception.
    :return: The problem response.
    """
    if isinstance(exc, CommandRejectedError):
        return problem_response(
            409,
            "command_rejected",
            "Command rejected",
            f"The command was rejected: {exc.reason}.",
            reason=exc.reason,
        )
    if isinstance(exc, UnverifiedRegisterError):
        return problem_response(
            409, "unverified_register", "Unverified register", str(exc), register=exc.name
        )
    if isinstance(exc, FeatureConflictError):
        return problem_response(
            409, "feature_conflict", "Feature conflict", str(exc), reason=exc.reason
        )
    if isinstance(exc, ValidationFailedError):
        return problem_response(
            422,
            "validation_failed",
            "Validation failed",
            "The request is invalid.",
            issues=[str(issue) for issue in exc.issues],
        )
    if isinstance(exc, VersionConflictError):
        return problem_response(
            412,
            "version_conflict",
            "Version conflict",
            "The resource changed since it was read; reload it and retry.",
        )
    if isinstance(exc, WriteOutcomeUnknownError):
        return problem_response(
            503,
            "write_outcome_unknown",
            "Write outcome unknown",
            "The write may or may not have been applied; read the status before retrying.",
        )
    if isinstance(exc, GatewayError):
        return problem_response(
            503,
            "gateway_unavailable",
            "Stove module unavailable",
            "The stove module could not be reached or refused the request.",
        )
    _LOGGER.error("Unhandled domain error", exc_info=exc)
    if isinstance(exc, ConfigurationError):
        return problem_response(
            500,
            "configuration_error",
            "Configuration error",
            "The server configuration does not support this operation.",
        )
    return problem_response(500, "internal_error", "Internal error", _INTERNAL_DETAIL)


def _handle_api_problem(exc: ApiProblemError) -> Response:
    return problem_response(exc.status, exc.code, exc.title, exc.detail, headers=exc.headers)


def _handle_pydantic_error(exc: PydanticValidationError) -> Response:
    return problem_response(
        422,
        "validation_failed",
        "Validation failed",
        "The request is invalid.",
        issues=format_pydantic_issues(exc),
    )


def _handle_http_error(exc: HTTPException) -> Response:
    status = exc.code or 500
    code = HTTPStatus(status).name.lower() if status in HTTPStatus else "http_error"
    return problem_response(status, code, exc.name, exc.description or exc.name)


def _handle_unexpected_error(exc: Exception) -> Response:
    _LOGGER.error("Unhandled exception while serving a request", exc_info=exc)
    return problem_response(500, "internal_error", "Internal error", _INTERNAL_DETAIL)


def register_error_handlers(app: Flask) -> None:
    """Install the problem+json handlers on the app.

    :param app: Flask application.
    """
    app.register_error_handler(ApiProblemError, _handle_api_problem)
    app.register_error_handler(StovePilotError, _handle_stove_error)
    app.register_error_handler(PydanticValidationError, _handle_pydantic_error)
    app.register_error_handler(HTTPException, _handle_http_error)
    app.register_error_handler(Exception, _handle_unexpected_error)
