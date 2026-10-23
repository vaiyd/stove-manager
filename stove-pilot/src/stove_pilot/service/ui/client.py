"""HTTP client of the Stove Pilot API (the only way the UI reaches the backend)."""

from datetime import datetime
from typing import Any, Final

import httpx

API_PREFIX: Final = "/api/v1"
API_KEY_HEADER: Final = "X-API-Key"
_TIMEOUT_S: Final = 5.0
# Reason: the module is slow and serial; a fresh read can queue behind the poller (6 s seen) and a
# chrono save takes more than 5 s, so only cheap cached reads keep the short timeout.
FRESH_READ_TIMEOUT_S: Final = 20.0
WRITE_TIMEOUT_S: Final = 45.0
WRITE_UNKNOWN_CODE: Final = "write_unknown"
_HTTP_ERROR_STATUS: Final = 400
_MUTATING_METHODS: Final = frozenset({"PUT", "POST", "DELETE"})
# Reason: after these errors a mutating request may already have been processed by the API.
_SENT_ERRORS: Final = (httpx.ReadTimeout, httpx.ReadError, httpx.RemoteProtocolError)
_UNKNOWN_CODES: Final = frozenset({WRITE_UNKNOWN_CODE, "write_outcome_unknown"})


class ApiProblem(Exception):
    """An error answered by the API, or the API being unreachable (``code="api_unreachable"``)."""

    def __init__(
        self,
        status: int,
        code: str,
        detail: str,
        issues: list[str] | None = None,
        reason: str | None = None,
    ) -> None:
        """Store the problem members.

        :param status: HTTP status (0 when the API did not answer).
        :param code: Machine-readable problem code.
        :param detail: Server detail (kept for logs, never shown to the user).
        :param issues: Validation issues, if any.
        :param reason: Machine-readable reason of a rejected command, if any.
        """
        super().__init__(f"{status} {code}")
        self.status = status
        self.code = code
        self.detail = detail
        self.issues = issues or []
        self.reason = reason

    @property
    def outcome_unknown(self) -> bool:
        """Whether a write may have been applied (client timeout or API unknown-outcome answer)."""
        return self.code in _UNKNOWN_CODES


class StovePilotApiClient:
    """Synchronous API client; one instance is shared by every request thread."""

    def __init__(self, base_url: str, api_key: str = "", timeout_s: float = _TIMEOUT_S) -> None:
        """Build the underlying HTTP client (no request is sent).

        :param base_url: API origin, for example ``http://127.0.0.1:8710``.
        :param api_key: Optional API key; the header is sent only when it is non-empty.
        :param timeout_s: Default timeout of cheap reads; mutating calls use ``WRITE_TIMEOUT_S``.
        """
        headers = {API_KEY_HEADER: api_key} if api_key else {}
        self._timeout_s = timeout_s
        self._http = httpx.Client(
            base_url=base_url.rstrip("/") + API_PREFIX, headers=headers, timeout=timeout_s
        )

    def close(self) -> None:
        """Close the connection pool."""
        self._http.close()

    # -- generic verbs (feature pages use only these) -----------------------------------------

    def get(
        self, path: str, params: dict[str, Any] | None = None, *, timeout_s: float | None = None
    ) -> tuple[dict[str, Any], str | None]:
        """GET a JSON resource.

        :param path: Path below ``/api/v1``.
        :param params: Optional query parameters.
        :param timeout_s: Timeout override (use ``FRESH_READ_TIMEOUT_S`` for a live stove read).
        :return: Parsed body and the ``ETag`` header, if any.
        :raises ApiProblem: The API failed or was unreachable.
        """
        response = self._send("GET", path, timeout_s=timeout_s, params=params)
        return response.json(), response.headers.get("ETag")

    def put(
        self,
        path: str,
        body: dict[str, Any],
        etag: str | None = None,
        *,
        timeout_s: float | None = None,
    ) -> tuple[dict[str, Any], str | None]:
        """PUT a JSON body.

        :param path: Path below ``/api/v1``.
        :param body: JSON body.
        :param etag: Optional ``If-Match`` value.
        :param timeout_s: Timeout override (default ``WRITE_TIMEOUT_S``).
        :return: Parsed body and the new ``ETag`` header, if any.
        :raises ApiProblem: The API failed, was unreachable or the write outcome is unknown.
        """
        headers = {"If-Match": etag} if etag else None
        response = self._send("PUT", path, timeout_s=timeout_s, json=body, headers=headers)
        return response.json(), response.headers.get("ETag")

    def post(
        self, path: str, body: dict[str, Any] | None = None, *, timeout_s: float | None = None
    ) -> dict[str, Any]:
        """POST an optional JSON body.

        :param path: Path below ``/api/v1``.
        :param body: Optional JSON body.
        :param timeout_s: Timeout override (default ``WRITE_TIMEOUT_S``).
        :return: Parsed body.
        :raises ApiProblem: The API failed, was unreachable or the write outcome is unknown.
        """
        result: dict[str, Any] = self._send("POST", path, timeout_s=timeout_s, json=body).json()
        return result

    # -- common base endpoints ----------------------------------------------------------------

    def features(self) -> dict[str, bool]:
        """Read the enabled features.

        :return: Feature name to enabled flag.
        """
        payload, _ = self.get("/features")
        return {str(name): bool(flag) for name, flag in payload.items()}

    def status(self, max_age_s: float | None = None) -> dict[str, Any]:
        """Read the stove status.

        :param max_age_s: Maximum accepted cache age (``0`` forces a live read).
        :return: The ``StoveStatusDto`` payload.
        """
        timeout_s = None if max_age_s is None else FRESH_READ_TIMEOUT_S
        return self.get("/stove/status", self._age_params(max_age_s), timeout_s=timeout_s)[0]

    def summary(self, max_age_s: float | None = None) -> dict[str, Any]:
        """Read the at-a-glance summary (it may trigger a chrono read, so it is a fresh read).

        :param max_age_s: Maximum accepted cache age (``0`` forces a live read).
        :return: The ``SummaryDto`` payload.
        """
        params = self._age_params(max_age_s)
        return self.get("/summary", params, timeout_s=FRESH_READ_TIMEOUT_S)[0]

    def programming(self) -> dict[str, Any]:
        """Read the suspension state (``ApiProblem`` 404 when no feature can be suspended).

        :return: The suspension payload.
        """
        return self.get("/programming")[0]

    def suspend(self, until: datetime | None, thermostat_mode: bool) -> dict[str, Any]:
        """Suspend every programming feature.

        :param until: Optional automatic resume time.
        :param thermostat_mode: Start the stove when it is OFF.
        :return: The suspend outcome payload.
        """
        body: dict[str, Any] = {"thermostat_mode": thermostat_mode}
        if until is not None:
            body["until"] = until.isoformat()
        return self.post("/programming/suspend", body)

    def vacation(self, until: datetime | None) -> dict[str, Any]:
        """Start the vacation mode (suspension, frost setpoint, ENERGY SAVING STOP).

        :param until: Optional automatic end time.
        :return: The suspend outcome payload.
        """
        body: dict[str, Any] = {} if until is None else {"until": until.isoformat()}
        return self.post("/programming/vacation", body)

    def resume(self) -> dict[str, Any]:
        """Resume the suspended programming.

        :return: The resume payload.
        """
        return self.post("/programming/resume")

    def start(self) -> dict[str, Any]:
        """Send the start command.

        :return: The command outcome payload.
        """
        return self.post("/stove/commands/start")

    def stop(self, confirm_ignition_stop: bool = False) -> dict[str, Any]:
        """Send the stop command.

        :param confirm_ignition_stop: User confirmed a stop during ignition.
        :return: The command outcome payload.
        """
        return self.post("/stove/commands/stop", {"confirm_ignition_stop": confirm_ignition_stop})

    def set_setpoint(self, value_c: float) -> dict[str, Any]:
        """Change the setpoint.

        :param value_c: Target temperature in degrees Celsius.
        :return: The change payload.
        """
        return self.put("/stove/setpoint", {"value_c": value_c})[0]

    def set_power(self, level: int) -> dict[str, Any]:
        """Change the power level.

        :param level: Power level.
        :return: The change payload.
        """
        return self.put("/stove/power", {"level": level})[0]

    def temperatures(self, start: datetime, end: datetime, bucket_min: int) -> list[dict[str, Any]]:
        """Read bucketed temperatures.

        :param start: Range start.
        :param end: Range end.
        :param bucket_min: Bucket width in minutes.
        :return: Temperature points.
        """
        params = {"from": start.isoformat(), "to": end.isoformat(), "bucket_min": bucket_min}
        return self._send("GET", "/history/temperatures", params=params).json()

    def events(
        self,
        limit: int,
        *,
        start: datetime | None = None,
        end: datetime | None = None,
        before_at: str | None = None,
        before_id: int | None = None,
    ) -> list[dict[str, Any]]:
        """Read audit events, newest first.

        :param limit: Maximum number of events.
        :param start: Oldest instant to include.
        :param end: Newest instant to include.
        :param before_at: Cursor ``at`` text as the API sent it (needs ``before_id``).
        :param before_id: Cursor event id (needs ``before_at``).
        :return: Events strictly older than the cursor when given.
        """
        params: dict[str, Any] = {"limit": limit}
        if start is not None:
            params["from"] = start.isoformat()
        if end is not None:
            params["to"] = end.isoformat()
        if before_at is not None and before_id is not None:
            params["before_at"] = before_at
            params["before_id"] = before_id
        return self._send("GET", "/history/events", params=params).json()

    def events_page(
        self,
        limit: int,
        *,
        page_size: int,
        start: datetime | None = None,
        end: datetime | None = None,
        before_at: str | None = None,
        before_id: int | None = None,
    ) -> dict[str, Any]:
        """Read one event page with the range total and, when a cursor is given, the newer count.

        :param limit: Maximum number of events (the history page asks for one extra).
        :param page_size: Events displayed per page (page numbers and the previous cursor).
        :param start: Oldest instant to include.
        :param end: Newest instant to include.
        :param before_at: Cursor ``at`` text as the API sent it (needs ``before_id``).
        :param before_id: Cursor event id (needs ``before_at``).
        :return: ``events``, ``total``, ``page``, ``pages`` and, when present, ``newer_count`` and ``previous_*``.
        :raises ApiProblem: The body is not a JSON object.
        """
        params: dict[str, Any] = {"limit": limit, "include_counts": 1, "page_size": page_size}
        if start is not None:
            params["from"] = start.isoformat()
        if end is not None:
            params["to"] = end.isoformat()
        if before_at is not None and before_id is not None:
            params["before_at"] = before_at
            params["before_id"] = before_id
        body = self._send("GET", "/history/events", params=params).json()
        if not isinstance(body, dict):
            raise ApiProblem(0, "http_error", "history events page was not an object")
        return body

    def alerts(self, limit: int = 50) -> list[dict[str, Any]]:
        """Read the display-alert catalog, most recently seen first.

        :param limit: Maximum number of rows.
        :return: Alert payloads.
        :raises ApiProblem: The body is not a JSON list.
        """
        body = self._send("GET", "/history/alerts", params={"limit": limit}).json()
        if not isinstance(body, list):
            raise ApiProblem(0, "http_error", "history alerts was not a list")
        return body

    # -- internals ----------------------------------------------------------------------------

    @staticmethod
    def _age_params(max_age_s: float | None) -> dict[str, Any] | None:
        """Build the ``max_age_s`` query parameter.

        :param max_age_s: Maximum accepted cache age, or ``None`` to omit it.
        :return: Query parameters or ``None``.
        """
        return None if max_age_s is None else {"max_age_s": f"{max_age_s:g}"}

    def _send(
        self, method: str, path: str, *, timeout_s: float | None = None, **kwargs: Any
    ) -> httpx.Response:
        """Send a request and turn failures into ``ApiProblem``.

        :param method: HTTP method.
        :param path: Path below ``/api/v1``.
        :param timeout_s: Timeout override (default: write timeout for mutating methods).
        :param kwargs: Extra ``httpx`` request arguments.
        :return: The successful response.
        :raises ApiProblem: Transport failure or an HTTP error status.
        """
        mutating = method in _MUTATING_METHODS
        if timeout_s is None:
            timeout_s = WRITE_TIMEOUT_S if mutating else self._timeout_s
        try:
            response = self._http.request(method, path, timeout=timeout_s, **kwargs)
        except httpx.HTTPError as exc:
            # Reason: the request was sent, so a mutating call may have been applied anyway.
            unknown = mutating and isinstance(exc, _SENT_ERRORS)
            code = WRITE_UNKNOWN_CODE if unknown else "api_unreachable"
            raise ApiProblem(0, code, type(exc).__name__) from None
        if response.status_code >= _HTTP_ERROR_STATUS:
            raise self._problem(response)
        return response

    @staticmethod
    def _problem(response: httpx.Response) -> ApiProblem:
        """Parse a problem+json body (anything else becomes a generic problem).

        :param response: The error response.
        :return: The problem to raise.
        """
        try:
            body = response.json()
        except ValueError:
            body = None
        if not isinstance(body, dict):
            return ApiProblem(response.status_code, "http_error", "")
        issues = body.get("issues")
        return ApiProblem(
            response.status_code,
            str(body.get("code", "http_error")),
            str(body.get("detail", "")),
            [str(issue) for issue in issues] if isinstance(issues, list) else None,
            body.get("reason") if isinstance(body.get("reason"), str) else None,
        )
