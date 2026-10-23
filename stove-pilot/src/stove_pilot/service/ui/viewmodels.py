"""Template-ready view models built from API payloads (no I/O, French texts via ``t``)."""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Final, Self

from stove_pilot.service.ui.formatting import (
    hhmm,
    parse_datetime,
    since_label,
    when_label,
)
from stove_pilot.service.ui.i18n import has_key, t
from stove_pilot.service.ui.safety_badge import SafetyBadge
from stove_pilot.service.ui.suspension_view import SuspensionView

DRIFT_BADGE_THRESHOLD_S: Final = 120
_SECONDS_PER_MINUTE: Final = 60
_ALERT_CLASSES: Final = {
    "info": "alert-outline alert-warning text-base-content",
    "warning": "alert-warning",
    "error": "alert-error",
}
_BADGE_CLASSES: Final = {
    "OFF": "badge-neutral",
    "IGNITION": "badge-warning",
    "RUNNING": "badge-secondary",
    "CLEANING": "badge-outline badge-warning border-warning text-base-content",
    "SHUTDOWN": "badge-warning",
    "STANDBY": "badge-outline badge-warning border-warning text-base-content",
    "ALARM": "badge-error",
    "UNKNOWN": "badge-neutral",
}
_UNKNOWN_STATE: Final = "UNKNOWN"
_STOPPABLE_STATES: Final = frozenset({"IGNITION", "RUNNING", "CLEANING", "STANDBY"})
_STARTABLE_STATES: Final = frozenset({"OFF", "SHUTDOWN"})


@dataclass(frozen=True, slots=True)
class CapabilitiesView:
    """Which stove writes are allowed."""

    start: bool
    stop: bool
    setpoint: bool
    power: bool


@dataclass(frozen=True, slots=True)
class StatusView:
    """The stove status prepared for display."""

    state: str
    state_label: str
    badge_class: str
    ambient_text: str
    setpoint_text: str
    fumes_text: str
    power_level: int
    stove_time: str | None
    drift_text: str | None
    polled_at: str
    polled_time: str
    stale: bool
    is_ignition: bool
    capabilities: CapabilitiesView
    energy_stop_off: bool

    @classmethod
    def from_api(cls, payload: dict[str, Any]) -> Self:
        """Map a ``StoveStatusDto`` payload.

        :param payload: Status payload.
        :return: The view.
        """
        state = payload.get("state", _UNKNOWN_STATE)
        if state not in _BADGE_CLASSES:
            state = _UNKNOWN_STATE
        caps = payload.get("capabilities") or {}
        stove_time = parse_datetime(payload.get("stove_time"))
        polled = parse_datetime(payload.get("polled_at"))
        return cls(
            state=state,
            state_label=t(f"state.{state}"),
            badge_class=_BADGE_CLASSES[state],
            ambient_text=f"{float(payload['ambient_temp_c']):.1f}",
            setpoint_text=f"{float(payload['setpoint_c']):g}",
            fumes_text=f"{float(payload['fumes_temp_c']):.0f}",
            power_level=int(payload["power_level"]),
            stove_time=None if stove_time is None else hhmm(stove_time),
            drift_text=_drift_text(payload.get("clock_drift_s")),
            polled_at="" if polled is None else polled.isoformat(timespec="milliseconds"),
            polled_time="" if polled is None else hhmm(polled),
            stale=bool(payload.get("stale", False)),
            is_ignition=state == "IGNITION",
            capabilities=CapabilitiesView(
                start=bool(caps.get("start", False)),
                stop=bool(caps.get("stop", False)),
                setpoint=bool(caps.get("setpoint", False)),
                power=bool(caps.get("power", False)),
            ),
            energy_stop_off=_energy_stop_off(payload.get("energy_saving")),
        )


def _energy_stop_off(energy: dict[str, Any] | None) -> bool:
    """Tell whether the ENERGY SAVING STOP is mapped and currently OFF.

    :param energy: ``energy_saving`` member of the status payload.
    :return: ``True`` when a vacation would have to switch STOP on.
    """
    return isinstance(energy, dict) and energy.get("stop_offset_c") is None


def _drift_text(drift_s: float | None) -> str | None:
    """Describe a clock drift above the badge threshold.

    :param drift_s: Stove minus server time in seconds.
    :return: For example ``+16 min``, or ``None`` when the drift is small or unknown.
    """
    if drift_s is None or abs(drift_s) <= DRIFT_BADGE_THRESHOLD_S:
        return None
    minutes = round(drift_s / _SECONDS_PER_MINUTE)
    return t("glance.clock_drift", minutes=f"{minutes:+d}")


@dataclass(frozen=True, slots=True)
class AlertView:
    """The top alert of the glance card."""

    css_class: str
    title: str
    action: str | None


@dataclass(frozen=True, slots=True)
class ProgrammingLine:
    """One programming feature line (``dimmed`` while programming is suspended)."""

    label: str
    text: str
    dimmed: bool


def _setpoint_text(current: dict[str, Any]) -> str | None:
    """Format a setpoint for the programming line.

    :param current: ``CurrentProgramDto`` payload.
    :return: The number text, or ``None`` when it is missing.
    """
    raw = current.get("setpoint_c")
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        return None
    return f"{float(raw):g}"


def _running_text(current: dict[str, Any], now: datetime) -> str | None:
    """Describe the window covering now.

    :param current: ``CurrentProgramDto`` payload.
    :param now: Local current time.
    :return: The French text, or ``None`` when the payload cannot be shown.
    """
    setpoint = _setpoint_text(current)
    start = current.get("start")
    if setpoint is None or not isinstance(start, str) or not start:
        return None
    if current.get("open_ended"):
        return t("programming.running_open", start=start, setpoint=setpoint)
    stop_at = parse_datetime(current.get("stop_at"))
    if stop_at is not None:
        return t("programming.running_until", when=when_label(stop_at, now), setpoint=setpoint)
    stop = current.get("stop")
    if isinstance(stop, str) and stop:
        return t("programming.running", start=start, stop=stop, setpoint=setpoint)
    return None


def _next_text(upcoming: dict[str, Any], now: datetime) -> str | None:
    """Describe the next chrono event.

    :param upcoming: ``NextProgramDto`` payload.
    :param now: Local current time.
    :return: The French text, or ``None`` when the instant is missing.
    """
    at = parse_datetime(upcoming.get("at"))
    if at is None:
        return None
    when = when_label(at, now)
    action = upcoming.get("action")
    if action == "stop":
        return t("programming.next_stop", when=when)
    if action == "start":
        return t("programming.next_start", when=when)
    return t("programming.next", when=when)


def _programming_text(entry: dict[str, Any], now: datetime) -> str:
    """Choose the French text of one programming entry.

    :param entry: ``ProgrammingSummaryDto`` payload.
    :param now: Local current time.
    :return: A non-empty French sentence.
    """
    blocked = entry.get("blocked_reason")
    if blocked:
        key = f"programming.blocked.{blocked}"
        text = t(key if has_key(key) else "programming.blocked.unknown")
    else:
        current = entry.get("current")
        upcoming = entry.get("next")
        text = _running_text(current, now) if isinstance(current, dict) else None
        if text is None and isinstance(upcoming, dict):
            text = _next_text(upcoming, now)
        if not text:
            text = t("programming.none")
    if not text.strip():
        return t("programming.none")
    return text


def _programming_line(entry: dict[str, Any], now: datetime, force_dim: bool) -> ProgrammingLine:
    """Build the French line of one programming entry.

    :param entry: ``ProgrammingSummaryDto`` payload.
    :param now: Local current time.
    :param force_dim: Grey the line because programming is suspended.
    :return: The line.
    """
    return ProgrammingLine(
        str(entry.get("label", "")),
        _programming_text(entry, now),
        force_dim or bool(entry.get("blocked_reason")),
    )


@dataclass(frozen=True, slots=True)
class GlanceView:
    """The at-a-glance card (``suspension`` is set only while programming is suspended)."""

    status: StatusView
    alert: AlertView | None
    programming: list[ProgrammingLine] = field(default_factory=list)
    suspension: SuspensionView | None = None
    safety: SafetyBadge | None = None

    @classmethod
    def from_summary(cls, payload: dict[str, Any], now: datetime | None = None) -> Self:
        """Map a ``SummaryDto`` payload.

        :param payload: Summary payload.
        :param now: Local current time (defaults to the system local time).
        :return: The view.
        """
        moment = now or datetime.now().astimezone()
        raw_alert = payload.get("alert")
        suspension_payload = payload.get("suspension")
        suspension = (
            None
            if suspension_payload is None
            else SuspensionView.from_api(suspension_payload, moment)
        )
        suspended = suspension is not None and suspension.suspended
        return cls(
            status=StatusView.from_api(payload["stove"]),
            alert=None if raw_alert is None else _alert(raw_alert),
            programming=[
                _programming_line(entry, moment, suspended)
                for entry in payload.get("programming", [])
            ],
            suspension=suspension if suspended else None,
            safety=SafetyBadge.from_summary(payload.get("safety")),
        )


def _alert(raw: dict[str, Any]) -> AlertView:
    """Map an ``AlertDto`` payload.

    :param raw: Alert payload.
    :return: The view (unknown levels render as info).
    """
    return AlertView(
        _ALERT_CLASSES.get(str(raw.get("level")), "alert-outline alert-warning text-base-content"),
        str(raw.get("title", "")),
        raw.get("action") or None,
    )


@dataclass(frozen=True, slots=True)
class ControlsView:
    """Everything the controls partial needs.

    ``suspension`` is ``None`` when the API has no suspendable feature (button hidden).
    """

    status: StatusView
    suspension: SuspensionView | None

    @property
    def vacation_active(self) -> bool:
        """Whether a vacation is running (the setpoint is locked by the API)."""
        return self.suspension is not None and self.suspension.vacation

    @property
    def show_stop(self) -> bool:
        """Whether the stove is running, so the main button stops it."""
        return self.status.state in _STOPPABLE_STATES

    @property
    def start_available(self) -> bool:
        """Whether a start is meaningful in the current state."""
        return self.status.state in _STARTABLE_STATES

    @classmethod
    def build(
        cls, status: dict[str, Any], programming: dict[str, Any] | None, now: datetime
    ) -> Self:
        """Combine the status and the suspension payloads.

        :param status: ``StoveStatusDto`` payload.
        :param programming: ``GET /programming`` payload, or ``None`` without participant.
        :param now: Local current time.
        :return: The view.
        """
        return cls(
            StatusView.from_api(status),
            None if programming is None else SuspensionView.from_api(programming, now),
        )


@dataclass(frozen=True, slots=True)
class EventView:
    """One line of the events timeline."""

    when: str
    text: str


def _command_text(detail: dict[str, Any]) -> str | None:
    """Describe a ``command`` event.

    :param detail: Event detail (``command`` and ``result``).
    :return: The text, or ``None`` when the command or result is unknown.
    """
    command = f"events.command.{detail.get('command')}"
    result = f"events.result.{detail.get('result')}"
    if not (has_key(command) and has_key(result)):
        return None
    return t("events.command", command=t(command), result=t(result))


def _state_change_text(detail: dict[str, Any]) -> str | None:
    """Describe a ``state_changed`` event.

    :param detail: Event detail (``from_state`` and ``to_state``).
    :return: The text, or ``None`` when a state is unknown.
    """
    before = f"state.{detail.get('from_state')}"
    after = f"state.{detail.get('to_state')}"
    if not (has_key(before) and has_key(after)):
        return None
    if before == after:
        return t("events.state_phase", state=t(after))
    return t("events.state_changed", from_state=t(before), to_state=t(after))


def event_text(event: dict[str, Any]) -> str:
    """Describe one audit event in French (unknown kinds get a neutral label).

    :param event: ``EventDto`` payload.
    :return: The text.
    """
    kind = str(event.get("kind", ""))
    detail = event.get("detail") or {}
    text: str | None
    if kind == "command":
        text = _command_text(detail)
    elif kind == "state_changed":
        text = _state_change_text(detail)
    else:
        text = t(f"event.kind.{kind}") if has_key(f"event.kind.{kind}") else None
    return text or t("event.kind.unknown")


def events_from_api(payload: list[dict[str, Any]], now: datetime) -> list[EventView]:
    """Map ``EventDto`` payloads.

    :param payload: Events, newest first.
    :param now: Local current time.
    :return: One view per event.
    """
    views = []
    for event in payload:
        at = parse_datetime(event.get("at"))
        views.append(EventView("" if at is None else since_label(at, now), event_text(event)))
    return views
