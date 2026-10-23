# Stove Pilot - UI Split Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. Do **not** create git commits: the user reviews and commits. Each task ends with a manual verification and a "Checkpoint" for user review.

> **Rev 2 (2026-10-22):** start condition extended to Amendment 1 and the vacation mode, Task U1 probes for their API additions, UI settings field list re-derived from the code (Task U2), seven `ui_*` fields removed in Task U4, design note numbered as the next free revision (Task U5).

> **Rev 3 (2026-10-23):** start condition updated to the work done since Rev 2 (orchestrator handoff of 2026-10-23, safety restart). New "Runtime safety" constraints: the user's real instance runs from this source tree on 8710/8711, so agent ports move to 287xx, environment variables are set only in child scripts, and Task U4 is split so that the real UI is switched to the new project (user, attended) before any deletion. Task U1 probes the safety, alerts and event-count additions. Task U2 copies 14 settings fields and `service/dev_server.py`. Task U3 smoke list extended. Task U5 numbers the design note rev 7.

> **Start condition:** this plan starts only after all of the following are complete and accepted by the user: `2026-10-21-stove-pilot-implementation-plan.md` (rev 5, Task 19 "MVP accepted", Amendment 1), the vacation mode (`2026-10-22-stove-pilot-vacation-mode-design.md`), the nine tasks of `2026-10-23-stove-pilot-orchestrator-handoff.md`, and the safety restart (`2026-10-23-stove-pilot-safety-restart-plan.md`, S1 to S7 and its Opus review). Each changed the API contract or the UI, so the Task U1 baseline is captured after them and the UI must be final before Task U3 compares the old and new UI side by side. The Task 19 artifacts exist: `Dockerfile`, `compose.yaml`, `requirements.lock`, `deploy/systemd/stove-pilot-{api,ui}.service`, `deploy/windows/run.ps1`, `deploy/backup/`, README deployment sections. Where this plan says "the Task 19 X", it means those files.

> **Runtime safety (overrides every command below):**
> - The user's REAL instance runs permanently from this source tree (`stove-pilot/.venv`, editable install) on 8710 (API) and 8711 (UI) under a Windows scheduled task. Agents never stop, restart or call those processes, never use ports 8710/8711/8712/8720, and never contact the real stove (`http://192.168.68.54`).
> - Agent ports: contract API `28740`; side-by-side run in Task U3: API `28740`, old UI `28741`, new UI `28742`. Every simulator run sets, inside a child script only: `STOVE_PILOT_GATEWAY=simulator`, the ports above, `STOVE_PILOT_UI_API_BASE_URL=http://127.0.0.1:28740`, `STOVE_PILOT_UI_HOST=127.0.0.1`, `STOVE_PILOT_NAVEL_BASE_URL=http://127.0.0.1:9`, `STOVE_PILOT_NAVEL_DEVICE_ID=AABBCCDDEEFF`, `STOVE_PILOT_NAVEL_SECURITY=fake`, a throwaway `STOVE_PILOT_DB_PATH` under `%TEMP%\ui-split\`, and a working directory under `%TEMP%\ui-split\` so no `.env` is loaded. The PowerShell snippets of this plan that assign `$env:STOVE_PILOT_*` in an interactive shell are illustrations: translate them into child scripts. The shell may be shared, so never set `$env:STOVE_PILOT_*` in the session.
> - Nothing before Task U4 Step 0 may change the behavior of the running real instance: Tasks U1 to U3 only add files (the new project, the contract tool and snapshots).

> **Independence:** this plan is self-contained and leaves the API on Flask + Waitress, unchanged. The FastAPI migration is a separate, optional plan (`2026-10-22-stove-pilot-fastapi-migration-plan.md`) that may run later or never. Nothing here depends on it, and the orchestrator must stop at the end of this plan.

**Goal:** Move the web UI into its own project `stove-pilot-ui` (still Flask, moved as-is), so that `stove-pilot` becomes an API-only package, with no change to the API or to the UI behavior.

**Architecture:**
- `stove-pilot-ui/` (new sibling project, package `stove_pilot_ui`):
  - the existing Flask BFF (Jinja2 + HTMX + daisyUI);
  - its own `pyproject.toml` and its own settings class, which reads the same `STOVE_PILOT_*` variable names;
  - its own `.env`, Dockerfile and systemd unit.
  - It talks to the API over HTTP only, exactly as today.
- `stove-pilot/` (package `stove_pilot`): the API, the CLI and the poller, unchanged except for removing the UI code, the UI settings and the `stove-pilot-ui` console script.

**Tech Stack:** unchanged on both sides. The API stays on Flask 3.1 + Waitress. The UI stays on Flask 3.1, Flask-WTF and Waitress, with httpx, Tailwind CSS 4 standalone CLI + daisyUI 5.

## Decision Record (summary)

- **Context:** the UI is a second flavor inside the API package. It shares `Settings`, so the UI process can read the Navel secret. It also shares `service/app.py` and `service/features.py`, and the latter imports UI modules.
- **Decision:** a separate UI project, kept on Flask and moved without behavior change.
- **Alternatives considered:**
  - Keep the UI inside the package: rejected (secret exposure, coupled releases).
  - Rewrite the UI while moving it: rejected, because it would combine a move with a behavior change.
- **Consequences:**
  - Independent UI releases and deploys.
  - Least privilege: the UI env no longer contains `STOVE_PILOT_NAVEL_*`.
  - One more `pyproject.toml`, venv and image to maintain.
  - No infrastructure cost change: still two processes or two containers.

## Global Constraints

- **No automated tests** (no `tests/`, no pytest). Verification is manual (CLI, `curl`, browser), plus the `tools/contract_snapshot.py` comparison written in Task U1. The tool is a developer aid, not a test suite.
- **Pure relocation (strangler rule):** no behavior change in the UI or the API. The API contract must be identical before and after (checked in Task U4).
- **Simulator only** for agents. Every run sets `STOVE_PILOT_GATEWAY=simulator` explicitly in the shell, because `stove-pilot/.env` may select `navel`; shell variables override `.env` values. Real-stove checks happen only in the step marked **(attended)**.
- Agents never read, print or edit `.env` files. When a step needs the UI `.env`, ask the user to create it from `.env.example`.
- Contract runs use:
  - a throwaway database directory, `$env:TEMP\ui-split\contract`;
  - port `28740` (wherever this plan says `8720`, read `28740`);
  - a throwaway key, `contract-check-key`, never the real key.
- English for code, comments, logs and identifiers. French only for UI copy. No emojis. Short Sphinx docstrings, type hints everywhere, files under 500 LOC, imports at the top of modules.
- Packaging follows the existing convention: setuptools auto-discovery, src layout, no extra tool tables in `pyproject.toml`.
- Console script names are a contract. `stove-pilot-api` and `stove-pilot` stay in `stove-pilot`. `stove-pilot-ui` moves to the new project under the same name.
- Python 3.13 on the dev PC (`py -3.13`), `requires-python = ">=3.12"`.

---

### Task U1: Contract snapshot tool and baseline

**Files:**
- Create: `stove-pilot/tools/contract_snapshot.py`
- Create (generated): `stove-pilot/contract/before-ui-split-nokey.json`, `stove-pilot/contract/before-ui-split-key.json`

The tool captures what the UI depends on: statuses, media types, contract headers, problem fields and the body shape (keys and JSON types, never volatile values). It also compares two captures. The FastAPI migration plan reuses it if that plan ever runs.

- [ ] **Step 1: Write `tools/contract_snapshot.py`:**

```python
"""Capture or compare the HTTP contract of a running Stove Pilot API (simulator only).

Capture: python tools/contract_snapshot.py capture --base-url http://127.0.0.1:8720 --out FILE
Compare: python tools/contract_snapshot.py compare OLD_FILE NEW_FILE

A snapshot keeps statuses, media types, contract headers, problem fields and body shapes (keys
and JSON types), never volatile values. The API key is read from the environment variable named
by ``--api-key-env``, never from the command line.
"""

import argparse
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

import httpx

_PREFIX: Final = "/api/v1"
_PROBLEM_MEDIA_TYPE: Final = "application/problem+json"
_PROBLEM_EXACT_KEYS: Final = ("type", "title", "status", "code", "reason", "register")
_CONTRACT_HEADERS: Final = ("allow", "etag", "retry-after")
_JSON_HEADERS: Final = {"Content-Type": "application/json"}
_START_ATTEMPTS: Final = 7
_TIMEOUT_S: Final = 30.0


@dataclass(frozen=True, slots=True)
class Probe:
    """One request of the snapshot."""

    name: str
    method: str
    path: str
    json_body: Any = None
    raw_body: bytes | None = None
    headers: dict[str, str] | None = None
    send_key: bool = True


def _probes() -> list[Probe]:
    """List the probes in execution order (later probes rely on the state left by earlier ones).

    :return: The probes.
    """
    starts = [
        Probe(f"start_{index}", "POST", "/stove/commands/start")
        for index in range(1, _START_ATTEMPTS + 1)
    ]
    return [
        Probe("health", "GET", "/health"),
        Probe("features", "GET", "/features"),
        Probe("status", "GET", "/stove/status"),
        Probe("status_fresh", "GET", "/stove/status?max_age_s=0"),
        Probe("status_negative_age", "GET", "/stove/status?max_age_s=-1"),
        Probe("status_bad_age", "GET", "/stove/status?max_age_s=abc"),
        Probe("summary", "GET", "/summary"),
        Probe("clock", "GET", "/stove/clock"),
        Probe("settings", "GET", "/stove/settings"),
        Probe("maintenance", "GET", "/stove/maintenance"),
        Probe("temperatures", "GET", "/history/temperatures"),
        Probe("temperatures_bad_bucket", "GET", "/history/temperatures?bucket_min=0"),
        Probe("temperatures_bad_from", "GET", "/history/temperatures?from=yesterday"),
        Probe("events", "GET", "/history/events?limit=5"),
        Probe("events_bad_limit", "GET", "/history/events?limit=0"),
        Probe(
            "events_range_empty",
            "GET",
            "/history/events?from=2000-01-01T00:00:00&to=2000-01-02T00:00:00",
        ),
        Probe(
            "events_cursor_empty",
            "GET",
            "/history/events?limit=5&before_at=2000-01-01T00:00:00&before_id=1",
        ),
        Probe(
            "events_bad_cursor",
            "GET",
            "/history/events?before_at=2000-01-01T00:00:00&before_id=x",
        ),
        Probe("programming", "GET", "/programming"),
        Probe("chrono", "GET", "/chrono"),
        Probe("chrono_fingerprint", "GET", "/chrono/fingerprint"),
        Probe("chrono_put_no_if_match", "PUT", "/chrono", json_body={}),
        Probe(
            "chrono_put_weak_tag_bad_body",
            "PUT",
            "/chrono",
            json_body={},
            headers={"If-Match": 'W/"stale"'},
        ),
        Probe("block_unknown", "PUT", "/chrono/blocks/bogus/state", json_body={"enabled": True}),
        Probe("block_bad_body", "PUT", "/chrono/blocks/day/state", json_body={}),
        Probe("setpoint_no_body", "PUT", "/stove/setpoint"),
        Probe("setpoint_array", "PUT", "/stove/setpoint", raw_body=b"[]", headers=_JSON_HEADERS),
        Probe(
            "setpoint_bad_json", "PUT", "/stove/setpoint", raw_body=b"{bad", headers=_JSON_HEADERS
        ),
        Probe("setpoint_extra", "PUT", "/stove/setpoint", json_body={"value_c": 21, "x": 1}),
        Probe("setpoint_out_of_range", "PUT", "/stove/setpoint", json_body={"value_c": 99}),
        Probe("setpoint_ok", "PUT", "/stove/setpoint", json_body={"value_c": 21}),
        Probe("power_ok", "PUT", "/stove/power", json_body={"level": 3}),
        Probe("display_eco", "PUT", "/stove/settings/display-eco", json_body={"enabled": False}),
        Probe("clock_sync", "POST", "/stove/clock/sync"),
        Probe("suspend", "POST", "/programming/suspend", json_body={}),
        Probe("resume", "POST", "/programming/resume"),
        Probe("stop_no_body", "POST", "/stove/commands/stop"),
        *starts,
        # Reason: after the starts the stove is no longer OFF, so the vacation never triggers a
        # thermostat start that would change the state seen by the probes above.
        Probe("vacation", "POST", "/programming/vacation", json_body={}),
        Probe("vacation_again", "POST", "/programming/vacation", json_body={}),
        Probe("programming_vacation", "GET", "/programming"),
        Probe("setpoint_vacation_locked", "PUT", "/stove/setpoint", json_body={"value_c": 21}),
        Probe("resume_vacation", "POST", "/programming/resume"),
        Probe("events_counts", "GET", "/history/events?limit=5&include_counts=1&page_size=5"),
        Probe("alerts", "GET", "/history/alerts"),
        Probe("alerts_bad_limit", "GET", "/history/alerts?limit=0"),
        Probe("safety", "GET", "/safety"),
        Probe(
            "safety_put_ok",
            "PUT",
            "/safety",
            json_body={
                "enabled": False,
                "threshold_c": 10,
                "max_attempts": 3,
                "retry_interval_min": 15,
            },
        ),
        Probe(
            "safety_put_half_degree",
            "PUT",
            "/safety",
            json_body={
                "enabled": False,
                "threshold_c": 10.5,
                "max_attempts": 3,
                "retry_interval_min": 15,
            },
        ),
        Probe(
            "safety_put_short_interval",
            "PUT",
            "/safety",
            json_body={
                "enabled": False,
                "threshold_c": 10,
                "max_attempts": 3,
                "retry_interval_min": 5,
            },
        ),
        Probe("safety_rearm", "POST", "/safety/rearm"),
        Probe("unknown_path", "GET", "/nope"),
        Probe("wrong_method", "DELETE", "/stove/status"),
        Probe("trailing_slash", "GET", "/health/"),
    ]


def _key_probes() -> list[Probe]:
    """List the extra probes of a run where the API enforces a key.

    :return: The probes sent without the key.
    """
    return [
        Probe("no_key_health", "GET", "/health", send_key=False),
        Probe("no_key_status", "GET", "/stove/status", send_key=False),
    ]


def _shape(value: Any) -> Any:
    """Reduce a JSON value to its keys and JSON types.

    :param value: Parsed JSON value.
    :return: The shape (lists keep the shape of their first item only).
    """
    if isinstance(value, dict):
        return {key: _shape(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_shape(value[0])] if value else []
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, int | float):
        return "number"
    return "null" if value is None else "str"


def _record(response: httpx.Response) -> dict[str, Any]:
    """Describe one response.

    :param response: HTTP response.
    :return: JSON-safe record.
    """
    media_type = response.headers.get("content-type", "").split(";")[0].strip()
    record: dict[str, Any] = {
        "status": response.status_code,
        "media_type": media_type,
        "headers": [name for name in _CONTRACT_HEADERS if name in response.headers],
    }
    try:
        body = response.json()
    except ValueError:
        record["shape"] = "non-json"
        return record
    if media_type == _PROBLEM_MEDIA_TYPE and isinstance(body, dict):
        record["problem"] = {key: body[key] for key in _PROBLEM_EXACT_KEYS if key in body}
        record["issues"] = body.get("issues")
    record["shape"] = _shape(body)
    return record


def _capture(base_url: str, api_key_env: str | None) -> dict[str, Any]:
    """Run every probe against the API.

    :param base_url: API root without the ``/api/v1`` prefix.
    :param api_key_env: Name of the variable holding the key, or ``None`` for a run without key.
    :return: Records keyed by probe name.
    """
    api_key = os.environ.get(api_key_env, "") if api_key_env else ""
    if api_key_env and not api_key:
        raise SystemExit(f"error: environment variable {api_key_env} is empty")
    probes = _probes() + (_key_probes() if api_key else [])
    records: dict[str, Any] = {}
    with httpx.Client(base_url=base_url.rstrip("/") + _PREFIX, timeout=_TIMEOUT_S) as client:
        for probe in probes:
            headers = dict(probe.headers or {})
            if api_key and probe.send_key:
                headers["X-API-Key"] = api_key
            response = client.request(
                probe.method,
                probe.path,
                json=probe.json_body,
                content=probe.raw_body,
                headers=headers,
            )
            records[probe.name] = _record(response)
    return records


def _compare_shape(path: str, old: Any, new: Any, failures: list[str]) -> None:
    """Append shape differences (``null`` matches any type, empty lists match any list).

    :param path: Location used in the messages.
    :param old: Baseline shape.
    :param new: Candidate shape.
    :param failures: Output list.
    """
    if old == "null" or new == "null":
        return
    if isinstance(old, dict) and isinstance(new, dict):
        failures.extend(f"{path}.{key} removed" for key in sorted(old.keys() - new.keys()))
        failures.extend(f"{path}.{key} added" for key in sorted(new.keys() - old.keys()))
        for key in sorted(old.keys() & new.keys()):
            _compare_shape(f"{path}.{key}", old[key], new[key], failures)
        return
    if isinstance(old, list) and isinstance(new, list):
        if old and new:
            _compare_shape(f"{path}[0]", old[0], new[0], failures)
        return
    if old != new:
        failures.append(f"{path}: {old} -> {new}")


def _compare(old_path: Path, new_path: Path) -> int:
    """Print the differences between two snapshots.

    :param old_path: Baseline snapshot.
    :param new_path: Candidate snapshot.
    :return: 1 when a contract difference exists, otherwise 0.
    """
    old = json.loads(old_path.read_text(encoding="utf-8"))
    new = json.loads(new_path.read_text(encoding="utf-8"))
    failures = [f"{name}: probe missing" for name in sorted(old.keys() - new.keys())]
    infos: list[str] = []
    for name in sorted(old.keys() & new.keys()):
        before, after = old[name], new[name]
        for field in ("status", "media_type", "problem"):
            if before.get(field) != after.get(field):
                failures.append(f"{name}: {field} {before.get(field)!r} -> {after.get(field)!r}")
        failures.extend(
            f"{name}: header {header} removed"
            for header in sorted(set(before["headers"]) - set(after["headers"]))
        )
        infos.extend(
            f"{name}: header {header} added"
            for header in sorted(set(after["headers"]) - set(before["headers"]))
        )
        if before.get("issues") != after.get("issues"):
            infos.append(f"{name}: issues {before.get('issues')!r} -> {after.get('issues')!r}")
        _compare_shape(name, before.get("shape"), after.get("shape"), failures)
    for line in failures:
        print(f"FAIL {line}")
    for line in infos:
        print(f"INFO {line}")
    print(f"{len(failures)} failure(s), {len(infos)} info line(s)")
    return 1 if failures else 0


def main() -> int:
    """Parse the command line and run a capture or a comparison.

    :return: Process exit code.
    """
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)
    capture = commands.add_parser("capture", help="Run the probes and write a snapshot.")
    capture.add_argument("--base-url", required=True)
    capture.add_argument("--out", type=Path, required=True)
    capture.add_argument("--api-key-env", default=None)
    compare = commands.add_parser("compare", help="Compare two snapshots.")
    compare.add_argument("old", type=Path)
    compare.add_argument("new", type=Path)
    args = parser.parse_args()
    if args.command == "compare":
        return _compare(args.old, args.new)
    records = _capture(args.base_url, args.api_key_env)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(records, indent=2) + "\n", encoding="utf-8")
    print(f"{len(records)} probe(s) written to {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 2: Capture the baseline without a key.** Run from `stove-pilot/` with the backend venv active, in two shells:

```powershell
# Shell 1: throwaway simulator API
$env:STOVE_PILOT_GATEWAY = "simulator"
$env:STOVE_PILOT_DB_PATH = "$env:TEMP\stove-pilot-contract\stove-pilot.sqlite"
$env:STOVE_PILOT_API_PORT = "8720"
$env:STOVE_PILOT_API_KEY = ""
Remove-Item -Recurse -Force "$env:TEMP\stove-pilot-contract" -ErrorAction SilentlyContinue
stove-pilot-api

# Shell 2
python tools/contract_snapshot.py capture --base-url http://127.0.0.1:8720 --out contract/before-ui-split-nokey.json
```

- [ ] **Step 3: Capture the baseline with a key.**
  - Shell 1: stop the API, wipe the database directory again, set `$env:STOVE_PILOT_API_KEY = "contract-check-key"`, and restart it.
  - Shell 2: run `$env:CONTRACT_API_KEY = "contract-check-key"`, then `python tools/contract_snapshot.py capture --base-url http://127.0.0.1:8720 --out contract/before-ui-split-key.json --api-key-env CONTRACT_API_KEY`.
- [ ] **Step 4: Check the tool is deterministic.**
  - Repeat Step 2 into `contract/repeat-nokey.json`, starting from a fresh database.
  - Run `python tools/contract_snapshot.py compare contract/before-ui-split-nokey.json contract/repeat-nokey.json`. Expected: `0 failure(s)`.
  - If a probe flaps (for example a simulator timing race on `start_1`), record it in a `# Reason:` comment next to the probe list and treat that probe as informative.
  - Delete `repeat-nokey.json`.
- [ ] **Manual verification:**
  - In `before-ui-split-key.json`: `no_key_status` is 401 with code `unauthorized`, and `no_key_health` is 200.
  - In `before-ui-split-nokey.json`:
    - `start_7` is 429 with a `retry-after` header;
    - `chrono` carries an `etag` header;
    - `chrono_put_no_if_match` is 428;
    - `block_unknown` is 404 `unknown_block`;
    - `events_range_empty` and `events_cursor_empty` are 200 with an empty list, and `events_bad_cursor` is a 4xx problem (whatever Amendment 1 implemented, as long as it is stable);
    - `vacation` is 200, `vacation_again` is 409 `already_suspended`, `programming_vacation` carries `kind`, `frost_setpoint_c` and `previous_setpoint_c`, `setpoint_vacation_locked` is 409 with reason `vacation_active`, and `resume_vacation` is 200.
    - `events` stays a JSON array while `events_counts` is an object with the counts; `alerts` is 200 (list) and `alerts_bad_limit` a 4xx problem;
    - `safety` and `safety_put_ok` are 200 with the `SafetyDto` shape, `safety_put_half_degree` and `safety_put_short_interval` are 422, `safety_rearm` is 200; `summary` carries the `safety` block.
  - If a probe path, parameter or body field differs from what Amendment 1, the vacation mode, the handoff tasks or the safety restart actually shipped (for example the accepted values of `include_counts`), align the probe with the shipped API (never the reverse) and note it in the checkpoint report. The safety probes keep `enabled: false` so the simulator stove is never started by the guard.
- [ ] **Checkpoint.**

### Task U2: Scaffold `stove-pilot-ui`

**Files:**
- Create: `stove-pilot-ui/{pyproject.toml,README.md,.env.example,.gitignore,MANIFEST.in}`
- Create: `stove-pilot-ui/src/stove_pilot_ui/{__init__.py,common/__init__.py,common/settings.py,common/logging.py,service/__init__.py,service/signals.py}`
- Create: `stove-pilot-ui/tools/{build-css.ps1,build-css.sh}`

Layout note: the UI keeps the `service/ui/` sub-path of the old package, so the move is a mechanical import rewrite. Reshaping the UI package (for example `lib/` for the API client, feature folders) is a possible follow-up, never in the same step.

- [ ] **Step 1: `pyproject.toml`** (same build backend and conventions as `stove-pilot`):

```toml
[build-system]
requires = ["setuptools>=75"]
build-backend = "setuptools.build_meta"

[project]
name = "stove-pilot-ui"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = [
  "flask>=3.1,<4",
  "flask-wtf>=1.2,<2",
  "httpx>=0.28,<1",
  "pydantic>=2.9,<3",
  "pydantic-settings>=2.6,<3",
  "tzdata>=2024.1",
  "waitress>=3.0,<4",
]

[project.scripts]
stove-pilot-ui = "stove_pilot_ui.server_ui:main"
```

- [ ] **Step 2: `common/settings.py`.** Keep the class name `Settings` and the field names, so the UI modules only change their import path. The fields are exactly those the UI reads at the time of the move: re-derive the list with `rg -o "settings\.[a-z_]+" src/stove_pilot/service/ui src/stove_pilot/server_ui.py` and copy each field with its exact type, default and bounds from `stove_pilot/common/settings.py`. At Rev 3 that is 14 fields: `api_key`, `dev`, `features`, `gateway`, `poll_interval_s`, `timezone`, `vacation_setpoint_c` and the seven `ui_*` fields (the `rg` output also lists template names such as `settings.html` or catalog keys such as `settings.title`: ignore those, keep only real `Settings` attributes). The snippet below predates Rev 3 and is incomplete: the re-derived list wins. `gateway`, `poll_interval_s` and `vacation_setpoint_c` are API-owned values the UI also reads; keep them as settings in this pure relocation (same variable names, so one value in both env files) and list "read them from the API instead" as a follow-up in the checkpoint report. With `extra="ignore"`, an `.env` shared with the API stays valid.

```python
"""UI configuration: the same ``STOVE_PILOT_`` variable names as the API, UI fields only."""

from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Environment-driven UI settings (prefix ``STOVE_PILOT_``)."""

    model_config = SettingsConfigDict(env_prefix="STOVE_PILOT_", env_file=".env", extra="ignore")

    features: set[Literal["chrono"]] = {"chrono"}
    timezone: str = "Europe/Paris"
    api_key: SecretStr = SecretStr("")

    ui_host: str = "0.0.0.0"
    ui_port: int = 8711
    ui_api_base_url: str = "http://127.0.0.1:8710"
    ui_secret_key: SecretStr = SecretStr("")
    ui_frame_ancestors: str = "'self'"
    ui_refresh_default_s: Literal[0, 10, 30, 60, 300] = 30
    ui_history_events_page_size: int = Field(default=25, ge=5, le=100)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide settings instance.

    :return: Cached settings.
    """
    return Settings()
```

- [ ] **Step 3: Copy verbatim** `stove_pilot/common/logging.py` to `stove_pilot_ui/common/logging.py`, and `stove_pilot/service/signals.py` to `stove_pilot_ui/service/signals.py` (both are stdlib-only). Copy `stove_pilot/service/dev_server.py` to `stove_pilot_ui/service/dev_server.py` (the UI uses `enter` in `server_ui.py` and `apply_ui_dev` in `create_ui_app`); keep only what the UI uses, adapt the watched paths to the new project, and keep its documented inline imports as they are. Add empty `__init__.py` files.
- [ ] **Step 4: Packaging files:**
  - `MANIFEST.in`: `recursive-include src/stove_pilot_ui *.html *.css *.js *.json *.png *.webmanifest`, then `prune tools`.
  - `.gitignore`: `.env`, `tools/tailwindcss*`, `tools/daisyui*.mjs`, `__pycache__/`, `.venv/`, `*.egg-info/`.
  - `.env.example`: copy the UI-related lines of `stove-pilot/.env.example` verbatim (`STOVE_PILOT_UI_*`, `STOVE_PILOT_API_KEY`, `STOVE_PILOT_FEATURES`, `STOVE_PILOT_TIMEZONE`). Add a header comment saying that `STOVE_PILOT_API_KEY` must equal the API's value, and that leaving both empty disables the check.
- [ ] **Step 5: CSS tooling.** Copy `stove-pilot/tools/build-css.ps1` and `build-css.sh`, changing only the static path to `src/stove_pilot_ui/service/ui/static/css` in both scripts. The `@plugin "../../../../../../tools/daisyui.mjs"` line of `input.css` keeps working, because the depth from `static/css` to the project root is unchanged (6 levels).
- [ ] **Step 6: README stub:** purpose, install (`py -3.13 -m venv .venv`, then `.venv\Scripts\pip install -e .`), configuration (`.env` from `.env.example`), run (`stove-pilot-ui`) and CSS rebuild. The deployment section is added in Task U4.
- [ ] **Manual verification:**
  - `.venv\Scripts\pip install -e .` succeeds in `stove-pilot-ui/`.
  - `.venv\Scripts\python -c "from stove_pilot_ui.common.settings import get_settings; print(sorted(get_settings().model_dump()))"`, run from a working directory under `%TEMP%\ui-split\` (no `.env`), lists exactly the fields of Step 2 (14 at Rev 3).
- [ ] **Checkpoint.**

### Task U3: Move the UI code (old copy stays in place)

**Files:**
- Copy: `stove-pilot/src/stove_pilot/service/ui/**` to `stove-pilot-ui/src/stove_pilot_ui/service/ui/**` (Python modules, `routes/`, `templates/`, `static/`, `i18n/`)
- Create: `stove-pilot-ui/src/stove_pilot_ui/service/app.py`, `stove-pilot-ui/src/stove_pilot_ui/service/features.py`, `stove-pilot-ui/src/stove_pilot_ui/server_ui.py`

- [ ] **Step 1: Copy the tree** byte for byte, including the committed `app.css`, the icons and images, and the vendored `htmx.min.js`.
- [ ] **Step 2: `service/app.py`.** Copy `create_ui_app` from `stove_pilot/service/app.py` verbatim, with only the imports it uses (`Flask`, `Settings`, `StovePilotApiClient`, `register_context`, `LastGoodCache`, the four route registrars, `register_security`) and any private helper or constant it references. Copy nothing API-related.
- [ ] **Step 3: `service/features.py`.** This UI-side registry replaces `FeatureSpec.register_ui`.

```python
"""UI feature registry: page registrars keyed by the ``settings.features`` names."""

from collections.abc import Callable

from flask import Flask

from stove_pilot_ui.service.ui.client import StovePilotApiClient
from stove_pilot_ui.service.ui.routes.chrono import register_chrono_ui

UiRegistrar = Callable[[Flask, Callable[[], StovePilotApiClient]], None]

# Reason: keys are the ``settings.features`` names, shared with the API registry.
UI_FEATURES: dict[str, UiRegistrar] = {"chrono": register_chrono_ui}
```

- [ ] **Step 4: `server_ui.py`.** Copy `stove_pilot/server_ui.py`. Replace `_ui_registrars` with a version that returns `[UI_FEATURES[name] for name in sorted(settings.features) if name in UI_FEATURES]`, and drop the `FEATURES` import.
- [ ] **Step 5: Rewrite imports** in every copied `.py` file:
  - `stove_pilot.service.ui.` becomes `stove_pilot_ui.service.ui.`;
  - `stove_pilot.common.` becomes `stove_pilot_ui.common.`;
  - `stove_pilot.service.signals` becomes `stove_pilot_ui.service.signals`.

  Then look for string literals naming the old package (`importlib.resources.files("stove_pilot...`, Flask import names): `rg -n "stove_pilot[.\"']" stove-pilot-ui/src` must return nothing. The pattern does not match `stove_pilot_ui`.
- [ ] **Step 6: Rebuild the CSS** with `stove-pilot-ui/tools/build-css.ps1`, then check it is unchanged: `(Get-FileHash stove-pilot-ui/src/stove_pilot_ui/service/ui/static/css/app.css).Hash -eq (Get-FileHash stove-pilot/src/stove_pilot/service/ui/static/css/app.css).Hash` must print `True`. A difference means a `@source` path no longer resolves.
- [ ] **Manual verification (side by side, simulator):**
  - Start three child processes (Runtime safety rules, empty `STOVE_PILOT_API_KEY`):
    - the simulator API from `stove-pilot\.venv` on 28740;
    - the old UI (`stove-pilot\.venv\Scripts\stove-pilot-ui`) on 28741;
    - the new UI (`stove-pilot-ui\.venv\Scripts\stove-pilot-ui`) on 28742.
  - Compare 28741 and 28742 on the dashboard, `/widget`, Chrono, Historique and Reglages, in light and dark themes and at phone width (390 px): screenshots side by side.
  - On 28742, exercise the write paths:
    - a setpoint change;
    - start and stop with their toasts;
    - the suspend modal;
    - the vacation mode: modal recap, banner on the glance and `/widget`, setpoint stepper and ENERGY SAVING controls disabled, "Terminer le mode vacances" restores the setpoint;
    - the Historique event timeline: arrow pagination with `page N / M`, arrows disabled on the first and last page, cursor kept in the URL, live refresh only on the first page;
    - the Historique alerts card (occurrences and last date, empty state);
    - the temperature charts: readable axes, hover tooltip with the mouse and tap or drag tooltip at phone width, still working after a live swap;
    - the Reglages "Securite" card: save valid values, a 10.5 threshold or a 5 min interval shows the validation error, "Rearmer" appears only in the paused or failed states; the safety badge on the glance card; `/widget` unchanged;
    - the dashboard readings row (fumes, power, stove clock blocks) and the chrono preview overlap hatching while editing;
    - a chrono block toggle;
    - the chrono editor open in two tabs: save in one, then save in the other, and the version-conflict message must appear.
  - CSRF still rejects a POST without a token: a `curl -X POST` on any UI command route returns the CSRF error, not a 500.
  - On `http://localhost:28742`, the service worker of old plan Task 18 registers and the offline page works (DevTools > Application), exactly as on 28741; every `_PRECACHED_STATIC` file exists in the new project (the app fails fast otherwise).
  - `STOVE_PILOT_DEV=true` in the child script of the new UI: saving a watched UI file restarts it.
- [ ] **Checkpoint.**

### Task U4: Remove the UI from `stove-pilot` and split deployment

**Files:**
- Delete: `stove-pilot/src/stove_pilot/service/ui/`, `stove-pilot/src/stove_pilot/server_ui.py`, `stove-pilot/tools/{build-css.ps1,build-css.sh,tailwindcss.exe,daisyui.mjs,daisyui-theme.mjs}`
- Modify: `stove-pilot/src/stove_pilot/service/app.py`, `stove-pilot/src/stove_pilot/service/features.py`, `stove-pilot/src/stove_pilot/common/settings.py`, `stove-pilot/{pyproject.toml,MANIFEST.in,.gitignore,.env.example,README.md}`
- Modify: the Task 19 `compose.yaml`, systemd UI unit and Windows launcher. Create `stove-pilot-ui/{Dockerfile,requirements.lock}`.

- [ ] **Step 0: Switch the real UI to the new project (user, attended; the orchestrator prepares, the user executes).** The real UI on 8711 is served from `stove-pilot/src/stove_pilot/service/ui/` through the editable install: deleting it while it runs breaks the live UI (templates and static files are read from disk), and Windows locks `stove-pilot-ui.exe` while it runs. So, before Step 1:
  - Prepare in `stove-pilot-ui/`: `deploy/windows/run-ui.ps1` (or an updated `stove-pilot/deploy/windows/run.ps1` starting the UI from `..\stove-pilot-ui\.venv`), and written instructions for the user.
  - The user creates `stove-pilot-ui/.env` from `.env.example` (same `STOVE_PILOT_API_KEY` as the API), stops the scheduled task, points it at the new launcher, restarts it, and checks the real UI on 8711 (dashboard, Reglages read only).
  - Ask the user to confirm with AskQuestion. Do not run Step 1 before this confirmation.
- [ ] **Step 1: Backend code.**
  - `service/app.py`: delete `create_ui_app`, its UI imports and the UI dev hook (`apply_ui_dev` import).
  - `service/dev_server.py`: drop the UI-only parts (`apply_ui_dev`, the `server_ui.py` docstring mention); keep what `server_api.py` uses.
  - `service/features.py`: delete the `register_ui` field and its docstring sentence, the `register_chrono_ui` argument, and the two `service.ui` imports.
  - `common/settings.py`: delete the seven `ui_*` fields (including `ui_history_events_page_size`). Fields also read by the API (`api_key`, `dev`, `features`, `gateway`, `poll_interval_s`, `timezone`, `vacation_setpoint_c`, the `safety_*` fields) stay. Leftover `STOVE_PILOT_UI_*` lines in an `.env` are ignored (`extra="ignore"`).
  - `service/signals.py` stays: `server_api.py` still uses it.
- [ ] **Step 2: Backend packaging.**
  - `pyproject.toml`: delete the `stove-pilot-ui` script and the `flask-wtf` dependency. Flask and Waitress stay, because the API uses them.
  - `MANIFEST.in`: `recursive-include src/stove_pilot *.toml *.json`, `prune tools`, `prune discovery`.
  - `.gitignore`: drop the two `tools/` lines.
  - `.env.example`: drop the `STOVE_PILOT_UI_*` lines and add a comment pointing to `../stove-pilot-ui/.env.example`.
  - Reinstall with `.venv\Scripts\pip install -e .`, so that `stove-pilot-ui.exe` disappears from the backend venv.
- [ ] **Step 3: Stale-reference search.** `rg -n "service\.ui|server_ui|create_ui_app|register_ui|flask_wtf|ui_secret_key|ui_api_base_url|ui_frame_ancestors|ui_refresh|ui_history_events_page_size" stove-pilot --glob "!contract/**"` returns nothing. This includes the README, once Step 5 is done.
- [ ] **Step 4: Deployment split.** `stove-pilot/compose.yaml` stays the single compose file.
  - `stove-pilot-ui/Dockerfile`: same pattern as the API Dockerfile (`python:3.12-slim`, non-root user, `pip install -r requirements.lock .`), with `EXPOSE 8711` and `CMD ["stove-pilot-ui"]`.
  - `stove-pilot-ui/requirements.lock`: generated the same way Task 19 generated the API lock.
  - `compose.yaml`, `ui` service:
    - `build.context: ../stove-pilot-ui`;
    - `env_file: ../stove-pilot-ui/.env`;
    - keep `STOVE_PILOT_UI_API_BASE_URL=http://api:8710` in `environment`.
    - The UI container no longer receives the API `.env`, so it no longer sees `STOVE_PILOT_NAVEL_*`.
  - Systemd: move the UI unit to `stove-pilot-ui/deploy/systemd/`, with `WorkingDirectory` and `ExecStart` pointing to the UI project and its venv.
  - Windows launcher: start the UI from `..\stove-pilot-ui\.venv`.
  - Regenerate the API `requirements.lock` (flask-wtf removed).
- [ ] **Step 5: READMEs.**
  - `stove-pilot/README.md`: remove the UI and CSS sections and point to `../stove-pilot-ui/README.md`.
  - Move those sections to the UI README, together with the deployment notes. They must say that **the user** creates `stove-pilot-ui/.env` with the same `STOVE_PILOT_API_KEY` as the API.
- [ ] **Manual verification:**
  - Run the simulator API on 28740 and the UI from the new project on 28742 (child scripts): the full UI smoke of Task U3 passes.
  - The real instance (8710/8711, now on the new UI project) still answers; ask the user to glance at it.
  - Repeat Task U1 Steps 2 and 3 into `contract/after-ui-split-nokey.json` and `contract/after-ui-split-key.json`, then compare each with its `before-ui-split-*` file. Expected: `0 failure(s)` and `0 info line(s)`, since the API did not change.
  - `docker compose build --no-cache` builds both images (if Docker is installed; otherwise say so in the report).
  - Docker is optional on this PC: if Docker is available, the user (not an agent: the real instance already holds 8710/8711) runs `docker compose up` at a moment of their choice; otherwise report `docker compose config` as the only check.
- [ ] **Checkpoint.**

### Task U5: Design doc revision, old plan appendix, attended smoke

**Files:**
- Modify: `zold/code/iot/docs/2026-10-21-stove-pilot-design.md` (rev 7: rev 6 is the safety restart)
- Modify: `zold/code/iot/docs/2026-10-21-stove-pilot-implementation-plan.md` (Appendix A only)

- [ ] **Step 1: Design doc revision.** Add the revision note to the header ("UI moved to the separate `stove-pilot-ui` project; the API is unchanged"), then update:
  - Section 2, goal 7: "`stove-pilot` (JSON API) and `stove-pilot-ui` (separate project, Flask + daisyUI)".
  - Section 4.2 containers table: the UI row becomes "Flask + Waitress, separate project and image".
  - Section 4.3: package layout of both projects.
  - ADR-003: the UI is a separate project with its own settings, env file and image (the decision record summary of this plan); this supersedes "two Flask apps in one package".
  - Section 11: the UI env file is separate and holds no Navel secret.
- [ ] **Step 2: Old plan Appendix A.**
  - Deferred Task 17 now targets `stove-pilot-ui/src/stove_pilot_ui/service/ui/...`, and registers its page in `UI_FEATURES` (`stove_pilot_ui/service/features.py`) instead of `server_ui.py`.
  - Revival checklist item 6: the UI work lives in `stove-pilot-ui`.
  - Touch nothing else in the old plan.
- [ ] **Step 3: Acceptance (attended, real stove, user present).**
  - Run the API with `STOVE_PILOT_GATEWAY=navel` and the UI from its new deployment (Docker or venv). Check the dashboard, `/widget`, Historique, Reglages (read only) and the chrono editor read, both on the LAN URL and on the installed PWA through the internal HTTPS URL.
  - With the user's explicit go-ahead, make one setpoint change from the UI and revert it.
- [ ] **Checkpoint:** UI split accepted. **End of plan.** Do not start the FastAPI migration plan unless the user asks for it.

---

## Self-review notes

- **Requirement coverage:**
  - Separate UI project, kept on Flask: Tasks U2 to U4.
  - API unchanged, proven by the contract comparison: Tasks U1 and U4.
  - Independent of the FastAPI migration: no FastAPI step appears here, and the API dependencies stay Flask + Waitress.
- **Risks:**
  - A missed string reference to the old package: mitigated by the `rg` check in Task U3 Step 5 and the side-by-side run.
  - A CSS build path regression: mitigated by the hash check in Task U3 Step 6.
  - Two `.env` files to keep consistent (the API key): documented in both READMEs.
- **Effort:** about 1.5 working days: U1 half a day, U2 to U4 one day, U5 a short session plus the attended check. No infrastructure cost change.
