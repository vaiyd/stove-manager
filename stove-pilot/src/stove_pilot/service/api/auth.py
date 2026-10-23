"""Optional ``X-API-Key`` check for the API blueprint.

``register_api_key_check`` defines its ``before_request`` hook as a closure over the key; this is
the documented closure pattern used by every ``register_*`` function of the API.
"""

import hmac
from typing import Final

from flask import Blueprint, Response, request

from stove_pilot.service.api.errors import problem_response

API_KEY_HEADER: Final = "X-API-Key"
_EXEMPT_ENDPOINTS: Final = frozenset({"api.health"})


def register_api_key_check(bp: Blueprint, api_key: str) -> None:
    """Require the API key on every route except ``/health`` when a key is configured.

    :param bp: API blueprint.
    :param api_key: Expected key; an empty value disables the check.
    """
    if not api_key:
        return
    expected = api_key.encode("utf-8")

    @bp.before_request
    def check_api_key() -> Response | None:
        if request.endpoint in _EXEMPT_ENDPOINTS:
            return None
        provided = request.headers.get(API_KEY_HEADER, "").encode("utf-8")
        if hmac.compare_digest(provided, expected):
            return None
        return problem_response(
            401,
            "unauthorized",
            "Unauthorized",
            f"A valid {API_KEY_HEADER} header is required.",
        )
