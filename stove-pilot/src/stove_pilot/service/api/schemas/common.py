"""DTO base classes and shared response models."""

from typing import Any

from pydantic import BaseModel, ConfigDict


class RequestModel(BaseModel):
    """Base of request bodies: unknown fields are rejected."""

    model_config = ConfigDict(extra="forbid")


class ProblemDto(BaseModel):
    """The problem+json error body (``issues`` for 422, ``reason`` for rejected commands)."""

    type: str
    title: str
    status: int
    detail: str
    code: str
    issues: list[str] | None = None
    reason: str | None = None


class SettingChangeDto(BaseModel):
    """Result of a setting write: ``changed`` is false when the stove already held the value."""

    changed: bool


def dump(dto: BaseModel) -> dict[str, Any]:
    """Serialize a DTO to a JSON-safe dict (datetimes become ISO 8601 strings).

    :param dto: Response DTO.
    :return: JSON-compatible dict.
    """
    return dto.model_dump(mode="json")
