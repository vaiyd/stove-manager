"""Background status poller: cache refresh, samples, deferred commands, clock sync, retention."""

import logging
import threading
from collections.abc import Callable, Sequence
from datetime import datetime, timedelta
from typing import Final

from stove_pilot.common.exceptions import GatewayError
from stove_pilot.core.stove.messages import DisplayMessage
from stove_pilot.core.stove.models import StoveStatus
from stove_pilot.core.stove.ports import HistoryRecorder
from stove_pilot.core.stove.repository import StoveRepository

_LOGGER = logging.getLogger(__name__)

# Reason: retention only needs to run now and then; hourly keeps the cost negligible.
_PURGE_INTERVAL: Final = timedelta(hours=1)


class StatusPoller:
    """Polls the stove at a fixed interval; one failing step never stops the loop."""

    def __init__(
        self,
        repository: StoveRepository,
        history: HistoryRecorder,
        interval_s: float,
        sample_interval_s: float,
        retention_days: int,
        clock: Callable[[], datetime],
        stop_event: threading.Event,
        on_poll: Callable[[], None] | None = None,
        status_hooks: Sequence[tuple[str, Callable[[StoveStatus, datetime], None]]] = (),
        alert_sources: Sequence[Callable[[], DisplayMessage | None]] = (),
    ) -> None:
        """Bind the collaborators and intervals.

        :param repository: Stove repository.
        :param history: History recorder (samples, alerts and retention).
        :param interval_s: Seconds between two polls.
        :param sample_interval_s: Minimum seconds between two stored samples.
        :param retention_days: Days of history to keep; 0 keeps everything (no purge).
        :param clock: Returns the aware server time.
        :param stop_event: Set to stop ``run_forever``.
        :param on_poll: Optional hook called after every poll, even a failed one (failures logged).
        :param status_hooks: ``(label, hook)`` pairs run after pending commands on a fresh status.
        :param alert_sources: Extra alert providers merged into the same ``record_alerts`` call.
        """
        self._repository = repository
        self._history = history
        self._interval_s = interval_s
        self._sample_interval = timedelta(seconds=sample_interval_s)
        self._retention_days = retention_days
        self._clock = clock
        self._stop_event = stop_event
        self._on_poll = on_poll
        self._status_hooks = tuple(status_hooks)
        self._alert_sources = tuple(alert_sources)
        self._last_sample_at: datetime | None = None
        self._last_purge_at: datetime | None = None
        self.last_poll_at: datetime | None = None

    def run_once(self) -> None:
        """Poll once: refresh, sample, record alerts, apply pending commands, hooks, sync, purge.

        When the refresh fails, the steps that need a fresh status (sample, alerts, pending
        commands, status hooks, clock sync) are skipped; the purge and the hook (suspension
        auto-resume and override check) read nothing from the status, so they still run.

        :raises GatewayError: The status refresh failed (raised after the independent steps).
        """
        failure: GatewayError | None = None
        status: StoveStatus | None = None
        try:
            status = self._repository.refresh()
        except GatewayError as exc:
            failure = exc
        now = self._clock()
        if status is not None:
            self.last_poll_at = now
            self._step("sample", self._record_sample, status, now)
            self._step("alerts", self._record_alerts, status)
            self._step("pending", self._repository.process_pending)
            for label, hook in self._status_hooks:
                self._step(label, hook, status, now)
            self._step("clock sync", self._repository.auto_sync_clock)
        else:
            _LOGGER.info(
                "Sample, alerts, pending commands, status hooks and clock sync skipped: "
                "no fresh status"
            )
        self._step("purge", self._purge, now)
        if self._on_poll is not None:
            self._step("on_poll hook", self._on_poll)
        if failure is not None:
            raise failure

    def run_forever(self) -> None:
        """Poll until the stop event is set; failures are logged and the loop continues."""
        _LOGGER.info("Status poller started (interval %ss)", self._interval_s)
        while True:
            try:
                self.run_once()
            except GatewayError as exc:
                _LOGGER.warning("Poll failed (%s); the status will go stale", type(exc).__name__)
            except Exception:
                _LOGGER.error("Unexpected poll failure", exc_info=True)
            if self._stop_event.wait(self._interval_s):
                break
        _LOGGER.info("Status poller stopped")

    def _record_sample(self, status: StoveStatus, now: datetime) -> None:
        if self._last_sample_at is not None and now - self._last_sample_at < self._sample_interval:
            return
        self._history.record_sample(status)
        self._last_sample_at = now

    def _record_alerts(self, status: StoveStatus) -> None:
        # Reason: an absence between two polls must be seen, so this is not on the sample interval.
        # The recorder sights SHORT_CYCLE from the samples stored above; a failed read keeps it.
        message = status.display_message
        messages: list[DisplayMessage] = [] if message is None else [message]
        for source in self._alert_sources:
            extra = self._alert_from_source(source)
            if extra is not None:
                messages.append(extra)
        self._history.record_alerts(tuple(messages), at=status.polled_at)

    @staticmethod
    def _alert_from_source(source: Callable[[], DisplayMessage | None]) -> DisplayMessage | None:
        """Read one extra alert. A failure is logged by label and skipped.

        :param source: Provider called once per fresh poll.
        :return: The message, or ``None`` when absent or the provider failed.
        """
        try:
            return source()
        except Exception:
            label = getattr(source, "__name__", "") or "alert-source"
            _LOGGER.error("Alert source %s failed", label, exc_info=True)
            return None

    def _purge(self, now: datetime) -> None:
        if self._retention_days == 0:
            return
        if self._last_purge_at is not None and now - self._last_purge_at < _PURGE_INTERVAL:
            return
        self._last_purge_at = now
        self._history.purge(self._retention_days)

    @staticmethod
    def _step(label: str, action: Callable[..., object], *args: object) -> None:
        """Run one poll step; its failure is logged and never raised.

        :param label: Step name for the log.
        :param action: Step callable.
        :param args: Arguments of the callable.
        """
        try:
            action(*args)
        except GatewayError as exc:
            _LOGGER.warning("Poll step %s failed (%s)", label, type(exc).__name__)
        except Exception:
            _LOGGER.error("Poll step %s failed unexpectedly", label, exc_info=True)
