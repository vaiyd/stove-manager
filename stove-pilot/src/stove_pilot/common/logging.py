"""JSON-lines logging with secret redaction."""

import json
import logging
import sys
from datetime import UTC, datetime
from typing import Any

_REDACTED = "***"
_STANDARD_ATTRS = frozenset(
    logging.LogRecord("", 0, "", 0, "", None, None).__dict__
) | {"message", "asctime", "taskName"}


class SecretRedactionFilter(logging.Filter):
    """Replace registered secret values with ``***`` in ``msg`` and ``args``."""

    _secrets: set[str] = set()

    @classmethod
    def register(cls, value: str) -> None:
        """Register a secret value to mask in log records.

        :param value: Secret value; empty values are ignored.
        """
        if value:
            cls._secrets.add(value)

    @classmethod
    def _mask(cls, text: str) -> str:
        for secret in cls._secrets:
            text = text.replace(secret, _REDACTED)
        return text

    def _mask_arg(self, arg: Any) -> Any:
        if isinstance(arg, str):
            return self._mask(arg)
        if arg is not None and any(secret in str(arg) for secret in self._secrets):
            return self._mask(str(arg))
        return arg

    def filter(self, record: logging.LogRecord) -> bool:
        """Mask secrets in the record message and arguments.

        :param record: Log record to sanitize in place.
        :return: Always ``True``.
        """
        if not self._secrets:
            return True
        record.msg = self._mask(str(record.msg))
        if isinstance(record.args, tuple):
            record.args = tuple(self._mask_arg(arg) for arg in record.args)
        elif isinstance(record.args, dict):
            record.args = {key: self._mask_arg(val) for key, val in record.args.items()}
        return True


class JsonLinesFormatter(logging.Formatter):
    """Format log records as one JSON object per line."""

    def format(self, record: logging.LogRecord) -> str:
        """Serialize a record with ``ts``, ``level``, ``logger``, ``msg`` and extras.

        :param record: Log record to format.
        :return: JSON line.
        """
        payload: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        for key, value in record.__dict__.items():
            if key not in _STANDARD_ATTRS and not key.startswith("_"):
                payload[key] = value
        if record.exc_info:
            payload["exc"] = SecretRedactionFilter._mask(self.formatException(record.exc_info))
        return json.dumps(payload, default=str, ensure_ascii=False)


def configure_logging(level: str) -> None:
    """Install the JSON-lines handler and redaction filter on the root logger.

    :param level: Root log level name (e.g. ``INFO``).
    """
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(JsonLinesFormatter())
    handler.addFilter(SecretRedactionFilter())
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level.upper())
