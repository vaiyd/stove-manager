"""Dependency composition: ``build_*`` factories taking a settings instance."""

import logging
import threading
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta, tzinfo
from functools import partial
from typing import Final
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from stove_pilot.common.exceptions import ConfigurationError
from stove_pilot.common.settings import Settings
from stove_pilot.core.chrono.layout import load_chrono_layout
from stove_pilot.core.chrono.repository import ChronoRepository
from stove_pilot.core.chrono.service import ChronoGateway
from stove_pilot.core.history.repository import HistoryRepository
from stove_pilot.core.history.service import HistoryStore
from stove_pilot.core.safety.chrono_check import ChronoStopMatcher
from stove_pilot.core.safety.lookup import HistoryStopLookup
from stove_pilot.core.safety.models import SafetyConfig, SafetyParams
from stove_pilot.core.safety.repository import SafetyRepository
from stove_pilot.core.safety.service import SafetyStore
from stove_pilot.core.stove.events import StoveEventRecorder
from stove_pilot.core.stove.models import StoveProfile
from stove_pilot.core.stove.poller import StatusPoller
from stove_pilot.core.stove.ports import PreferenceStore, RegisterIO
from stove_pilot.core.stove.preferences import RuntimePreferences
from stove_pilot.core.stove.profile import load_profile
from stove_pilot.core.stove.repository import StoveRepository
from stove_pilot.core.stove.rules import GuardPolicy
from stove_pilot.core.stove.service import NavelRegisterIO, StoveGateway
from stove_pilot.core.stove.simulator import SimulatedRegisterIO
from stove_pilot.core.suspension.models import KIND_VACATION
from stove_pilot.core.suspension.ports import Suspendable
from stove_pilot.core.suspension.repository import VACATION_ACTIVE, SuspensionRepository
from stove_pilot.core.suspension.service import SuspensionStore
from stove_pilot.lib.navel import NavelClient
from stove_pilot.service.safety_stove import SafetyStoveAdapter
from stove_pilot.lib.sqlite import SqliteDatabase

_LOGGER = logging.getLogger(__name__)

Clock = Callable[[], datetime]
Closer = Callable[[], None]

# Reason: a cached status older than three poll periods means the poller keeps failing.
STALE_AFTER_POLL_FACTOR: Final = 3


@dataclass(slots=True)
class Container:
    """The composed repositories.

    ``chrono`` is ``None`` when the feature is disabled. ``suspension`` is ``None`` when no
    enabled feature can be suspended. ``safety`` is always built. ``profile`` serves the
    feature value ranges.
    """

    stove: StoveRepository
    history: HistoryRepository
    profile: StoveProfile
    chrono: ChronoRepository | None = None
    suspension: SuspensionRepository | None = None
    safety: SafetyRepository | None = None
    closers: list[Closer] = field(default_factory=list)

    def close(self) -> None:
        """Run every registered closer (last registered first); failures are logged."""
        _run_closers(self.closers)


def _run_closers(closers: list[Closer]) -> None:
    """Run closers last registered first; a failure is logged and never raised.

    :param closers: Closers to run (the list is emptied).
    """
    while closers:
        closer = closers.pop()
        try:
            closer()
        except Exception:
            _LOGGER.warning("A resource failed to close", exc_info=True)


def build_navel_client(settings: Settings) -> NavelClient:
    """Build the Navel client from settings; no request is sent.

    :param settings: Application settings.
    :return: Configured client (caller closes it).
    :raises ConfigurationError: Device id or security password is blank.
    """
    security = settings.navel_security.get_secret_value()
    if not settings.navel_device_id or not security:
        raise ConfigurationError(
            "STOVE_PILOT_NAVEL_DEVICE_ID and STOVE_PILOT_NAVEL_SECURITY must be set (see .env)"
        )
    return NavelClient(
        base_url=settings.navel_base_url,
        device_id=settings.navel_device_id,
        security=security,
        timeout_s=settings.navel_timeout_s,
        min_interval_s=settings.navel_min_interval_s,
    )


def build_stove_profile(settings: Settings) -> StoveProfile:
    """Load the configured stove profile.

    :param settings: Application settings.
    :return: Profile flagged with ``allow_unverified`` in simulator mode.
    """
    return load_profile(settings.profile, allow_unverified=settings.gateway == "simulator")


def build_register_io(settings: Settings, *, clock: Clock | None = None) -> RegisterIO:
    """Build the register access for the configured gateway.

    :param settings: Application settings.
    :param clock: Simulated time source (simulator only; defaults to the system clock).
    :return: ``SimulatedRegisterIO`` or ``NavelRegisterIO`` (caller closes the latter).
    """
    if settings.gateway == "simulator":
        return SimulatedRegisterIO(
            settings.simulator_seed,
            clock or _system_clock,
            profile=build_stove_profile(settings),
        )
    return NavelRegisterIO(build_navel_client(settings), chunk_size=settings.navel_chunk_size)


def build_stove_gateway(
    settings: Settings,
    io: RegisterIO,
    *,
    clock: Clock | None = None,
    tz: tzinfo | None = None,
) -> StoveGateway:
    """Build the stove gateway; unverified writes are allowed only in simulator mode.

    :param settings: Application settings.
    :param io: Register access from ``build_register_io``.
    :param clock: Aware server time source (defaults to ``datetime.now(tz)``).
    :param tz: Local timezone (defaults to ``settings.timezone``).
    :return: The gateway.
    :raises ConfigurationError: The timezone is unknown on this host.
    """
    zone = tz or _load_timezone(settings.timezone)
    return StoveGateway(
        io,
        build_stove_profile(settings),
        clock or partial(datetime.now, zone),
        zone,
        allow_unverified=settings.gateway == "simulator",
        slow_interval_s=settings.slow_read_interval_s,
        display_max_age_s=STALE_AFTER_POLL_FACTOR * settings.poll_interval_s,
    )


def build_history_repository(
    settings: Settings, *, closers: list[Closer] | None = None
) -> HistoryRepository:
    """Build the history repository on the configured SQLite file.

    :param settings: Application settings.
    :param closers: Collects the database closer when given.
    :return: History repository using the aware server clock.
    :raises ConfigurationError: The timezone is unknown on this host.
    """
    database = SqliteDatabase(settings.db_path)
    if closers is not None:
        closers.append(database.close)
    clock = partial(datetime.now, _load_timezone(settings.timezone))
    return HistoryRepository(
        HistoryStore(database),
        clock,
        short_cycle_min_interval_min=settings.short_cycle_min_interval_min,
    )


def build_stove_repository(
    settings: Settings,
    history: HistoryRepository,
    *,
    io: RegisterIO | None = None,
    closers: list[Closer] | None = None,
    settings_lock: Callable[[], str | None] = lambda: None,
    preferences: PreferenceStore | None = None,
) -> StoveRepository:
    """Wire the profile, register access, gateway and guards into the stove repository.

    :param settings: Application settings.
    :param history: History repository (events and samples).
    :param io: Register access shared with other repositories (built here when omitted).
    :param closers: Collects the module client closer when ``io`` is built here (navel only).
    :param settings_lock: Returns why the setpoint and ENERGY SAVING are locked (late-bound).
    :param preferences: Store of the runtime clock auto-sync toggle.
    :return: The stove repository.
    :raises ConfigurationError: Invalid timezone or incomplete navel credentials.
    """
    tz = _load_timezone(settings.timezone)
    clock = partial(datetime.now, tz)
    if io is None:
        io = build_register_io(settings)
        if closers is not None and isinstance(io, NavelRegisterIO):
            closers.append(io.close)
    return StoveRepository(
        build_stove_gateway(settings, io, clock=clock, tz=tz),
        history,
        GuardPolicy(min_restart_interval=timedelta(minutes=settings.min_restart_interval_min)),
        clock,
        tz,
        stale_after=timedelta(seconds=STALE_AFTER_POLL_FACTOR * settings.poll_interval_s),
        deferred_expiry=timedelta(minutes=settings.deferred_start_expiry_min),
        deferred_stop_expiry=timedelta(minutes=settings.deferred_stop_expiry_min),
        clock_tolerance_s=settings.clock_drift_tolerance_s,
        clock_sync_min_interval=timedelta(hours=settings.clock_sync_min_interval_h),
        clock_auto_sync=settings.clock_auto_sync,
        live_read_min_interval=timedelta(seconds=settings.live_read_min_interval_s),
        settings_lock=settings_lock,
        preferences=preferences,
    )


def build_runtime_preferences(
    settings: Settings, *, closers: list[Closer] | None = None
) -> RuntimePreferences:
    """Build the runtime preferences store on the configured SQLite file.

    :param settings: Application settings.
    :param closers: Collects the database closer when given.
    :return: The preferences store (its schema is created).
    """
    database = SqliteDatabase(settings.db_path)
    if closers is not None:
        closers.append(database.close)
    return RuntimePreferences(database)


def build_chrono_repository(
    settings: Settings,
    stove: StoveRepository,
    history: HistoryRepository,
    io: RegisterIO,
    *,
    is_suspended: Callable[[], bool],
) -> ChronoRepository:
    """Build the chrono repository on the register access shared with the stove.

    Unverified layout fields are writable only with the simulator gateway.

    :param settings: Application settings (the profile name selects the chrono layout).
    :param stove: Stove repository (its ``sync_clock`` is injected as the clock sync).
    :param history: History repository (events).
    :param io: Register access shared with the stove repository.
    :param is_suspended: Tells whether programming is suspended (late-bound by the caller).
    :return: The chrono repository.
    :raises ConfigurationError: Unknown layout or timezone.
    """
    tz = _load_timezone(settings.timezone)
    allow_unverified = settings.gateway == "simulator"
    layout = load_chrono_layout(settings.profile, allow_unverified=allow_unverified)
    return ChronoRepository(
        ChronoGateway(io, layout),
        layout,
        build_stove_profile(settings),
        history,
        partial(datetime.now, tz),
        tz,
        stove.sync_clock,
        is_suspended=is_suspended,
        allow_unverified=allow_unverified,
    )


def build_suspension_repository(
    settings: Settings,
    container: Container,
    participants: Sequence[Suspendable],
    *,
    closers: list[Closer] | None = None,
) -> SuspensionRepository:
    """Build the suspension repository on the configured SQLite file.

    :param settings: Application settings.
    :param container: Composed stove and history repositories.
    :param participants: Programming features that can be suspended.
    :param closers: Collects the database closer when given.
    :return: The suspension repository.
    :raises ConfigurationError: Unknown timezone or unreadable stored state.
    """
    database = SqliteDatabase(settings.db_path)
    if closers is not None:
        closers.append(database.close)
    clock = partial(datetime.now, _load_timezone(settings.timezone))
    return SuspensionRepository(
        SuspensionStore(database),
        participants,
        container.stove,
        container.history,
        clock,
        settings.vacation_setpoint_c,
    )


def _is_suspended(container: Container) -> bool:
    suspension = container.suspension
    return suspension is not None and suspension.is_suspended()


def _settings_lock(container: Container) -> str | None:
    suspension = container.suspension
    if suspension is not None and suspension.get().kind == KIND_VACATION:
        return VACATION_ACTIVE
    return None


def _vacation_active(container: Container) -> bool:
    """Tell whether a vacation is in progress, in any journal phase.

    Programming suspension, a missing suspension, and a cleared state are false.
    ``get`` returns the stored state and does not touch the stove.

    :param container: Composed repositories.
    :return: ``True`` while the stored kind is vacation and the state is suspended.
    """
    suspension = container.suspension
    if suspension is None:
        return False
    state = suspension.get()
    return state.suspended and state.kind == KIND_VACATION


def build_safety_repository(
    settings: Settings,
    container: Container,
    *,
    closers: list[Closer] | None = None,
) -> SafetyRepository:
    """Build the safety repository on the configured SQLite file.

    The chrono matcher is omitted when chrono is disabled. The vacation check is
    late-bound so it sees the suspension stored on the container.

    :param settings: Application settings. The four ``safety_*_default*`` values seed the card.
    :param container: Composed stove, history, and optional chrono.
    :param closers: Collects the database closer when given.
    :return: The safety repository.
    :raises ConfigurationError: The timezone is unknown on this host.
    """
    database = SqliteDatabase(settings.db_path)
    if closers is not None:
        closers.append(database.close)
    clock = partial(datetime.now, _load_timezone(settings.timezone))
    defaults = SafetyConfig(
        settings.safety_enabled_default,
        settings.safety_threshold_default_c,
        settings.safety_max_attempts_default,
        settings.safety_retry_interval_default_min,
    )
    params = SafetyParams(
        settings.safety_confirm_polls,
        settings.safety_rearm_hysteresis_c,
        settings.min_restart_interval_min,
        poll_interval_s=settings.poll_interval_s,
    )
    chrono_check = None
    if container.chrono is not None:
        chrono_check = ChronoStopMatcher(
            container.chrono.get, settings.safety_chrono_stop_window_min
        )
    return SafetyRepository(
        store=SafetyStore(database),
        stove=SafetyStoveAdapter(
            container.stove,
            container.history,
            timedelta(minutes=settings.min_restart_interval_min),
            clock,
        ),
        events=StoveEventRecorder(container.history),
        lookup=HistoryStopLookup(container.history),
        vacation_active=partial(_vacation_active, container),
        chrono_check=chrono_check,
        defaults=defaults,
        params=params,
        chrono_window_min=settings.safety_chrono_stop_window_min,
        min_restart_interval_min=settings.min_restart_interval_min,
        clock=clock,
    )


def build_container(
    settings: Settings,
    participant_factories: Sequence[Callable[[Container], Suspendable]] = (),
) -> Container:
    """Build the repositories of the common base and of the enabled features.

    The chrono is built only when ``"chrono" in settings.features``. The suspension exists only
    when at least one participant factory is given (the caller passes the enabled features').

    :param settings: Application settings.
    :param participant_factories: One adapter factory per enabled feature that can be suspended.
    :return: Composed container (call ``close`` on shutdown).
    :raises ConfigurationError: Invalid configuration (resources already opened are closed).
    """
    closers: list[Closer] = []
    try:
        history = build_history_repository(settings, closers=closers)
        io = build_register_io(settings)
        if isinstance(io, NavelRegisterIO):
            closers.append(io.close)
        # Reason: late-bound; the container exists only once the stove repository is built.
        stove = build_stove_repository(
            settings,
            history,
            io=io,
            settings_lock=lambda: _settings_lock(container),
            preferences=build_runtime_preferences(settings, closers=closers),
        )
        closers.append(stove.close)
        container = Container(
            stove=stove, history=history, profile=build_stove_profile(settings), closers=closers
        )
        if "chrono" in settings.features:
            container.chrono = build_chrono_repository(
                settings, stove, history, io, is_suspended=partial(_is_suspended, container)
            )
        participants = [factory(container) for factory in participant_factories]
        if participants:
            container.suspension = build_suspension_repository(
                settings, container, participants, closers=closers
            )
        container.safety = build_safety_repository(settings, container, closers=closers)
    except BaseException:
        _run_closers(closers)
        raise
    return container


def build_status_poller(
    settings: Settings, container: Container, stop_event: threading.Event
) -> StatusPoller:
    """Build the background status poller.

    :param settings: Application settings.
    :param container: Composed repositories.
    :param stop_event: Set to stop the poller loop.
    :return: The poller (the caller runs ``run_forever`` in a thread).
    :raises ConfigurationError: The timezone is unknown on this host.
    """
    suspension = container.suspension
    safety = container.safety
    return StatusPoller(
        container.stove,
        container.history,
        interval_s=settings.poll_interval_s,
        sample_interval_s=settings.sample_interval_s,
        retention_days=settings.history_retention_days,
        clock=partial(datetime.now, _load_timezone(settings.timezone)),
        stop_event=stop_event,
        on_poll=None if suspension is None else suspension.check,
        status_hooks=() if safety is None else (("safety", safety.check),),
        alert_sources=() if safety is None else (safety.active_alert,),
    )


def _system_clock() -> datetime:
    return datetime.now().astimezone()


def _load_timezone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError:
        raise ConfigurationError(
            f"Timezone {name!r} not found; on Windows install the tzdata package"
        ) from None
