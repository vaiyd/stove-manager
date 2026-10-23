"""Thread-safe client for the Navel T009 local JSON API."""

import json
import logging
import secrets
import threading
import time
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

import httpx

from stove_pilot.common.exceptions import (
    GatewayNackError,
    GatewayProtocolError,
    GatewayUnavailableError,
)

_ENDPOINT = "/cgi-bin/GetJson.js"
_PROTOCOL = "RWMSmaster"
_READ_COMMANDS = frozenset({"Identity", "RequestReading"})
_BLANK_RETRIES = 3
_BLANK_EXTRA_SLEEP_S = 0.3
_LOGGER = logging.getLogger(__name__)


class _BlankReply(GatewayProtocolError):
    """The module answered HTTP 200 with a whitespace-only body (transient glitch)."""


@dataclass(frozen=True)
class NavelIdentity:
    """Module identity returned by ``Identity``."""

    device_type: str
    revision: str


class NavelClient:
    """Serialize JSON-envelope commands to one Navel module."""

    def __init__(
        self,
        *,
        base_url: str,
        device_id: str,
        security: str,
        timeout_s: float = 10.0,
        min_interval_s: float = 0.2,
    ) -> None:
        """Build the client; no request is sent until the first command.

        :param base_url: Module base URL.
        :param device_id: Module identifier (colons are stripped).
        :param security: Module security password.
        :param timeout_s: HTTP timeout in seconds.
        :param min_interval_s: Minimum delay between two requests.
        """
        self._device_id = device_id.replace(":", "").upper()
        self._security = security
        self._session_id = str(secrets.randbelow(10**8))
        self._min_interval_s = min_interval_s
        self._lock = threading.Lock()
        self._last_request_at = 0.0
        self._identified = False
        self._http = httpx.Client(base_url=base_url, timeout=timeout_s)

    def identify(self) -> NavelIdentity:
        """Authenticate the session.

        :return: Module type and revision.
        """
        with self._lock:
            return self._identify_locked()

    def read(self, items: Sequence[int]) -> list[int]:
        """Read encoded items in one round trip.

        :param items: Items built with ``encode_item``.
        :return: One byte value per item.
        """
        payload = {"Cmd": "RequestReading", "Protocol": _PROTOCOL, **self._item_fields(items)}
        return self._values(self._command(payload), expected=len(items))

    def write(self, items: Sequence[int], values: Sequence[int]) -> list[int]:
        """Write encoded items.

        :param items: Items built with ``encode_item``.
        :param values: One byte value per item.
        :return: Values echoed by the module.
        """
        if len(items) != len(values):
            raise ValueError("items and values must have the same length")
        payload = {
            "Cmd": "RequestWriting",
            "Protocol": _PROTOCOL,
            **self._item_fields(items),
            "Values": list(values),
            "Masks": [0xFF] * len(items),
        }
        return self._values(self._command(payload), expected=len(items))

    def close(self) -> None:
        """Release HTTP resources."""
        self._http.close()

    def _command(self, payload: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            if not self._identified:
                self._identify_locked()
            try:
                return self._send(payload)
            except GatewayNackError:
                # Reason: the module may drop the session silently; re-authenticate once.
                self._identified = False
                self._identify_locked()
                return self._send(payload)

    def _identify_locked(self) -> NavelIdentity:
        pl = self._send({"Cmd": "Identity", "Id": self._device_id, "Security": self._security})
        device = (pl.get("Devices") or [{}])[0]
        self._identified = True
        return NavelIdentity(str(device.get("Type", "")), str(device.get("Revision", "")))

    def _send(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Send one command, retrying blank replies for read-type commands only.

        :param payload: Command payload.
        :return: Reply payload.
        """
        command = payload["Cmd"]
        attempts = 1 + (_BLANK_RETRIES if command in _READ_COMMANDS else 0)
        for attempt in range(1, attempts + 1):
            try:
                return self._send_once(payload)
            except _BlankReply:
                if command == "RequestWriting":
                    # Reason: a blank reply gives no proof the write was not applied.
                    raise GatewayProtocolError(
                        f"Blank reply for {command}: write outcome unknown; "
                        "verify it with a read before retrying"
                    ) from None
                if attempt == attempts:
                    break
                _LOGGER.debug("Blank reply for %s (attempt %d/%d)", command, attempt, attempts)
                time.sleep(_BLANK_EXTRA_SLEEP_S)
        raise GatewayProtocolError(
            f"Module returned blank replies for {command} ({attempts} attempts)"
        ) from None

    def _send_once(self, payload: dict[str, Any]) -> dict[str, Any]:
        self._throttle()
        envelope = {"mt": "Q", "s": {"sj": self._session_id, "ss": "browser"}, "pl": payload}
        body = json.dumps(envelope, separators=(",", ":"))
        try:
            response = self._http.get(f"{_ENDPOINT}?{quote(body, safe='')}")
            response.raise_for_status()
            if not response.text.strip():
                raise _BlankReply(f"Blank reply for {payload['Cmd']}")
            reply = response.json()
        except httpx.HTTPError as exc:
            # Reason: httpx messages and chained causes embed the request URL, which
            # carries the security password; drop the cause and keep only the type.
            raise GatewayUnavailableError(
                f"Navel request failed: {payload['Cmd']} ({type(exc).__name__})"
            ) from None
        except ValueError as exc:
            raise GatewayProtocolError(f"Non-JSON reply for {payload['Cmd']}") from exc
        finally:
            self._last_request_at = time.monotonic()
        pl = reply.get("pl") if isinstance(reply, dict) else None
        if not isinstance(pl, dict):
            raise GatewayProtocolError(f"Missing payload for {payload['Cmd']}")
        if reply.get("mt") == "N":
            raise GatewayNackError(payload["Cmd"], int(pl.get("NackErrCode", 0)))
        return pl

    def _throttle(self) -> None:
        wait = self._min_interval_s - (time.monotonic() - self._last_request_at)
        if wait > 0:
            time.sleep(wait)

    @staticmethod
    def _item_fields(items: Sequence[int]) -> dict[str, list[Any]]:
        if not items:
            raise ValueError("at least one item is required")
        count = len(items)
        return {"BitData": [8] * count, "Endianess": ["L"] * count, "Items": list(items)}

    @staticmethod
    def _values(pl: dict[str, Any], *, expected: int) -> list[int]:
        values = pl.get("Values")
        if not isinstance(values, list) or len(values) != expected:
            raise GatewayProtocolError("Unexpected Values in module reply")
        return [int(value) for value in values]
