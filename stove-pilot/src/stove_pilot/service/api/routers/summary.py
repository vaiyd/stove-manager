"""``GET /summary``: the at-a-glance aggregate (stove, top alert, programming lines).

Programming lines come from injected provider callables. The safety block is optional.
Route handlers are closures defined inside ``register_*`` functions (see ``health``).
"""

from collections.abc import Callable, Sequence
from datetime import datetime, tzinfo
from typing import Any, Final

from flask import Blueprint

from stove_pilot.common.exceptions import ConfigurationError
from stove_pilot.common.settings import Settings
from stove_pilot.core.history.repository import HistoryRepository
from stove_pilot.core.safety.alert import failure_message
from stove_pilot.core.safety.models import SafetyReport, SafetyStatus
from stove_pilot.core.safety.repository import SafetyRepository
from stove_pilot.core.stove.repository import StoveRepository
from stove_pilot.service.api.parsing import query_float
from stove_pilot.service.api.schemas.common import dump
from stove_pilot.service.api.schemas.stove import StoveStatusDto
from stove_pilot.service.api.schemas.summary import (
    AlertDto,
    ProgrammingSummaryDto,
    SafetySummaryDto,
    SummaryDto,
    SuspensionDto,
)

_STANDBY: Final = "STANDBY"
# Reason: the display message of the ENERGY SAVING wait carries no threshold, the alert does.
_ENERGY_WAIT_MESSAGES: Final = frozenset({"ENERGY_OK"})
_SECONDS_PER_MINUTE: Final = 60
_LEVEL_RANK: Final = {"error": 3, "warning": 2, "info": 1}


def build_alert(
    stove: StoveStatusDto, tolerance_s: float, derived: AlertDto | None = None
) -> AlertDto | None:
    """Pick the glance alert, ranking a derived warning against the stove display.

    Severity is error, then warning, then info. The same severity keeps the stove
    display, so an error or a warning on the panel wins over ``SHORT_CYCLE``.
    The ENERGY SAVING wait (info) and the clock-drift warning sit below a derived
    warning. With ``derived`` omitted, the order is unchanged: alarm, display
    message, ENERGY SAVING wait, clock drift.

    :param stove: Stove status DTO.
    :param tolerance_s: Maximum accepted absolute clock drift in seconds.
    :param derived: Derived alert such as ``SHORT_CYCLE``, or ``None``.
    :return: The alert, or ``None`` when there is nothing to report.
    """
    primary = _primary_alert(stove)
    context = _context_alert(stove, tolerance_s)
    if primary is not None and _outranks(primary, derived):
        return primary
    if derived is not None and _outranks(derived, context):
        return derived
    return context


def _primary_alert(stove: StoveStatusDto) -> AlertDto | None:
    """Return the alarm or the panel message, skipping the ENERGY SAVING placeholder.

    :param stove: Stove status DTO.
    :return: The stove alert, or ``None`` when the panel has nothing else to say.
    """
    if stove.alarm is not None:
        return AlertDto(level="error", title=stove.alarm.title, action=stove.alarm.action)
    standby = stove.state == _STANDBY
    message = stove.display_message
    if message is not None and not (standby and message.code in _ENERGY_WAIT_MESSAGES):
        return AlertDto(level=message.level, title=message.title, action=message.action)
    return None


def _context_alert(stove: StoveStatusDto, tolerance_s: float) -> AlertDto | None:
    """Return the ENERGY SAVING wait or the clock-drift warning.

    :param stove: Stove status DTO.
    :param tolerance_s: Maximum accepted absolute clock drift in seconds.
    :return: The lower-priority alert, or ``None``.
    """
    if stove.state == _STANDBY:
        return AlertDto(level="info", title=_energy_wait_title(stove), action=None)
    drift = stove.clock_drift_s
    if drift is not None and abs(drift) > tolerance_s:
        minutes = round(abs(drift) / _SECONDS_PER_MINUTE)
        return AlertDto(
            level="warning",
            title=f"Horloge du poele decalee de {minutes} min",
            action="Synchronisez l'horloge du poele depuis les reglages.",
        )
    return None


def _outranks(alert: AlertDto, other: AlertDto | None) -> bool:
    """Tell whether ``alert`` wins, with a tie keeping ``alert``.

    :param alert: Candidate that wins a tie.
    :param other: The other candidate, or ``None``.
    :return: ``True`` when ``alert`` is shown.
    """
    if other is None:
        return True
    return _LEVEL_RANK.get(alert.level, 0) >= _LEVEL_RANK.get(other.level, 0)


def _energy_wait_title(stove: StoveStatusDto) -> str:
    energy = stove.energy_saving
    if energy is None or energy.restart_below_c is None:
        return "Veille economie - rallumage automatique"
    return f"Veille economie - rallumage automatique sous {energy.restart_below_c:g} C"


def register_summary_routes(
    bp: Blueprint,
    settings: Settings,
    get_stove_repo: Callable[[], StoveRepository],
    summary_providers: Sequence[Callable[[datetime], ProgrammingSummaryDto]],
    get_suspension: Callable[[], SuspensionDto | None] | None,
    tz: tzinfo,
    get_history_repo: Callable[[], HistoryRepository],
    get_safety_repo: Callable[[], SafetyRepository] | None = None,
) -> None:
    """Add the summary route.

    :param bp: API blueprint.
    :param settings: Application settings (clock drift tolerance).
    :param get_stove_repo: Lazy getter of the stove repository.
    :param summary_providers: One callable per enabled feature, taking the aware local time.
    :param get_suspension: Optional suspension getter (``None`` until the suspension is wired).
    :param tz: Local timezone passed to the providers.
    :param get_history_repo: Lazy getter of the history repository (derived alerts).
    :param get_safety_repo: Lazy getter of the safety repository (``None`` when not wired).
    """

    @bp.get("/summary")
    def summary() -> dict[str, Any]:
        stove = StoveStatusDto.load(get_stove_repo(), query_float("max_age_s", minimum=0))
        now = datetime.now(tz)
        report = _safety_report(get_safety_repo)
        return dump(
            SummaryDto(
                stove=stove,
                alert=_with_safety_alert(
                    stove,
                    build_alert(
                        stove,
                        settings.clock_drift_tolerance_s,
                        _derived_alert(get_history_repo()),
                    ),
                    report,
                ),
                programming=[provider(now) for provider in summary_providers],
                suspension=None if get_suspension is None else get_suspension(),
                safety=None if report is None else _safety_summary(report),
            )
        )


def _safety_report(
    get_safety_repo: Callable[[], SafetyRepository] | None,
) -> SafetyReport | None:
    """Read the safety report. A corrupt row becomes ``None`` and one ERROR line.

    :param get_safety_repo: Lazy getter, or ``None`` when safety is not wired.
    :return: The report, or ``None`` when safety is absent or unreadable.
    """
    if get_safety_repo is None:
        return None
    repo = get_safety_repo()
    try:
        return repo.view()
    except ConfigurationError as exc:
        repo.note_unreadable(exc)
        return None


def _with_safety_alert(
    stove: StoveStatusDto, alert: AlertDto | None, report: SafetyReport | None
) -> AlertDto | None:
    """Show a failed safety restart after the stove alarm and before every other alert.

    :param stove: Stove status DTO. Its alarm still wins.
    :param alert: Alert already chosen by ``build_alert``.
    :param report: Safety report, or ``None`` when safety is not wired.
    :return: The glance alert.
    """
    if stove.alarm is not None or report is None or report.status is not SafetyStatus.FAILED:
        return alert
    message = failure_message(report.state.failure_reason, report.state.attempts)
    return AlertDto(level=message.level, title=message.title_fr, action=message.action_fr)


def _safety_summary(report: SafetyReport) -> SafetySummaryDto:
    """Map the glance safety block.

    :param report: Safety report.
    :return: Enabled flag, derived status and threshold.
    """
    return SafetySummaryDto(
        enabled=report.config.enabled,
        status=report.status.value,
        threshold_c=report.config.threshold_c,
    )


def _derived_alert(repo: HistoryRepository) -> AlertDto | None:
    """Map the active short-cycle row onto the glance alert.

    :param repo: History repository sharing the poller's alert flag.
    :return: The derived alert, or ``None`` when it is clear.
    """
    message = repo.active_short_cycle()
    if message is None:
        return None
    return AlertDto(level=message.level, title=message.title_fr, action=message.action_fr)
