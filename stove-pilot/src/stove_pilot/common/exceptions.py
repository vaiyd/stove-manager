"""Exception hierarchy shared by all Stove Pilot layers."""

from collections.abc import Sequence
from typing import Any


class StovePilotError(Exception):
    """Base class for all Stove Pilot errors."""


class ConfigurationError(StovePilotError):
    """Invalid or missing configuration."""


class UnverifiedRegisterError(ConfigurationError):
    """A write targets a register or command that is not verified."""

    def __init__(self, name: str) -> None:
        """Store the unverified register or command name.

        :param name: Name of the unverified register or command.
        """
        super().__init__(f"{name!r} is not verified; writes are refused")
        self.name = name


class GatewayError(StovePilotError):
    """Base class for stove gateway failures."""


class GatewayUnavailableError(GatewayError):
    """The stove module cannot be reached or timed out."""


class GatewayProtocolError(GatewayError):
    """The stove module returned an unexpected or malformed response."""


class WriteOutcomeUnknownError(GatewayError):
    """A write got no valid reply and may or may not have been applied."""


class GatewayNackError(GatewayError):
    """The stove module refused a command."""

    def __init__(self, command: str, code: int) -> None:
        """Store the refused command and the module error code.

        :param command: Name of the refused command.
        :param code: Error code returned by the module.
        """
        super().__init__(f"Command {command!r} refused with code {code}")
        self.command = command
        self.code = code


class CommandRejectedError(StovePilotError):
    """A command was rejected by a safety or business rule."""

    def __init__(self, reason: str) -> None:
        """Store the rejection reason.

        :param reason: Human-readable rejection reason.
        """
        super().__init__(reason)
        self.reason = reason


class ValidationFailedError(StovePilotError):
    """Input validation failed with one or more issues."""

    def __init__(self, issues: Sequence[Any]) -> None:
        """Store the validation issues.

        :param issues: Validation issues (strings or issue objects).
        """
        super().__init__("; ".join(str(issue) for issue in issues))
        self.issues = list(issues)


class VersionConflictError(StovePilotError):
    """The resource version changed since it was last read."""


class FeatureConflictError(StovePilotError):
    """Two features conflict over the same resource."""

    def __init__(self, reason: str) -> None:
        """Store the conflict reason.

        :param reason: Human-readable conflict reason.
        """
        super().__init__(reason)
        self.reason = reason
