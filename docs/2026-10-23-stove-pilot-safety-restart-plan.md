# Stove Pilot - Safety restart ("Securite") plan (2026-10-23)

Status: Proposed. Owner of execution: one orchestrator (Sonnet) with Grok implementers and an Opus final review.

## 1. Goal

A server-side safety guard: when enabled in Reglages, the API process watches the ambient temperature on every poll and restarts the stove when the temperature reaches the configured threshold or goes below it. The feature is mostly server-side (core + API); the UI only adds a settings card and a discreet dashboard badge.

## 2. User decisions (2026-10-23)

| Topic | Decision |
| --- | --- |
| After a safety start | The stove keeps running and regulates on its own setpoint; the stop comes from the chrono, the user or ENERGY SAVING. The server never stops the stove for this feature. |
| Failed start | New attempt after `retry_interval_min` (default 15), at most `max_attempts` per episode (default 3). Both are editable in Reglages; the container environment only provides their defaults. Alert on the dashboard when the restart is impossible. |
| Threshold | Whole degrees only (step 1 C): the 0.5 C precision adds nothing for this use. |
| Manual stop | No automatic restart after a manual stop by the user. The dashboard badge still shows that the safety is ON (paused). |
| Vacation mode | The safety is suspended while a vacation is active (any phase). |
| Look and feel | Another orchestrator is reworking the dashboard look (`2026-10-23-stove-pilot-orchestrator-handoff.md`, tasks 1, 5, 9 on `glance.html`). The badge must fit that new design; it needs a visual checkpoint with the user. |

## 3. Behaviour rules

Evaluated once per successful poll (fresh status only), after the pending-commands step. A failed or stale poll never triggers anything.

### 3.1 Inputs and debounce

- `below` = `ambient_temp_c <= threshold_c`. Readings outside `[0, 40]` C are implausible and ignored (no counter change).
- A start needs `below` on `safety_confirm_polls` consecutive fresh polls (default 2). Any non-below fresh reading resets the counter.

### 3.2 Phases (persisted, survive a restart)

| Phase | Meaning | Leaves when |
| --- | --- | --- |
| `idle` | Armed, watching. | Confirmed `below` and stove `OFF` and no manual latch: attempt 1, go `restarting`. Confirmed `below` and stove `ALARM`/`UNKNOWN`: go `failed` (reason `alarm`/`unknown`). |
| `restarting` | A start was applied, deferred or has an unknown outcome; waiting for ignition. | Stove seen `IGNITION`/`RUNNING`/`CLEANING`: event `safety_restart_succeeded`, `idle`, attempts reset. Stove `ALARM`: `failed` (reason `alarm`, no retry). Stove `OFF`, no pending start, and 5 min since the attempt: attempt failed: `waiting_retry` if attempts < max, else `failed` (reason `attempts_exhausted`). |
| `waiting_retry` | Previous attempt did not light the stove. | `now >= last_attempt_at + retry_interval_min` and still confirmed `below`, stove `OFF`, no latch: next attempt, `restarting`. Rearm condition: `idle`. |
| `failed` | Restart impossible; dashboard alert. | Rearm condition only: `idle`, event `safety_rearmed`. |

Rearm condition (from `waiting_retry` or `failed`): ambient `>= threshold_c + safety_rearm_hysteresis_c` (default 1.0), or the stove is seen `IGNITION`/`RUNNING` (someone lit it), or the user rearms (API `POST /safety/rearm`, or switching `enabled` from false to true). Saving other fields (threshold, attempts, interval) never rearms and never clears the manual latch.

State `SHUTDOWN`: wait (no command). States `IGNITION`/`RUNNING`/`CLEANING`/`STANDBY`: nothing to do (in `STANDBY` the stove's own ENERGY SAVING logic restarts it).

### 3.3 Start command

- Always through `StoveRepository.start(source=EventSource.SYSTEM)`, so every existing guard stays in force (no start in ALARM, deferral while cooling or within the minimum restart interval, no remote alarm reset).
- `APPLIED`: count the attempt, event `safety_restart` (`attempt`, `max_attempts`, `ambient_c`, `threshold_c`). `DEFERRED`: count the attempt and do not call start again while `pending_start()` is set. `NO_OP`: back to `idle`. `CommandRejectedError`: `failed` with the guard reason. `WriteOutcomeUnknownError`: counts as an attempt (never resent before the interval). Other `GatewayError`: no attempt counted, try again next poll.
- Each attempt is a new guarded command; the repository's own rule "a start is never retried" is unchanged. Retries happen only from `OFF`, never from `ALARM`.

### 3.4 Manual stop latch

The latch blocks any safety start until it is cleared. It is set when, while the safety is enabled and no vacation is active:

1. App stop: a `command` event with `command=stop`, `source=USER` and result `applied` or `unknown` (deferred stops included) newer than the last processed one.
2. Panel stop: the safety sees a transition from `IGNITION`/`RUNNING`/`CLEANING`/`STANDBY` to `SHUTDOWN`/`OFF` with no server stop command in the last 5 minutes, and the transition does not match an enabled chrono stop. Match = an enabled chrono stop minute (stove local time: `polled_at` + `clock_drift_s`) within `[stop - 1 min, stop + safety_chrono_stop_window_min]` (default 5) of the first poll that shows the stop. When the chrono feature is disabled, the chrono config is unreadable, or a poll gap hides the transition time, the stop counts as manual (no restart: the user's priority).

The latch is cleared when the stove is seen `IGNITION`/`RUNNING`, on rearm, or when the user enables the safety. Event `safety_paused` (`reason`: `app_stop` or `panel_stop`).

Known limit (documented in the settings hint and README): a panel stop that happens to coincide with a chrono stop minute is treated as a chrono stop.

### 3.5 Suspension, settings, warnings

- Vacation active (`suspension.get().kind == "vacation"`, any phase): no evaluation at all; status `suspended_vacation`. When the vacation ends: `idle`, attempts and confirm counter reset (the latch is kept).
- A plain programming suspension (`kind="suspend"`) does not suspend the safety.
- Disabled: no evaluation. Enabling resets to `idle`, clears the latch and the attempts.
- The safety settings are a server setting: never locked by the vacation mode (`409 vacation_active` does not apply).
- Changing `max_attempts` or `retry_interval_min` during an episode applies at once: `next_attempt_at` is recomputed from `last_attempt_at`, and a `waiting_retry` whose attempts already reach the new maximum goes to `failed` (reason `attempts_exhausted`). Attempts already made are never reset by a settings change.
- Warning `threshold_not_below_setpoint` when `threshold_c >= setpoint_c` of the cached status (a start would only modulate or go to standby); informative, not blocking.
- Monitoring needs the API process: `last_check_at` is exposed so a dead poller is visible.

## 4. Architecture and placement

Layering unchanged (`common -> lib/utilities -> core -> service`); one new feature `core/safety/` (one domain fact: the safety guard).

```text
core/safety/
  models.py      # SafetyConfig, SafetyState, SafetyPhase, SafetyStatus, SafetyReport
  service.py     # SafetyStore: SQLite schema "safety" v1 via lib.sqlite.upgrade (config row + state row)
  rules.py       # pure decision: (config, state, status, now, context) -> SafetyDecision
  detection.py   # pure manual-stop attribution (transition, recent stop command, chrono match)
  ports.py       # Protocols: stove commands, vacation check, chrono stop check, stop-command lookup
  repository.py  # SafetyRepository facade: view(), update(), rearm(), check(status, now)
core/chrono/stops.py   # pure: enabled stop minutes of a ChronoConfig and the window match
```

- `StatusPoller` gains a generic `status_hooks: Sequence[tuple[str, Callable[[StoveStatus, datetime], None]]]` run after `pending`, each isolated by `_step`. The existing `on_poll` stays as is.
- `composition.py`: `build_safety_repository`, `Container.safety`, late-bound vacation check (same pattern as `_settings_lock`), chrono stop check built from `ChronoRepository.get()` (cached config; at most one stove read per detected stop transition, never per poll), `None` when chrono is disabled.
- `core/stove/repository.py` is at 497 lines: add nothing there. Use `get_status`, `start`, `pending_start` and `HistoryRepository.last_event` (or a small history query if `last_event` cannot filter on source).
- Concurrency: the poller thread and API threads share the repository; `SafetyRepository` owns a lock for its state; stove I/O stays serialized by the stove lock.

### 4.1 Settings (`common/settings.py`, `.env.example`)

| Variable | Default | Range |
| --- | --- | --- |
| `STOVE_PILOT_SAFETY_ENABLED_DEFAULT` | `false` | bool (used until the user saves the card) |
| `STOVE_PILOT_SAFETY_THRESHOLD_DEFAULT_C` | `10` | int, 5..20 |
| `STOVE_PILOT_SAFETY_MAX_ATTEMPTS_DEFAULT` | `3` | 1..5 |
| `STOVE_PILOT_SAFETY_RETRY_INTERVAL_DEFAULT_MIN` | `15` | `MIN_RESTART_INTERVAL_MIN`..240 |
| `STOVE_PILOT_SAFETY_CONFIRM_POLLS` | `2` | 1..10 |
| `STOVE_PILOT_SAFETY_REARM_HYSTERESIS_C` | `1.0` | 0.5..5 |
| `STOVE_PILOT_SAFETY_CHRONO_STOP_WINDOW_MIN` | `5` | 1..15 |

The four `*_DEFAULT*` variables only seed the configuration until the user saves the Reglages card; the user-editable values (`enabled`, `threshold_c`, `max_attempts`, `retry_interval_min`) then live in the `safety` SQLite schema. The stove guard `STOVE_PILOT_MIN_RESTART_INTERVAL_MIN` (15) applies to every start, so the retry interval cannot go below it: the API rejects a lower value (422) and the UI input uses it as `min`; an environment default below it is raised to it with one startup warning. The other variables (confirm polls, hysteresis, chrono window) stay container-only.

### 4.2 API (additive only)

- `GET /api/v1/safety` -> `SafetyDto`: `enabled`, `threshold_c` (int), `threshold_min_c` (5), `threshold_max_c` (20), `status` (`disabled`, `armed`, `confirming`, `restarting`, `waiting_retry`, `failed`, `paused_manual`, `suspended_vacation`, `stale`), `attempts`, `max_attempts`, `max_attempts_min` (1), `max_attempts_max` (5), `retry_interval_min`, `retry_interval_min_min` (= `MIN_RESTART_INTERVAL_MIN`), `retry_interval_max_min` (240), `last_attempt_at`, `next_attempt_at`, `failure_reason`, `paused_reason`, `last_check_at`, `warning`.
- `PUT /api/v1/safety` body `{enabled: bool, threshold_c: int, max_attempts: int, retry_interval_min: int}` (all required) -> `SafetyDto`; 422 when a value is outside its range or not a whole number (`10.0` accepted, `10.5` rejected); event `setting_changed` (`setting="safety"`).
- `POST /api/v1/safety/rearm` -> `SafetyDto`; event `safety_rearmed` (`source=USER`).
- `GET /api/v1/summary`: additive `safety` block (`enabled`, `status`, `threshold_c`) and, in `build_alert`, a `failed` safety alert right after the stove alarm ("Rallumage de securite impossible", action with the reason). If the alert store of handoff task 4 exists, record code `SAFETY_RESTART_FAILED` there (severity error) through its public API.
- Events: `safety_restart`, `safety_restart_succeeded`, `safety_restart_failed`, `safety_paused`, `safety_rearmed` (labels in `fr.json` `event.kind.*`).

### 4.3 UI

- Reglages: new card `partials/settings_safety.html` (title "Securite"), one form: toggle, three whole-number inputs with `step="1"` and min/max from the DTO (threshold in C, maximum attempts, interval between attempts in minutes), save button, status line, "Rearmer" button only in `paused_manual`/`failed`/`waiting_retry`, the warning text, a hint: "Le serveur rallume le poele si la temperature ambiante atteint ce seuil ou passe en dessous. Pas de rallumage apres un arret manuel, ni pendant le mode vacances. Desactivez la securite avant tout entretien." View model in `service/ui/settings_view.py` (or a new `safety_view.py` if it pushes the file past 300 lines); routes in `routes/settings.py`.
- Dashboard: one discreet badge only when enabled, inside the existing glance header (no new row), reusing the component family chosen by handoff task 9 (shield icon from `macros/icons.html`, threshold in a technical block). Variants: armed (neutral), paused or suspended (muted, `title`/`aria-label` gives the reason), restarting (info). The failure itself goes through the existing alert line. `/widget` unchanged. View model in a new `service/ui/safety_view.py` (`viewmodels.py` is at 425 lines).
- Visual checkpoint: screenshots at 390 px and desktop, light and dark themes, shown to the user for approval before the final review.

## 5. Tasks

| Id | Scope | Files | Depends on |
| --- | --- | --- | --- |
| S1 | Models, SQLite store, pure rules, settings | `core/safety/{__init__,models,service,rules,ports}.py`, `common/settings.py`, `.env.example` | - |
| S2 | Manual stop attribution, chrono stop helper | `core/safety/detection.py`, `core/chrono/stops.py` | S1 models (may run in parallel if it takes primitives) |
| S3 | Facade, poller hook, composition, events | `core/safety/repository.py`, `core/stove/poller.py`, `service/composition.py` | S1, S2; handoff tasks 4 and 5 finished |
| S4 | API router, schemas, summary block and alert | `service/api/routers/safety.py`, `service/api/schemas/safety.py`, `service/api/schemas/summary.py`, `service/api/routers/summary.py`, `service/app.py` | S3; handoff task 5 finished |
| S5 | Reglages card and event labels | `partials/settings_safety.html`, `settings.html`, `settings_view.py` or `safety_view.py`, `routes/settings.py`, `fr.json`, CSS rebuild | S4; handoff finished |
| S6 | Dashboard badge and visual checkpoint | `partials/glance.html`, `safety_view.py`, dashboard view wiring, `fr.json`, CSS rebuild | S5 |
| S7 | Docs | stove-pilot `README.md` (settings, behaviour, limits), short section in `2026-10-21-stove-pilot-design.md` | S6 |
| R | Final review (Opus, read-only) | whole change set | S7 |

## 6. Verification scenarios (simulator and throwaway scripts with a fake clock)

Count the start writes in every scenario (simulator write log): it is the main safety metric.

1. Chrono stop at 02:00 matched, ambient 10.5 -> 10.0 -> 10.0 with threshold 10: one start, `safety_restart`, then `safety_restart_succeeded` when IGNITION is seen.
2. Single low reading then 10.5: no start.
3. App stop, then ambient below: no start, `paused_manual`; user start later clears the latch.
4. Panel stop off the chrono minutes (state write without a server command): `paused_manual`. Panel stop on a chrono stop minute: stays armed.
5. Start that does not light (stove back to OFF): retry after `retry_interval_min` of fake clock (run once with the defaults 3 / 15 and once with 2 / 30 saved through `PUT /safety`; also lower `max_attempts` during `waiting_retry` and check it goes `failed`), at most `max_attempts` start writes, then `failed` and the summary alert. Stove in ALARM: `failed`, zero further writes, no alarm reset.
6. Vacation active: no evaluation, `suspended_vacation`; after the vacation ends: normal behaviour.
7. Failed or stale polls: no action.
8. Process restart during `waiting_retry`: attempts and `last_attempt_at` are kept, no extra start before the interval.
9. Disabled: nothing. Enabling clears the latch and `failed`. Rearm by hysteresis (ambient >= threshold + 1): `idle`.
10. API: GET/PUT/rearm, 422 cases, summary additive, every existing endpoint unchanged, the safety settings are writable during a vacation.
11. UI: card states (armed, paused, failed with Rearmer, warning), badge variants at 390 px and desktop in both themes, `/widget` unchanged.

## 7. Out of scope

- Stopping the stove from the server when the temperature recovers.
- Notifications outside the dashboard (push, mail).
- Detecting a panel stop that coincides with a chrono stop minute.
- Any change to the existing stove guards.
