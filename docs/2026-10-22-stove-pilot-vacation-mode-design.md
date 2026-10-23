# Stove Pilot - Vacation mode (design addendum)

- Status: Accepted (user-approved 2026-10-22). Amendment 2 (2026-10-22, user decision after the final
  review): write-ahead journal with automatic rollback for both suspension kinds (sections 3.1 to 3.5, 6, 7,
  9, tasks V6 and V7); a vacation activation during SHUTDOWN/ALARM and the fresh read at activation are
  specified in 3.2 (task F1).
- Extends: `2026-10-21-stove-pilot-design.md` section 6.5 "Suspend all programming" (rev 5)
- Scope: common base (`core/suspension`, `core/stove`), `/api/v1/programming`, dashboard UI
- Must land before UI split Task U1 (the API contract baseline), because it adds API fields

## 1. Goal

One dashboard action, "Mode vacances", that:

1. suspends every programming feature (same mechanism as section 6.5),
2. sets the stove setpoint to a frost-protection value (default 7 C),
3. makes sure the stove really behaves as a frost guard (ENERGY SAVING STOP on, stove started),
4. stores every value it changed, and restores them on return (manual, or automatic at a return date).

The existing "Suspendre les programmations" action stays unchanged (it never touches the setpoint).

## 2. Decisions

| Topic | Decision |
| --- | --- |
| Relation to suspension | Separate dashboard button; vacation is a **kind** of suspension (one state, one row, one atomic operation). |
| ENERGY SAVING STOP OFF | Enabled automatically at +2 C for the vacation, original value stored and restored. Unmapped or not writable: activation refused. |
| ENERGY SAVING START | Never written (register not verified for writes). |
| Power level | Never written. |
| Setpoint / ENERGY SAVING changed from the app during vacation | Refused: `409 feature_conflict` reason `vacation_active`. |
| Setpoint changed on the stove panel during vacation | Early return: everything is restored except the setpoint, which keeps the panel value. |
| Chrono block re-enabled on the panel during vacation | Early return: everything is restored (setpoint included). |
| Return date | Optional (manual end only when absent). |

Alternatives considered: a separate `core/vacation` feature orchestrating suspension and stove writes
(rejected: two states to keep consistent, second table, second auto-resume), and the setpoint as an
extra `Suspendable` participant (rejected: `is_active()` has no meaning for a setpoint, the override
check would fire wrongly, and a plain suspension would also change the setpoint).

## 3. Domain (`core/suspension`)

### 3.1 State

`SuspensionState` gains three fields (defaults keep the plain suspension unchanged):

- `kind: Literal["programming", "vacation"] = "programming"`
- `frost_setpoint_c: float | None = None`
- `stove_snapshot: dict[str, Any] = {}`, for vacation:
  `{"setpoint_c": 20.0, "energy_stop_offset_c": null, "energy_stop_changed": true}`
  (`energy_stop_offset_c` is the original STOP value, `null` = OFF; `energy_stop_changed` tells
  whether the vacation wrote it, so resume restores it only in that case).

Amendment 2 (rev: write-ahead journal, section 3.5) adds a fourth field, valid for both kinds:

- `phase: Literal["activating", "active", "restoring"] = "active"` (only meaningful while suspended).

Persistence: `SuspensionStore` schema version 3. V1 shipped schema v2 (applied to existing databases,
including the dev database), and `ALTER TABLE ADD COLUMN` is not repeatable, so the store switches from
`SqliteDatabase.migrate` (whole list re-run) to stepwise `SqliteDatabase.upgrade` (each step applied once, as
`core/history` does). Steps: (1) the v1 `CREATE TABLE IF NOT EXISTS`; (2) the three v2 statements; (3) the v3
statement. A fresh, a v1, a v2 database all end up correct; existing rows get `phase = 'active'`:

```sql
-- step 2 (v2)
ALTER TABLE programming_suspension ADD COLUMN kind TEXT NOT NULL DEFAULT 'programming';
ALTER TABLE programming_suspension ADD COLUMN frost_setpoint_c REAL;
ALTER TABLE programming_suspension ADD COLUMN stove_snapshot_json TEXT NOT NULL DEFAULT '{}';
-- step 3 (v3)
ALTER TABLE programming_suspension ADD COLUMN phase TEXT NOT NULL DEFAULT 'active';
```

`load`/`save` read and write the new columns; an unreadable row (including an unknown `kind` or `phase`)
still raises `ConfigurationError`.

### 3.2 `SuspensionRepository.start_vacation(*, until: datetime | None) -> SuspendOutcome`

Under the repository lock, following the write-ahead journal of section 3.5 (amendment 2 reorders the
original steps; the plain `suspend` follows the same order):

1. Already suspended (either kind, any phase): `FeatureConflictError("already_suspended")`.
2. Validate `until` (same rule as `suspend`).
3. Capture every "before" value, writing nothing: a FORCED FRESH stove read (setpoint and the ENERGY SAVING
   registers, slow group included; a read failure aborts here, nothing changed; a stale cache is never
   used). The STOP register is not writable (or no ENERGY SAVING register is mapped):
   `FeatureConflictError("energy_saving_unmapped")`; `energy_saving` is `None` while STOP is writable means
   the slow read failed: `GatewayError`, nothing changed. Participant snapshots through `Suspendable.snapshot()`
   (section 3.5). Previous setpoint and original STOP value into `stove_snapshot`.
4. Persist the state with `phase="activating"` (`kind="vacation"`, `thermostat_mode=True`, `until`,
   participant snapshots, `stove_snapshot`, `frost_setpoint_c`). If the save fails, nothing was written: raise.
5. Apply: suspend the participants from their snapshots, write the setpoint to
   `settings.vacation_setpoint_c`, then, only when STOP is OFF, write STOP = 2. Both stove writes go through
   `StoveRepository` with `bypass_lock=True` (section 4) and `source=EventSource.USER`; STOP uses the STOP-only
   `set_energy_stop` (never START).
6. Persist `phase="active"`.
7. A failure in steps 5 or 6 with the process alive: compensate from the persisted snapshots (same order and
   rules as the restore of section 3.3, read-before-write, idempotent), then clear the state, then re-raise.
   If the compensation also fails: keep the persisted `phase="activating"`, log, re-raise; `check()` finishes
   the rollback (section 3.4).
8. Record `vacation_started` (`until`, `frost_setpoint_c`, `previous_setpoint_c`,
   `energy_stop_enabled`, `participants`).
9. Thermostat step, vacation path (the plain suspension thermostat is unchanged): the start is required,
   since an OFF stove never restarts by itself and there would be no frost protection. State OFF or SHUTDOWN
   (the start guard defers a start during SHUTDOWN): call `start`, all guards apply. A running or igniting
   stove: nothing to do. Any other state where a start cannot be requested (ALARM, UNKNOWN, ...): no start is
   attempted and the outcome carries the warning `vacation_stove_not_started:<state>`. In a warm house the
   start costs one ignition, then the stove goes to ENERGY SAVING standby.

### 3.3 End of vacation

`resume()` (manual) and `check()` at `until` (automatic) handle both kinds. For `kind="vacation"`
the restore order is: setpoint, then STOP (only if `energy_stop_changed`), then participants. Every
write is read-before-write, so a retry is safe. Amendment 2: the repository first persists
`phase="restoring"` (before the first restore write, in any phase it is called from, including a manual
`resume()` of an interrupted activation); the state is cleared only once every step succeeded.
A `GatewayError` keeps the state (phase `restoring`) and the poller retries on the next `check()`. Resume
never starts or stops the stove (unchanged rule). Event: `vacation_ended` (`reason`: absent for a manual
end, `until` for the automatic one, `recovered` when finished by the recovery of 3.4; `participants`).
The plain suspension follows the same phase handling (`programming_resumed`).

### 3.4 `check()` per phase

`check()` runs on every poll (so also right after a restart) under the repository lock. It can never
observe a live activation, because `suspend` and `start_vacation` hold the same lock until the state is
`active`.

- `phase="activating"` (interrupted activation, either kind): roll back from the persisted snapshots (the
  restore order of 3.3; the plain suspension only restores participants), clear the state, record
  `suspension_rolled_back` (`suspension_kind` because `kind` is the event-kind parameter of the history
  recorder, `reason="interrupted_activation"`, `participants`, source SYSTEM). A `GatewayError`
  keeps the state and the next poll retries.
- `phase="restoring"`: continue the restore of 3.3, clear the state, record the usual end event
  (`programming_resumed` or `vacation_ended`) with `reason="recovered"`.
- `phase="active"`: the early-return detection below, and only here.

Early return detection (`phase="active"`, vacation only). Evaluated in this order on each poll, using the
poller's cached status (skipped when unavailable):

1. `until` reached: normal end (3.3).
2. `status.setpoint_c != frost_setpoint_c`: someone changed it on the panel. Restore STOP (if changed)
   and participants, **keep** the current setpoint, clear the state. Event `vacation_overridden`
   (`trigger="setpoint"`, `setpoint_c`).
3. A participant became active (chrono block re-enabled on the panel): restore setpoint, STOP (if
   changed) and the other participants, clear the state. Event `vacation_overridden`
   (`trigger="participant"`, `participants`).

The plain suspension keeps its current override behavior (`suspension_overridden`, no restore) in
`phase="active"`.

### 3.5 Write-ahead journal (amendment 2, both kinds)

Decision: the persisted state is a write-ahead journal that always rolls back (never forward). If the
process dies or the gateway drops between the stove writes and the final save, the persisted
`phase="activating"` row plus its snapshots are enough to restore the original values, without any
in-memory state. Alternatives considered: persist after the writes as originally specified (leaves a half
vacation with no record after a crash), and roll forward (needs the intent to be replayable and keeps the
stove away from its original state longer).

- `Suspendable` gains a read-only `snapshot() -> dict[str, Any]` returning what `suspend` would switch off
  (chrono: the enabled blocks), with no write. `suspend(snapshot)` then switches off exactly that snapshot
  (read-before-write, idempotent, no decision of its own); `resume(snapshot)` is unchanged.
- All "before" values are captured first (3.2 step 3), persisted (`activating`), applied, then confirmed
  (`active`). Phases: `activating` (rollback pending), `active` (steady state), `restoring` (restore under way,
  persisted before the first restore write).
- The settings lock `vacation_active` (section 4) applies in all three phases of a vacation.

## 4. Settings lock (`core/stove`)

`StoveRepository` receives, through composition, `settings_lock: Callable[[], str | None]`
(default `lambda: None`), mirroring the chrono `is_suspended` injection. `core/stove` never imports
`core/suspension`.

- Composition binds it to a lock-free read of the suspension state: `"vacation_active"` when
  `kind == "vacation"`, else `None` (same lock-free rule as `is_suspended`, because it is called
  while other locks are held).
- `set_setpoint` and `set_energy_saving` gain `bypass_lock: bool = False` (keyword-only). When the
  lock returns a reason and `bypass_lock` is false: `FeatureConflictError(reason)`, nothing written.
- Only `core/suspension` passes `bypass_lock=True`.

## 5. Configuration

`common/settings.py`: `vacation_setpoint_c: float = Field(default=7.0, ge=7, le=12)`
(env `STOVE_PILOT_VACATION_SETPOINT_C`), documented in `.env.example`. The profile bounds of the
setpoint register (7..30 on the MAIRA) still apply at write time.

## 6. API (`/api/v1`, additive only)

| Method | Path | Contract |
| --- | --- | --- |
| POST | `/programming/vacation` `{until?: datetime}` | `SuspendOutcomeDto {state, start_outcome \| null, warnings[]}`. `409 already_suspended`, `409 energy_saving_unmapped`, `422` invalid `until` |
| POST | `/programming/resume` | Unchanged; ends either kind |
| GET | `/programming` | Adds `kind`, `frost_setpoint_c`, `previous_setpoint_c`, and (amendment 2) `phase` (`"activating"` \| `"active"` \| `"restoring"`, default `"active"`) |
| GET | `/summary` | `suspension` block adds the same four fields |
| PUT | `/stove/setpoint`, `/stove/settings/energy-saving` | New `409 feature_conflict` reason `vacation_active` |

## 7. Dashboard UI

- `partials/controls.html`: "Mode vacances" button next to "Suspendre les programmations". While
  suspended, both are replaced by "Reprendre" (`kind="programming"`) or "Terminer le mode vacances"
  (`kind="vacation"`), both posting to the existing resume action.
- `partials/vacation_modal.html` (new): optional return date (`datetime-local`), recap ("Consigne
  7 C (20 C restauree au retour), programmations suspendues, ENERGY SAVING STOP active a +2 C" when
  it was OFF), warnings: check the pellet hopper autonomy; alarms are not notified remotely.
- `POST /actions/vacation` (CSRF-protected, in `routes/commands.py`), `StovePilotApiClient.vacation`,
  toasts reuse `suspend_toasts`.
- Glance and widget: `alert-info` banner "Mode vacances jusqu'au 02/11 - hors-gel 7 C (consigne
  20 C au retour)"; programming lines greyed as for the suspension.
- While in vacation: the setpoint stepper and the ENERGY SAVING controls (settings page) are
  disabled, with the hint "Terminez le mode vacances pour modifier".
- `fr.json`: keys `vacation.*`, `problem.feature_conflict.vacation_active`,
  `problem.feature_conflict.energy_saving_unmapped`, `event.kind.vacation_started`,
  `event.kind.vacation_ended`, `event.kind.vacation_overridden`, history texts, and the warning text of
  `vacation_stove_not_started:<state>`.
- Amendment 2: while `phase` is `activating` or `restoring` (either kind) the glance/widget banner reads
  "Operation interrompue - restauration en cours" (fr.json key) instead of the normal banner, and the
  suspend/vacation buttons stay hidden (the resume button follows the kind as before). Key
  `event.kind.suspension_rolled_back` for the history text.

## 8. Failure modes and limits

- Host crash or restart during an activation or a restore: the persisted `phase` (section 3.5) lets the
  first `check()` after the restart finish the rollback or the restore from the snapshots.
- Known limits (final reviews, backlog): a crash between the `active` save and the thermostat start leaves
  an active vacation with the stove OFF and nothing restarts it (a poller start would conflict with
  "never auto-retry an ignition"; the attended check and the stove-off hint are the mitigation); a journaled
  chrono snapshot that later fails validation makes `check()` and `resume()` fail and keeps the settings
  lock on until the database row is fixed by hand (same for a captured setpoint outside the profile bounds);
  a start deferred during SHUTDOWN is held in memory only (lost on a restart or after 45 minutes) and
  survives the end of the vacation if it ends within the cooldown; recovery after a long outage overwrites a
  setpoint changed on the panel meanwhile; without the chrono feature no `SuspensionRepository` is built,
  so an active vacation would be orphaned.
- Host off at `until`: the restore happens at the first poll after the next start (state persisted).
- Stove unreachable at `until`: state kept, retried on every poll; the banner stays visible.
- No remote alarm notification (pellets out, failed ignition) during the absence: out of scope,
  listed as a backlog item (notification webhook).
- The restart threshold below 7 C depends on the ENERGY SAVING START offset set on the panel.

## 9. Verification (manual only)

The project has **no automated tests** (plan Global Constraints: no `tests/` folder, no pytest).
Every step ends with a manual check on `STOVE_PILOT_GATEWAY=simulator` (curl, browser, or a
throwaway script in `%TEMP%`, deleted afterwards). Agents never drive the real stove.

- Store (final form, v3): copy the current `data/stove-pilot.sqlite` (already at `suspension = 2`) to
  `%TEMP%`, start the API on the copy, check that `schema_versions` shows `suspension = 3`, the existing
  row is kept, `kind = 'programming'` and `phase = 'active'`; also a fresh database and a v1 database
  (built from the baseline v1 statements) end at v3.
- Start/end: `POST /programming/vacation` with and without `until`; `GET /programming` shows
  `kind`, `frost_setpoint_c`, `previous_setpoint_c`; `GET /stove/status` shows setpoint 7; resume
  restores the setpoint, STOP (only when it was OFF before) and the chrono blocks.
- Refusals: second activation `409 already_suspended` (both kinds); `PUT /stove/setpoint` and
  `PUT /stove/settings/energy-saving` return `409 vacation_active` during vacation and work again
  after the end.
- Auto-end: `until` = now + 2 min, the poller ends the vacation and records `vacation_ended`
  (`reason=until`).
- Rollback and early return: a throwaway script in `%TEMP%` composes `SuspensionRepository` with the
  in-process simulator, (a) forces a failure on the setpoint write, on the STOP write, then on
  `SuspensionStore.save`, and checks that nothing stays changed; (b) mutates the simulated setpoint
  register between two `check()` calls (panel change: setpoint kept, rest restored,
  `vacation_overridden`), then re-enables a chrono block the same way (everything restored).
- Crash recovery (amendment 2, a throwaway script in `%TEMP%` on a temp database): (a) kill the activation
  after the setpoint write (raise `SystemExit` before the `phase="active"` save), then a fresh
  `SuspensionRepository` on the same database runs `check()` and must restore the setpoint, STOP and the
  chrono blocks, clear the state and record `suspension_rolled_back`; (b) the same for the plain
  suspension (kill after the participants are suspended); (c) kill during the restore (after the setpoint
  restore), then a fresh repository's `check()` finishes the restore and records the end event with
  `reason=recovered`; (d) a gateway failure during recovery keeps the state and the next `check()` retries.
- Activation preconditions (final review fixes): activation during SHUTDOWN defers the start and the stove
  starts once OFF; during ALARM/UNKNOWN the outcome carries `vacation_stove_not_started:<state>`; the STOP
  and setpoint snapshots reflect a fresh read (change STOP out-of-band right before activating).
- The plain suspension still behaves as before (suspend, resume, `suspension_overridden`).
- UI: buttons per state, modal recap with and without the STOP change, glance and `/widget`
  banner, disabled setpoint stepper and ENERGY SAVING controls, resume label per kind, toasts.
- Layer check: `rg "core\.suspension" src/stove_pilot/core/stove` and
  `rg "stove_pilot\.(core|lib)" src/stove_pilot/service/ui` return nothing.
- **Attended (user present, real stove, later):** activation sets 7 C and ENERGY SAVING STOP on the
  panel, the chrono blocks are disabled, the stove goes to `ENS` then `ENERGY OK` standby; "Terminer
  le mode vacances" restores the original setpoint, STOP and blocks.

## 10. Implementation tasks

Track progress by ticking the boxes below (this file is the tracker; do not edit the main plan).
Each task ends with its manual check from section 9 and a checkpoint for user review.

- [x] **V1 - Persistence (no behavior change):** `SuspensionStore` schema v2 and `SuspensionState`
  fields. Check: store v2 migration on a copy of the database.
- [x] **V2 - Settings lock:** `StoveRepository` `settings_lock` + `bypass_lock`, composition binding,
  `vacation_setpoint_c` in `common/settings.py` and `.env.example`. Check: the lock is inert while
  not in vacation (setpoint and ENERGY SAVING writes unchanged).
- [x] **V3 - Domain:** `start_vacation`, vacation-aware `resume`/`check` (3.3, 3.4), events.
  Check: rollback and early-return script.
- [x] **V4 - API:** `POST /programming/vacation`, DTO fields on `/programming` and `/summary`,
  `409 vacation_active` mapping. Check: curl start/end, refusals, auto-end.
- [x] **V5 - Dashboard UI:** button, modal, banner, disabled controls, `POST /actions/vacation`,
  client method, `fr.json` keys. Check: browser on the simulator, layer check.
- [x] **F1 - Final review fixes:** vacation thermostat step for SHUTDOWN/ALARM/UNKNOWN (3.2 step 9),
  forced fresh read at activation (3.2 step 3), small hygiene fixes. Check: SHUTDOWN/ALARM activation,
  stale-STOP script, regression script.
- [x] **V6 - Write-ahead journal (domain):** `phase` field and store v3 via stepwise `upgrade`,
  `Suspendable.snapshot()` / `suspend(snapshot)` (chrono participant included), activation order and
  compensation for both kinds (3.2, 3.5), `restoring` phase, recovery in `check()` (3.4),
  `suspension_rolled_back` event. Check: crash-recovery script, store v3 migration, regression script.
- [x] **V7 - Journal API and UI:** `phase` on `/programming` and `/summary`, recovery banner and
  `fr.json` keys. Check: curl and browser on the simulator, layer check.
- [ ] **Checkpoint:** user review. The design doc rev 6 note pointing to this addendum is added by
  the user's session, not by the implementer.