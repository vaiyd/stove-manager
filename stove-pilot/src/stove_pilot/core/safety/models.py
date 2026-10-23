"""Safety domain models: configuration, persisted episode, and pure decisions."""

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Final

from stove_pilot.core.stove.models import StoveState

THRESHOLD_MIN_C: Final = 5
THRESHOLD_MAX_C: Final = 20
MAX_ATTEMPTS_MIN: Final = 1
MAX_ATTEMPTS_MAX: Final = 5
RETRY_INTERVAL_MAX_MIN: Final = 240
NO_IGNITION_WAIT: Final = timedelta(minutes=5)

EVENT_RESTART: Final = "safety_restart"
EVENT_RESTART_SUCCEEDED: Final = "safety_restart_succeeded"
EVENT_RESTART_FAILED: Final = "safety_restart_failed"
EVENT_PAUSED: Final = "safety_paused"
EVENT_REARMED: Final = "safety_rearmed"

PAUSE_APP_STOP: Final = "app_stop"
PAUSE_PANEL_STOP: Final = "panel_stop"
PAUSE_REASONS: Final = frozenset({PAUSE_APP_STOP, PAUSE_PANEL_STOP})

REASON_ALARM: Final = "alarm"
REASON_UNKNOWN: Final = "unknown"
REASON_IGNITION_FAILED: Final = "ignition_failed"
REASON_ATTEMPTS_EXHAUSTED: Final = "attempts_exhausted"
REASON_HYSTERESIS: Final = "hysteresis"
REASON_STOVE_LIT: Final = "stove_lit"
WARNING_THRESHOLD_NOT_BELOW_SETPOINT: Final = "threshold_not_below_setpoint"


class SafetyPhase(StrEnum):
    """Persisted episode phase."""

    IDLE = "idle"
    RESTARTING = "restarting"
    WAITING_RETRY = "waiting_retry"
    FAILED = "failed"


class SafetyStatus(StrEnum):
    """Derived status exposed by the API."""

    DISABLED = "disabled"
    ARMED = "armed"
    CONFIRMING = "confirming"
    RESTARTING = "restarting"
    WAITING_RETRY = "waiting_retry"
    FAILED = "failed"
    PAUSED_MANUAL = "paused_manual"
    SUSPENDED_VACATION = "suspended_vacation"
    STALE = "stale"


class StartResult(StrEnum):
    """Outcome of one guarded start, as seen by the rules."""

    APPLIED = "applied"
    DEFERRED = "deferred"
    NO_OP = "no_op"
    REJECTED = "rejected"
    UNKNOWN = "unknown"
    GATEWAY_ERROR = "gateway_error"


@dataclass(frozen=True, slots=True)
class SafetyConfig:
    """User-editable safety settings persisted in the safety schema."""

    enabled: bool
    threshold_c: int
    max_attempts: int
    retry_interval_min: int


@dataclass(frozen=True, slots=True)
class SafetyState:
    """Episode state. Every datetime is timezone-aware or ``None``."""

    phase: SafetyPhase = SafetyPhase.IDLE
    attempts: int = 0
    confirm_count: int = 0
    last_attempt_at: datetime | None = None
    next_attempt_at: datetime | None = None
    failure_reason: str | None = None
    paused_reason: str | None = None
    last_check_at: datetime | None = None
    last_stove_state: str | None = None
    last_polled_at: datetime | None = None
    last_app_stop_at: datetime | None = None
    vacation_was_active: bool = False


@dataclass(frozen=True, slots=True)
class SafetyEvent:
    """One safety fact for the history sink. ``detail`` holds the plan fields."""

    kind: str
    detail: dict[str, object]


@dataclass(frozen=True, slots=True)
class SafetyParams:
    """Container-level knobs. The retry floor is ``min_restart_interval_min``."""

    confirm_polls: int
    rearm_hysteresis_c: float
    min_restart_interval_min: int
    poll_interval_s: float = 30


@dataclass(frozen=True, slots=True)
class SafetyObservation:
    """One fresh poll, plus the manual-stop attribution already decided by the caller."""

    polled_at: datetime
    ambient_c: float
    stove_state: StoveState
    pending_start: bool
    vacation_active: bool
    manual_stop_reason: str | None
    app_stop_at: datetime | None
    alarm_code: str | None = None


@dataclass(frozen=True, slots=True)
class SafetyDecision:
    """Next episode, whether the facade should call start, and the events to record."""

    state: SafetyState
    start_requested: bool
    events: tuple[SafetyEvent, ...]


@dataclass(frozen=True, slots=True)
class SafetyReport:
    """API view of the safety feature. Plain data: no I/O and no clock read."""

    config: SafetyConfig
    state: SafetyState
    status: SafetyStatus
    warning: str | None
    threshold_min_c: int
    threshold_max_c: int
    max_attempts_min: int
    max_attempts_max: int
    retry_interval_min_min: int
    retry_interval_max_min: int
