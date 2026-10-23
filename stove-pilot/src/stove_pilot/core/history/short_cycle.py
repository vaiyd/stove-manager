"""Short-cycle alert: the stove lit again too soon after the previous start.

The manual prefers the OFF stop (P1 modulation) when the stove lights and stops
more than once per 90 minutes. This module decides that advice from temperature
samples only. It never starts, stops, or changes the stove or the chrono.

Samples and precision
---------------------
The poller stores one ``temperature_samples`` row (``at``, ``state``) when its
clock has moved by at least ``sample_interval_s`` (default 60 s). Polls run
every ``poll_interval_s`` (default 30 s); a failed poll stores nothing. The
stored instant is the read's ``polled_at``.

A start instant is the first stored sample of the new state, so while polling
is healthy it is late by at most one sample interval (default 60 s). A light
that returns to an off-like state before the next sample is invisible. Worst
case while healthy: a cycle shorter than ``sample_interval_s`` (default 60 s)
is missed. Host ``stove_events`` are not used. Chrono starts and stops are
executed by the stove, and a ``state_changed`` row exists only for a transition
this process observed.

States
------
A START is a transition into an on-like state from an off-like state between
two consecutive samples, accepted only when their gap is at most
``SHORT_CYCLE_MAX_GAP`` (15 min; a pair exactly that far apart still counts).

- On-like: ``IGNITION``, ``RUNNING``, ``CLEANING``. Ignition phases are all
  ``IGNITION``, so they are one start. ``IGNITION`` then ``RUNNING`` is not a
  new start. ``CLEANING`` is a burn-time brazier clean.
- Off-like: ``OFF``, ``SHUTDOWN`` (final cleaning / cooling), ``STANDBY``
  (ENERGY SAVING flame-out; the next ignition is a new light), ``ALARM``.
- ``UNKNOWN`` is neither. A hop through it is not a start.

Failed ignition counts. The app never retries, but the samples can show
``IGNITION``, then an off-like state, then ``IGNITION`` again. That second
entry is a start. The rule cannot see why the flame went out, so a manual
restart counts the same way.

Rule
----
- Raise when a start is strictly sooner than ``min_interval`` (default 90 min)
  after the previous accepted start. Exactly ``min_interval`` is compliant.
- Clear when the newest accepted start has no accepted start in the open
  interval ``(start - min_interval, start)`` and the samples cover that
  interval with no hole larger than ``max_gap``. The dashboard drops the
  alert. The history row stays, occurrences included.
- No previous start (the first start in the samples, including the first
  sample after the app begins, with nothing off-like before it) does not raise.
- No accepted start in the given samples keeps ``held`` (the persisted flag).
  Without a new start the alert stays raised across restarts.
- A pair farther apart than ``max_gap`` is not a start: a multi-hour polling
  hole must not invent a light. Such a hole inside the lookback also blocks a
  clear (a start could sit in it) but does not block a raise already proven by
  two visible starts. The worst accepted timestamp error is ``max_gap`` (15 min);
  with the default 60 s samples it is one sample interval.

The caller passes every sample in ``[at - min_interval - max_gap, at]``. A
configured sample interval above 15 min makes ordinary pairs look like holes,
so no start is detected. The default interval is 60 s.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from operator import attrgetter
from typing import Final

from stove_pilot.core.stove.models import StoveState

# Reason: five minutes would treat a slowed sample interval as a hole.
# Fifteen minutes still rejects hour-long outages. Healthy samples are 60 s apart.
SHORT_CYCLE_MAX_GAP: Final = timedelta(minutes=15)

# Reason: CLEANING stays on the burn; ignition phases share IGNITION.
_ON_LIKE: Final = frozenset({StoveState.IGNITION, StoveState.RUNNING, StoveState.CLEANING})
# Reason: STANDBY is the energy-saving flame-out; SHUTDOWN is the final cooling.
_OFF_LIKE: Final = frozenset(
    {StoveState.OFF, StoveState.SHUTDOWN, StoveState.STANDBY, StoveState.ALARM}
)


@dataclass(frozen=True, slots=True)
class CycleSample:
    """One ordered reading the rule can see (``at`` is timezone-aware)."""

    at: datetime
    state: StoveState


@dataclass(frozen=True, slots=True)
class ShortCycleDecision:
    """Whether ``SHORT_CYCLE`` should be shown after one sample window.

    ``reliable`` is false when the window does not prove a change, so ``raised``
    repeats ``held``. ``last_start_at`` is the newest accepted start, if any.
    """

    raised: bool
    reliable: bool
    last_start_at: datetime | None = None


def evaluate_short_cycle(
    samples: Sequence[CycleSample],
    *,
    min_interval: timedelta,
    max_gap: timedelta,
    held: bool,
) -> ShortCycleDecision:
    """Decide the short-cycle flag from one lookback window.

    :param samples: Samples covering ``[at - min_interval - max_gap, at]``.
    :param min_interval: Minimum compliant gap between two starts (positive).
    :param max_gap: Largest gap that still brackets a start (not negative).
    :param held: Persisted flag, kept when the window cannot prove a change.
    :return: The flag to sight on this poll.
    :raises ValueError: A duration is out of range or a timestamp is naive.
    """
    if min_interval <= timedelta(0):
        raise ValueError("min_interval must be positive")
    if max_gap < timedelta(0):
        raise ValueError("max_gap must not be negative")
    _require_aware(samples)
    ordered = tuple(sorted(samples, key=attrgetter("at")))
    starts = _start_instants(ordered, max_gap)
    if not starts:
        return ShortCycleDecision(held, False, None)
    last = starts[-1]
    previous = starts[-2] if len(starts) >= 2 else None
    if previous is not None and last - previous < min_interval:
        return ShortCycleDecision(True, True, last)
    if _clear_is_reliable(ordered, last, min_interval, max_gap):
        return ShortCycleDecision(False, True, last)
    return ShortCycleDecision(held, False, last)


def _require_aware(samples: Sequence[CycleSample]) -> None:
    """Reject a naive timestamp.

    :param samples: Samples about to be ordered.
    :raises ValueError: A sample instant has no timezone.
    """
    for sample in samples:
        at = sample.at
        if at.tzinfo is None or at.utcoffset() is None:
            raise ValueError("Sample timestamps must be timezone-aware")


def _start_instants(samples: Sequence[CycleSample], max_gap: timedelta) -> tuple[datetime, ...]:
    """Return accepted start instants, oldest first.

    :param samples: Samples ordered by time.
    :param max_gap: Largest gap that still brackets a start.
    :return: Instant of each accepted start (the first sample of the new state).
    """
    found: list[datetime] = []
    previous: CycleSample | None = None
    for sample in samples:
        if previous is not None and _is_start(previous, sample, max_gap):
            found.append(sample.at)
        previous = sample
    return tuple(found)


def _is_start(previous: CycleSample, sample: CycleSample, max_gap: timedelta) -> bool:
    """Tell whether two consecutive samples are an accepted start.

    :param previous: Earlier sample.
    :param sample: Later sample (its instant is the start when this is true).
    :param max_gap: Largest gap that still brackets a start.
    :return: ``True`` for an on-like sample after an off-like one inside the gap.
    """
    gap = sample.at - previous.at
    if gap < timedelta(0) or gap > max_gap:
        return False
    return previous.state in _OFF_LIKE and sample.state in _ON_LIKE


def _clear_is_reliable(
    samples: Sequence[CycleSample],
    last: datetime,
    min_interval: timedelta,
    max_gap: timedelta,
) -> bool:
    """Tell whether a compliant decision is proven up to ``last``.

    :param samples: Samples ordered by time.
    :param last: Newest accepted start.
    :param min_interval: Lookback that must be covered.
    :param max_gap: A larger hole can hide a start.
    :return: ``True`` when no hole in ``(last - min_interval, last)`` can hide a start.
    """
    horizon = last - min_interval
    if not samples or samples[0].at > horizon:
        return False
    previous = samples[0]
    for sample in samples[1:]:
        if sample.at > last:
            break
        gap = sample.at - previous.at
        if gap > max_gap and previous.at < last and sample.at > horizon:
            return False
        previous = sample
    return True
