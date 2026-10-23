"""Pure display and alarm message decoding (no I/O). French texts are UI copy."""

import re
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Final, Literal

_ALARM_TOKEN: Final = re.compile(r"\bE(\d{1,2})\b")
_WHITESPACE: Final = re.compile(r"\s+")


@dataclass(frozen=True, slots=True)
class DisplayMessage:
    """A message decoded from the panel display buffer."""

    code: str
    level: Literal["info", "warning", "error"]
    title_fr: str
    action_fr: str


@dataclass(frozen=True, slots=True)
class AlarmInfo:
    """A stove alarm with its French explanation."""

    code: str
    title_fr: str
    action_fr: str
    requires_technician: bool


_ALARM_LIST: Final = (
    AlarmInfo(
        "E1",
        "Défaut de pressostat",
        "Vérifiez que le conduit de fumée et l'arrivée d'air ne sont pas obstrués, "
        "puis redémarrez le poêle sur place.",
        False,
    ),
    AlarmInfo(
        "E2",
        "Défaut de pressostat à l'allumage",
        "Vérifiez que le conduit de fumée et le brasero ne sont pas obstrués, "
        "puis redémarrez le poêle sur place.",
        False,
    ),
    AlarmInfo(
        "E3",
        "Surchauffe du réservoir à pellets",
        "Laissez le poêle refroidir et appelez un technicien avant tout redémarrage.",
        True,
    ),
    AlarmInfo(
        "E4",
        "Surchauffe des fumées",
        "Laissez le poêle refroidir et appelez un technicien avant tout redémarrage.",
        True,
    ),
    AlarmInfo(
        "E6",
        "Défaut de la vis sans fin",
        "Appelez un technicien.",
        True,
    ),
    AlarmInfo(
        "E7",
        "Fumées trop froides (réservoir vide ou brasero encrassé)",
        "Vérifiez le niveau de pellets et nettoyez le brasero, puis redémarrez sur place.",
        False,
    ),
    AlarmInfo(
        "E8",
        "Défaut de la sonde de fumées",
        "Appelez un technicien.",
        True,
    ),
    AlarmInfo(
        "E9",
        "Seuil de température d'allumage non atteint",
        "Vérifiez les pellets et nettoyez le brasero, puis redémarrez sur place.",
        False,
    ),
    AlarmInfo(
        "E10",
        "Défaut de la sonde ambiante (le poêle ne s'arrête pas)",
        "Appelez un technicien.",
        True,
    ),
    AlarmInfo(
        "E12",
        "Défaut de l'extracteur de fumées",
        "Appelez un technicien.",
        True,
    ),
    AlarmInfo(
        "E13",
        "Trappe du réservoir ouverte depuis plus d'une minute",
        "Fermez la trappe du réservoir, puis redémarrez le poêle sur place.",
        False,
    ),
    AlarmInfo(
        "E14",
        "Seuil de température d'allumage non atteint",
        "Vérifiez les pellets et nettoyez le brasero, puis redémarrez sur place.",
        False,
    ),
    AlarmInfo(
        "E19",
        "Sécurité externe déclenchée (N.PEL)",
        "Vérifiez le dispositif de sécurité externe, puis redémarrez sur place.",
        False,
    ),
)

ALARMS: Final[Mapping[str, AlarmInfo]] = MappingProxyType({a.code: a for a in _ALARM_LIST})

_VIDER: Final = DisplayMessage(
    "VIDER_BRASERO",
    "error",
    "Brasero à vider",
    "Videz le brasero sur place, puis appuyez sur ON/OFF avant le prochain allumage.",
)
_FERMER: Final = DisplayMessage(
    "FERMER_TRAPPE",
    "error",
    "Trappe du réservoir ouverte",
    "Fermez la trappe du réservoir de pellets.",
)
_SERVICE: Final = DisplayMessage(
    "SERVICE",
    "warning",
    "Entretien à prévoir",
    "Faites réviser le poêle par un technicien.",
)
_ENERGY_OK: Final = DisplayMessage(
    "ENERGY_OK",
    "info",
    "Poêle en attente (économie d'énergie)",
    "Aucune action : le poêle redémarrera automatiquement.",
)
_REFROIDISSEMENT: Final = DisplayMessage(
    "REFROIDISSEMENT",
    "info",
    "Refroidissement en cours",
    "Aucune action nécessaire.",
)
_MANQUE_TENSION: Final = DisplayMessage(
    "REDEMARRAGE_MANQUE_TENSION",
    "warning",
    "Redémarrage après une coupure de courant",
    "Vérifiez l'alimentation électrique et l'état du poêle.",
)
_NETTOYAGE: Final = DisplayMessage(
    "NETTOYAGE_BRASERO",
    "info",
    "Nettoyage du brasero en cours",
    "Aucune action nécessaire.",
)
_NO_CHARGE: Final = DisplayMessage(
    "NO_CHARGE",
    "warning",
    "Pellets non détectés",
    "Vérifiez le niveau de pellets dans le réservoir.",
)
_CLAVIER_BLOQUE: Final = DisplayMessage(
    "CLAVIER_BLOQUE",
    "info",
    "Clavier verrouillé",
    "Déverrouillez le clavier du poêle si nécessaire.",
)
# Reason: derived from sample history, not from the panel buffer, so parse_display ignores it.
SHORT_CYCLE_MESSAGE: Final = DisplayMessage(
    "SHORT_CYCLE",
    "warning",
    "Cycles allumage-arret trop rapproches",
    "Preferez l'arret OFF (modulation en P1) : revoyez la programmation",
)


def _normalize(text: str) -> str:
    """Uppercase the buffer and collapse whitespace runs.

    :param text: Raw display buffer.
    :return: Normalized text.
    """
    return _WHITESPACE.sub(" ", text.upper()).strip()


def _alarm_message(text: str) -> DisplayMessage | None:
    """Find a known ``E<n>`` alarm token in normalized text.

    :param text: Normalized display text.
    :return: Error message for the first known alarm token, else ``None``.
    """
    for match in _ALARM_TOKEN.finditer(text):
        alarm = ALARMS.get(f"E{match.group(1)}")
        if alarm is not None:
            return DisplayMessage(alarm.code, "error", alarm.title_fr, alarm.action_fr)
    return None


def parse_display(text: str) -> DisplayMessage | None:
    """Best-effort decode of the (scrolling, possibly shuffled) display buffer.

    :param text: Raw display buffer text.
    :return: Decoded message, or ``None`` when nothing is recognized.
    """
    buffer = _normalize(text)
    if "VIDER" in buffer:
        return _VIDER
    if "FERMER" in buffer:
        return _FERMER
    if "SERVICE" in buffer:
        return _SERVICE
    if "ENERGY OK" in buffer or ("ENERGY" in buffer and "OK" in buffer.split(" ")):
        return _ENERGY_OK
    if "REFROID" in buffer:
        return _REFROIDISSEMENT
    if "MANQUE DE TENSION" in buffer:
        return _MANQUE_TENSION
    if "NETTOYAGE" in buffer:
        return _NETTOYAGE
    if "NO CHARGE" in buffer:
        return _NO_CHARGE
    if "CLAVIER BLOQUE" in buffer:
        return _CLAVIER_BLOQUE
    return _alarm_message(buffer)
