"""Application facade of the native chrono: cached reads, guarded saves, suspend and resume."""

import logging
import threading
from collections.abc import Callable, Mapping
from dataclasses import replace
from datetime import datetime, tzinfo
from typing import Any, Final

from stove_pilot.common.exceptions import (
    ConfigurationError,
    FeatureConflictError,
    GatewayProtocolError,
    ValidationFailedError,
    VersionConflictError,
)
from stove_pilot.core.chrono.changes import enabled_kinds, field_map, newly_enabled
from stove_pilot.core.chrono.layout import BlockLayout, ChronoLayout, decode_config, encode_config
from stove_pilot.core.chrono.models import (
    ChronoBlockKind,
    ChronoConfig,
    ChronoIssue,
    ChronoValidation,
    ProgrammingSummary,
)
from stove_pilot.core.chrono.service import ChronoGateway
from stove_pilot.core.chrono.summary import compute_summary
from stove_pilot.core.chrono.validation import validate_chrono
from stove_pilot.core.stove.events import StoveEventRecorder
from stove_pilot.core.stove.models import EventSource, StoveProfile
from stove_pilot.core.stove.ports import HistoryRecorder

_LOGGER = logging.getLogger(__name__)

SUSPENDED_REASON: Final = "programming_suspended"
_ENABLED_BLOCKS_KEY: Final = "enabled_blocks"


class ChronoRepository:
    """Single entry point to read and change the native chrono (EEPROM is the source of truth).

    All chrono I/O is serialized by one reentrant lock. Time comes from the injected clock.
    """

    def __init__(
        self,
        gateway: ChronoGateway,
        layout: ChronoLayout,
        profile: StoveProfile,
        history: HistoryRecorder,
        clock: Callable[[], datetime],
        tz: tzinfo,
        clock_sync: Callable[[], object],
        *,
        is_suspended: Callable[[], bool] = lambda: False,
        allow_unverified: bool = False,
        cache_ttl_s: int = 300,
    ) -> None:
        """Bind the collaborators and limits.

        :param gateway: Chrono EEPROM gateway.
        :param layout: Chrono layout.
        :param profile: Stove profile (setpoint and power ranges).
        :param history: History recorder (events).
        :param clock: Returns the aware server time.
        :param tz: Local timezone used to present times.
        :param clock_sync: Syncs the stove clock when drifted (common base).
        :param is_suspended: Tells whether programming is suspended.
        :param allow_unverified: Allow writes to unverified layout fields (simulator).
        :param cache_ttl_s: Default lifetime of the cached configuration in seconds.
        """
        # Reason: the repository flag can only narrow the layout flag, never widen it.
        self._allow_unverified = allow_unverified and layout.allow_unverified
        self._layout = replace(layout, allow_unverified=self._allow_unverified)
        self._gateway = gateway
        self._events = StoveEventRecorder(history)
        self._clock = clock
        self._tz = tz
        self._clock_sync = clock_sync
        self._is_suspended = is_suspended
        self._cache_ttl_s = cache_ttl_s
        self._setpoint_range = self._range(profile, "setpoint")
        self._power_range = self._range(profile, "power_level")
        self._blocks = {block.kind: block for block in layout.blocks}
        self._fields = field_map(layout)
        self._lock = threading.RLock()
        self._cached: tuple[datetime, ChronoConfig] | None = None

    # -- reads -------------------------------------------------------------------------------

    def get(self, max_age_s: float | None = None) -> ChronoConfig:
        """Return the cached configuration, or read the stove when the cache is too old.

        :param max_age_s: Maximum accepted cache age in seconds (default: the cache TTL).
        :return: The decoded configuration.
        :raises GatewayError: The read failed.
        """
        with self._lock:
            limit = self._cache_ttl_s if max_age_s is None else max_age_s
            if self._cached is not None:
                read_at, config = self._cached
                if 0 <= (self._clock() - read_at).total_seconds() < limit:
                    return config
            started = self._clock()
            _, config = self._read_current()
            self._cached = (started, config)
            return config

    def fingerprint(self, max_age_s: float | None = None) -> str:
        """Return the fingerprint of the (cached) configuration.

        :param max_age_s: Maximum accepted cache age in seconds.
        :return: The fingerprint.
        """
        return self.get(max_age_s).fingerprint

    def is_enabled(self) -> bool:
        """Tell whether any block is enabled (cached).

        :return: ``True`` when at least one block is enabled.
        """
        return self.get().enabled

    def validate(self, config: ChronoConfig) -> ChronoValidation:
        """Validate a configuration against the layout and the stove ranges.

        :param config: Configuration to check.
        :return: Blocking issues and warnings.
        """
        return validate_chrono(config, self._layout, self._setpoint_range, self._power_range)

    def summary(self, now: datetime) -> ProgrammingSummary:
        """Summarize the running and next program (uses the cache).

        :param now: Aware time to evaluate.
        :return: The summary; ``blocked_reason`` is set while programming is suspended.
        :raises GatewayError: The read failed.
        """
        blocked = SUSPENDED_REASON if self._is_suspended() else None
        return compute_summary(self.get(), now.astimezone(self._tz), blocked)

    # -- writes ------------------------------------------------------------------------------

    def save(self, config: ChronoConfig, *, expected_fingerprint: str) -> ChronoConfig:
        """Validate and write a configuration (changed bytes only), then read it back.

        :param config: Configuration to save.
        :param expected_fingerprint: Fingerprint the caller last saw.
        :return: The configuration read back from the stove.
        :raises VersionConflictError: The stove chrono changed since ``expected_fingerprint``.
        :raises ValidationFailedError: The configuration is invalid or cannot be encoded.
        :raises FeatureConflictError: A block would be enabled while programming is suspended.
        :raises GatewayProtocolError: The read-back differs from what was written.
        :raises WriteOutcomeUnknownError: A byte could not be confirmed.
        """
        with self._lock:
            raw, current = self._read_current()
            if current.fingerprint != expected_fingerprint:
                raise VersionConflictError("The chrono changed on the stove since it was read")
            validation = self.validate(config)
            if not validation.ok:
                raise ValidationFailedError(validation.issues)
            if self._is_suspended() and newly_enabled(current, config):
                raise FeatureConflictError(SUSPENDED_REASON)
            changes = {a: v for a, v in self._encode(config).items() if raw[a] != v}
            saved = self._write_verified(changes, raw)
            self._run_clock_sync()
            self._events.record("chrono_saved", EventSource.USER, changed_bytes=len(changes))
            return saved

    def set_block_enabled(self, kind: ChronoBlockKind, enabled: bool) -> ChronoConfig:
        """Enable or disable one block (read-before-write).

        Enabling validates the resulting configuration; disabling only needs a verified layout.

        :param kind: Block to change.
        :param enabled: Wanted state.
        :return: The configuration after the change.
        :raises FeatureConflictError: Enabling while programming is suspended.
        :raises ValidationFailedError: The layout is unverified or the config would be invalid.
        """
        with self._lock:
            if enabled and self._is_suspended():
                raise FeatureConflictError(SUSPENDED_REASON)
            raw, current = self._read_current()
            address = self._block(kind).enable
            if raw[address] == int(enabled):
                return current
            if enabled:
                wanted = self._with_enabled(current, kind)
                validation = self.validate(wanted)
                if not validation.ok:
                    raise ValidationFailedError(validation.issues)
            else:
                self._require_verified([kind])
            changes = {address: int(enabled)}
            saved = self._write_verified(changes, raw)
            self._events.record(
                "chrono_block_toggled", EventSource.USER, block=kind.value, enabled=enabled
            )
            return saved

    def snapshot(self) -> dict[str, Any]:
        """Read the enabled blocks that ``suspend`` would disable (no write).

        :return: Snapshot ``{"enabled_blocks": [...]}`` to pass to ``suspend`` and ``resume``.
        :raises ValidationFailedError: The layout of an enabled block is unverified.
        """
        with self._lock:
            _, current = self._read_current()
            kinds = enabled_kinds(current)
            self._require_verified(kinds)
            return {_ENABLED_BLOCKS_KEY: [kind.value for kind in kinds]}

    def suspend(self, snapshot: Mapping[str, Any]) -> None:
        """Disable the blocks listed in a snapshot, only those currently enabled.

        :param snapshot: Value returned by ``snapshot``.
        :raises ValidationFailedError: Malformed snapshot or unverified layout.
        """
        kinds = self._parse_snapshot(snapshot)
        with self._lock:
            raw, _ = self._read_current()
            targets = [kind for kind in kinds if raw[self._block(kind).enable] != 0]
            self._require_verified(targets)
            changes = {self._block(kind).enable: 0 for kind in targets}
            self._write_verified(changes, raw)
            self._events.record(
                "chrono_suspended",
                EventSource.USER,
                enabled_blocks=[kind.value for kind in kinds],
            )

    def resume(self, snapshot: Mapping[str, Any]) -> None:
        """Re-enable the blocks listed in a snapshot, only those currently disabled.

        :param snapshot: Value returned by ``snapshot``.
        :raises ValidationFailedError: Malformed snapshot or unverified layout.
        """
        kinds = self._parse_snapshot(snapshot)
        with self._lock:
            raw, _ = self._read_current()
            targets = [kind for kind in kinds if raw[self._block(kind).enable] == 0]
            self._require_verified(targets)
            changes = {self._block(kind).enable: 1 for kind in targets}
            self._write_verified(changes, raw)
            self._events.record(
                "chrono_resumed",
                EventSource.USER,
                enabled_blocks=[kind.value for kind in kinds],
                restored=[kind.value for kind in targets],
            )

    # -- internals ---------------------------------------------------------------------------

    @staticmethod
    def _range(profile: StoveProfile, name: str) -> tuple[int, int]:
        """Read an inclusive integer range from a profile register.

        :param profile: Stove profile.
        :param name: Register name (``setpoint`` or ``power_level``).
        :return: ``(min, max)``.
        :raises ConfigurationError: The register has no range.
        """
        register = profile.register(name)
        if register.min_value is None or register.max_value is None:
            raise ConfigurationError(f"Profile register {name!r} has no min/max for the chrono")
        return int(register.min_value), int(register.max_value)

    def _block(self, kind: ChronoBlockKind) -> BlockLayout:
        """Return a block layout.

        :param kind: Block kind.
        :return: The block layout.
        :raises ValidationFailedError: The layout has no such block.
        """
        block = self._blocks.get(kind)
        if block is None:
            raise ValidationFailedError([f"unknown chrono block {kind.value!r}"])
        return block

    def _parse_snapshot(self, snapshot: Mapping[str, Any]) -> list[ChronoBlockKind]:
        """Read the block kinds of a suspend snapshot.

        :param snapshot: Value returned by ``snapshot``.
        :return: Block kinds, without duplicates.
        :raises ValidationFailedError: Malformed snapshot or unknown block.
        """
        names = snapshot.get(_ENABLED_BLOCKS_KEY)
        if not isinstance(names, list) or not all(isinstance(name, str) for name in names):
            raise ValidationFailedError([f"snapshot needs a list {_ENABLED_BLOCKS_KEY!r}"])
        try:
            kinds = list(dict.fromkeys(ChronoBlockKind(name) for name in names))
        except ValueError as exc:
            raise ValidationFailedError([str(exc)]) from None
        for kind in kinds:
            self._block(kind)
        return kinds

    def _read_current(self) -> tuple[dict[int, int], ChronoConfig]:
        """Read the stove now (never the cache).

        :return: Raw bytes and the decoded configuration.
        :raises GatewayProtocolError: The bytes are not a valid chrono.
        """
        raw = self._gateway.read_raw()
        return raw, self._decode(raw)

    def _decode(self, raw: Mapping[int, int]) -> ChronoConfig:
        """Decode raw bytes, reporting invalid content as a protocol error.

        :param raw: EEPROM bytes keyed by address.
        :return: The configuration.
        :raises GatewayProtocolError: A byte is not valid for its field.
        """
        try:
            return decode_config(self._layout, raw)
        except ValueError as exc:
            raise GatewayProtocolError(f"The stove returned invalid chrono bytes: {exc}") from None

    def _encode(self, config: ChronoConfig) -> dict[int, int]:
        """Encode a configuration, reporting unstorable values as validation errors.

        :param config: Configuration to encode.
        :return: Bytes keyed by address.
        :raises ValidationFailedError: The configuration cannot be stored.
        """
        try:
            return encode_config(self._layout, config)
        except ValueError as exc:
            raise ValidationFailedError([str(exc)]) from None

    @staticmethod
    def _with_enabled(config: ChronoConfig, kind: ChronoBlockKind) -> ChronoConfig:
        """Return a copy of a configuration with one block enabled.

        :param config: Base configuration.
        :param kind: Block to enable.
        :return: The modified copy.
        """
        blocks = tuple(
            replace(block, enabled=True) if block.kind == kind else block for block in config.blocks
        )
        return replace(config, blocks=blocks)

    def _require_verified(self, kinds: list[ChronoBlockKind]) -> None:
        """Refuse writes to unverified blocks unless unverified writes are allowed.

        :param kinds: Blocks about to be written.
        :raises ValidationFailedError: An unverified block is targeted.
        """
        if self._allow_unverified:
            return
        issues = [
            ChronoIssue(
                "unverified_layout", kind, None, f"{kind.value} layout is not verified"
            )
            for kind in kinds
            if not self._block(kind).verified
        ]
        if issues:
            raise ValidationFailedError(issues)

    def _warn_unverified(self, changes: Mapping[int, int]) -> None:
        """Log one WARNING per byte about to be written to an unverified block.

        :param changes: New byte values keyed by address.
        """
        for address in sorted(changes):
            field = self._fields[address]
            if not field.verified:
                _LOGGER.warning(
                    "Writing unverified chrono field %s.%s (simulator mode)",
                    field.kind.value,
                    field.name,
                )

    def _write_verified(self, changes: Mapping[int, int], before: dict[int, int]) -> ChronoConfig:
        """Write bytes, read the chrono back and compare; the cache is dropped around the write.

        :param changes: New byte values keyed by address (may be empty).
        :param before: Raw bytes read before the change.
        :return: The configuration decoded from the read-back.
        :raises GatewayProtocolError: A byte differs after the write.
        """
        if not changes:
            return self._decode(before)
        self._warn_unverified(changes)
        self._cached = None
        try:
            self._gateway.write_bytes(changes)
            after = self._gateway.read_raw()
        finally:
            self._cached = None
        wrong = [a for a, value in changes.items() if after[a] != value]
        if wrong:
            where = ", ".join(f"0x{a:02X}" for a in sorted(wrong))
            raise GatewayProtocolError(f"Chrono read-back differs from the written bytes at {where}")
        return self._decode(after)

    def _run_clock_sync(self) -> None:
        """Call the clock sync; a failure is logged and never fails the save."""
        try:
            self._clock_sync()
        except Exception:  # Reason: the chrono bytes are already written; the sync is best effort.
            _LOGGER.warning("Clock sync after the chrono save failed", exc_info=True)
