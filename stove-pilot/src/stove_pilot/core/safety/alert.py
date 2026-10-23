"""French texts for a failed safety restart. Pure: no I/O."""

from typing import Final

from stove_pilot.core.safety.models import (
    REASON_ALARM,
    REASON_ATTEMPTS_EXHAUSTED,
    REASON_IGNITION_FAILED,
    REASON_UNKNOWN,
)
from stove_pilot.core.stove.messages import DisplayMessage

SAFETY_FAILURE_CODE: Final = "SAFETY_RESTART_FAILED"
SAFETY_FAILURE_TITLE: Final = "Rallumage de securite impossible"

_ALARM_ACTION: Final = (
    "Le poele est en alarme : intervenez sur place et acquittez l'alarme sur le poele."
)
_UNKNOWN_ACTION: Final = "L'etat du poele est inconnu : verifiez le poele."
_IGNITION_FAILED_ACTION: Final = (
    "Le poele s'est eteint pendant l'allumage : verifiez le brasero, puis rearmez dans Reglages."
)
_REJECTED_ACTION: Final = (
    "Le rallumage a ete refuse ({reason}) : verifiez le poele, puis rearmez dans Reglages."
)


def failure_action(reason: str | None, attempts: int) -> str:
    """Map a failure reason to the French glance action.

    :param reason: Persisted failure reason, or ``None``.
    :param attempts: Attempts already counted for this episode.
    :return: Plain-ASCII French action text.
    """
    if reason == REASON_ALARM:
        return _ALARM_ACTION
    if reason == REASON_UNKNOWN:
        return _UNKNOWN_ACTION
    if reason == REASON_IGNITION_FAILED:
        return _IGNITION_FAILED_ACTION
    if reason == REASON_ATTEMPTS_EXHAUSTED:
        noun = "tentative" if attempts == 1 else "tentatives"
        return (
            f"Le poele ne s'est pas allume apres {attempts} {noun} : "
            "verifiez les pellets et le brasero, puis rearmez dans Reglages."
        )
    code = reason if reason else "rejected"
    return _REJECTED_ACTION.format(reason=code)


def failure_message(reason: str | None, attempts: int) -> DisplayMessage:
    """Build the alert-store message for a failed safety episode.

    :param reason: Persisted failure reason, or ``None``.
    :param attempts: Attempts already counted for this episode.
    :return: Error message with code ``SAFETY_RESTART_FAILED``.
    """
    return DisplayMessage(
        SAFETY_FAILURE_CODE,
        "error",
        SAFETY_FAILURE_TITLE,
        failure_action(reason, attempts),
    )
