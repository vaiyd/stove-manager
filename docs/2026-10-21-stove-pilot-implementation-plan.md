# Stove Pilot Implementation Plan (rev 5)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. Do **not** create git commits: the user reviews and commits. Each task ends with a manual verification and a "Checkpoint" for user review.

> **Rev 3 (2026-10-22, user-approved addendum, applied while the orchestrator was paused):** the mapping results from the 2026-10-21 session (state codes, chrono in 3 blocks, RAM stop verified, clock drift of about 20 s/h) and the stove manual findings are now part of the plan. Changes by task:
> - Task 4: new Step 6a (settings and counters registers), updated Step 7 skeletons.
> - Tasks 5, 6, 8: clock in the common base with auto-sync, messages and alarms, settings, maintenance, and the ignition-stop rule.
> - Tasks 9, 10, 14, 16: the chrono model by blocks.
> - Tasks 11, 17: a 120 min gap.
> - Tasks 13, 14: `/summary`, the settings endpoints, and the `FeatureSpec.summary` entry.
> - Task 15: the glance hero and `/widget`.
> - Task 18: the Reglages page.
> - Task 19: extra acceptance checks.
> - Suspend all programming (design 6.5): `core/suspension` in Task 14, the participants in Tasks 10 and 12, the UI in Task 15.
> - Selectable display refresh (design 8.5): `max_age_s` reads in Tasks 8 and 13, the chrono cache and fingerprint in Tasks 10 and 14, `refresh.js` in Task 15, page behaviors in Tasks 16 to 18.
>
> Design rev 3 sections 1.2, 1.3, 6, 7, and 8 hold the rationale. Tasks already ticked are not reopened; their follow-ups are listed in the later tasks.

> **Rev 4 (2026-10-22, user decision): native chrono only.** EEPROM writes are verified, so the MVP drives programming through the stove's own chrono. Feature B (server schedule) is deferred: Tasks 11, 12, and 17 moved verbatim to Appendix A and must not be implemented. Changes:
> - Task 8: the settings drop `schedule_tick_s` and `catch_up_min`, and `features` becomes `set[Literal["chrono"]]`.
> - Task 5: `EventSource` drops `SCHEDULE`.
> - Tasks 10 and 14: no mutual exclusion (`conflict_check` removed), a single `FEATURES` entry.
> - Tasks 13, 15, 16, 19: schedule references removed.
>
> The `FeatureSpec` registry, `core/suspension`, `/summary`, and the deferred-stop rule stay, so Feature B can come back without touching the rest (design section 10 and the Appendix A checklist).

> **Rev 5 (2026-10-22, user decision): complete PWA.** Task 18 gains Step 3 (service worker, offline page, shell cache versioned by an asset hash) so the installable PWA ships with the MVP; design section 8.6 holds the caching rules. HTTPS for phones relies on the user's existing internal DNS, VPN and TLS termination. Follow-up plans, run after this one: `2026-10-22-stove-pilot-ui-split-plan.md`, then, only when the user asks, `2026-10-22-stove-pilot-fastapi-migration-plan.md`.

**Goal:** A local Flask web application that monitors and controls a Superior MAIRA pellet stove through its Navel T009 module. Weekly programming uses the stove's native chrono, edited through the module.

**Architecture:** Monorepo-style package `stove_pilot` (`common -> lib/utilities -> core -> service`). `service/api` is a JSON Flask API that owns the gateway and the poller. `service/ui` is a separate Flask BFF (Jinja2 + HTMX + daisyUI) that reaches the API over HTTP only. `core/chrono` is a removable feature that depends only on the common base (`core/stove`, `core/history`, `utilities/weektime.py`); `core/suspension` is common and never imports it. Design rationale: `2026-10-21-stove-pilot-design.md` (rev 5).

**Tech Stack:** Python 3.12+ (3.13 on the dev PC), Flask 3.1, Pydantic 2 + pydantic-settings, httpx, Waitress, Flask-WTF, stdlib `sqlite3`/`tomllib`/`zoneinfo`, Jinja2, HTMX 2, Tailwind CSS 4 standalone CLI + daisyUI 5 (no Node).

## Global Constraints

- **No automated tests** (no `tests/` folder, no pytest). Every task ends with a manual verification: CLI, `curl`, or the browser.
- Project root `zold/code/iot/stove-pilot/`, import package `stove_pilot`, src layout, single `pyproject.toml` (PEP 621, setuptools auto-discovery).
- Console scripts: `stove-pilot-api`, `stove-pilot-ui`, `stove-pilot`.
- Env prefix `STOVE_PILOT_`. Secrets in `.env` only. Never commit or log the module password or the API key.
- English for code, comments, logs, and identifiers. French only for UI copy (`service/ui/i18n/fr.json`). No emojis.
- Layer rules: `service/ui` never imports `stove_pilot.core`/`lib`/`service.api`. The common base (`core/stove`, `core/history`, `core/suspension`) never imports `core/chrono`. Only `service/composition.py`, `service/features.py`, and the feature's own router modules know the feature.
- **Agents never drive the real stove.** Development uses `STOVE_PILOT_GATEWAY=simulator`. Real-stove writes happen only in tasks marked **(attended)**, with the user present.
- EEPROM: read-before-write, diff-based, no periodic writes. With `STOVE_PILOT_GATEWAY=navel`, writable registers, command recipes, and chrono fields need `verified = true` at write time (`UnverifiedRegisterError` or the `unverified_layout` issue otherwise). Loaders never raise on `verified = false`, so a read-only live status works on skeleton TOML files. With `STOVE_PILOT_GATEWAY=simulator`, composition passes `allow_unverified=True` so agents can exercise writes; each such write logs one WARNING naming the unverified field. There is no extra setting for this.
- Python 3.12+ (3.13 is what is installed on the dev PC; use `py -3.13`). `requires-python` stays `>=3.12`.
- Data files (profile, chrono layout, UI templates, static assets) are loaded with `importlib.resources.files(...)` and Flask package-relative `template_folder`/`static_folder`, never with paths relative to the working directory.
- Authentication is intentionally minimal: no UI login. `STOVE_PILOT_API_KEY` and `STOVE_PILOT_UI_SECRET_KEY` are optional. The LAN is the trust boundary, so port 8711 must not be exposed beyond the LAN or VPN.
- Never auto-retry an ignition. Never reset alarms remotely. Never stop the stove during `IGNITION` without an explicit user confirmation (it forces an on-site brazier emptying); automated sources defer such a stop instead.
- The stove clock belongs to the common base (`core/stove`), not to the chrono feature. The always-1 unknown chrono bytes (`0x55`, `0x5A`, `0x61`, `0x67`, `0x6E`, `0x74`) and EEPROM `0x4C` to `0x4F` are never written.
- Short Sphinx docstrings, type hints everywhere, files under 500 LOC.
- Verified protocol facts (design section 1.2): RAM item = `address` (0x00 to 0xFF). EEPROM item = `0x8000 | address`. `RequestWriting` uses the same item, `mt="Q"`, `BitData=[8]`, `Masks=[255]`.

---

## Phase 0 - Foundation and discovery

### Task 1: Package scaffold and `common/`

**Files:**
- Create: `stove-pilot/{pyproject.toml,README.md,.env.example,.gitignore}`, `stove-pilot/discovery/.gitkeep`
- Create: `src/stove_pilot/__init__.py`, `src/stove_pilot/common/{__init__,settings,exceptions,logging}.py`

**Interfaces:**
- Produces: `Settings`, `get_settings()`, the exception hierarchy (`StovePilotError`, `ConfigurationError`, `GatewayError`, `GatewayUnavailableError`, `GatewayProtocolError`, `GatewayNackError(command, code)`, `UnverifiedRegisterError(name)` (subclass of `ConfigurationError`), `CommandRejectedError(reason)`, `ValidationFailedError(issues: Sequence[Any])` (stores `list(issues)`, message is `"; ".join(str(i) for i in issues)`), `VersionConflictError`, `FeatureConflictError(reason)`), `configure_logging(level: str) -> None`, `SecretRedactionFilter.register(value: str)`.

- [x] **Step 1: `pyproject.toml`**

```toml
[build-system]
requires = ["setuptools>=75"]
build-backend = "setuptools.build_meta"

[project]
name = "stove-pilot"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = [
  "flask>=3.1,<4",
  "flask-wtf>=1.2,<2",
  "httpx>=0.28,<1",
  "pydantic>=2.9,<3",
  "pydantic-settings>=2.6,<3",
  "waitress>=3.0,<4",
]

[project.scripts]
stove-pilot-api = "stove_pilot.server_api:main"
stove-pilot-ui = "stove_pilot.server_ui:main"
stove-pilot = "stove_pilot.cli:main"
```

- [x] **Step 2: `common/settings.py`**

```python
"""Single configuration home for all Stove Pilot flavors."""

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Environment-driven settings (prefix ``STOVE_PILOT_``)."""

    model_config = SettingsConfigDict(env_prefix="STOVE_PILOT_", env_file=".env", extra="ignore")

    gateway: Literal["navel", "simulator"] = "simulator"
    simulator_seed: Path | None = None
    navel_base_url: str = "http://192.168.68.54"
    navel_device_id: str = ""
    navel_security: SecretStr = SecretStr("")
    navel_timeout_s: float = 10.0
    navel_min_interval_s: float = 0.2

    profile: str = "superior_maira"
    features: set[Literal["chrono", "schedule"]] = {"chrono", "schedule"}
    db_path: Path = Path("./data/stove-pilot.sqlite")
    timezone: str = "Europe/Paris"
    poll_interval_s: int = 30
    sample_interval_s: int = 60
    history_retention_days: int = 180
    schedule_tick_s: int = 30
    catch_up_min: int = 60
    min_restart_interval_min: int = 15
    deferred_start_expiry_min: int = 45
    clock_drift_tolerance_s: int = 120

    api_host: str = "127.0.0.1"
    api_port: int = 8710
    api_key: SecretStr = SecretStr("")

    ui_host: str = "0.0.0.0"
    ui_port: int = 8711
    ui_api_base_url: str = "http://127.0.0.1:8710"
    ui_secret_key: SecretStr = SecretStr("")

    @field_validator("navel_device_id")
    @classmethod
    def _normalize_mac(cls, value: str) -> str:
        return value.replace(":", "").replace("-", "").upper()


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide settings instance.

    :return: Cached settings.
    """
    return Settings()
```

The default `gateway` is `simulator`, which keeps the real stove safe until the user opts in through `.env`. `api_key` and `ui_secret_key` are optional (empty by default): an empty API key disables the key check, and an empty UI secret is replaced by a random one at startup (Task 13 and Task 15).

- [x] **Step 3: `common/exceptions.py`** with the hierarchy listed in Interfaces. Each class has a one-line docstring. `GatewayNackError`, `CommandRejectedError`, `ValidationFailedError`, `UnverifiedRegisterError`, and `FeatureConflictError` store their constructor arguments as attributes.
- [x] **Step 4: `common/logging.py`.** A JSON-lines formatter on the root logger (`ts`, `level`, `logger`, `msg`, extras) plus `SecretRedactionFilter`, which replaces registered secret values with `***` in `msg` and `args`.
- [x] **Step 5: `.env.example`** with every setting and placeholders (`STOVE_PILOT_NAVEL_SECURITY=<AP password from module label>`); `STOVE_PILOT_API_KEY` and `STOVE_PILOT_UI_SECRET_KEY` are marked optional in comments. `.gitignore`: `.env`, `data/`, `tools/tailwindcss*`, `tools/daisyui*.mjs`, `__pycache__/`, `.venv/`, `*.egg-info/`.
- [x] **Manual verification:** Python 3.12+ (3.13 is what is installed on the dev PC; use `py -3.13`): `py -3.13 -m venv .venv; .venv\Scripts\pip install -e .`, then `python -c "from stove_pilot.common.settings import get_settings; print(get_settings().gateway)"` prints `simulator`.
- [x] **Checkpoint.**

---

### Task 2: `lib/navel` - RWMS encoding and the Navel HTTP client

**Files:**
- Create: `src/stove_pilot/lib/__init__.py`, `src/stove_pilot/lib/navel/{__init__,rwms,client}.py`

**Interfaces:**
- Produces: `Memory` (`RAM`, `EEPROM`), `encode_item(memory, address) -> int`, `NavelIdentity(device_type, revision)`, `NavelClient(base_url, device_id, security, timeout_s, min_interval_s)` with `identify()`, `read(items) -> list[int]`, `write(items, values) -> list[int]`, `close()`.

- [x] **Step 1: `rwms.py`**

```python
"""Micronova register item encoding for the Navel ``RWMSmaster`` protocol."""

from enum import StrEnum
from typing import Final

_EEPROM_FLAG: Final = 0x8000


class Memory(StrEnum):
    """Micronova board memory areas."""

    RAM = "ram"
    EEPROM = "eeprom"


def encode_item(memory: Memory, address: int) -> int:
    """Encode a register as a Navel item (RAM: address, EEPROM: 0x8000 | address).

    :param memory: Memory area.
    :param address: Register address (0-255).
    :return: Item value, identical for reads and writes.
    """
    if not 0 <= address <= 0xFF:
        raise ValueError(f"Register address out of range: {address}")
    return _EEPROM_FLAG | address if memory is Memory.EEPROM else address
```

- [x] **Step 2: `client.py`**

```python
"""Thread-safe client for the Navel T009 local JSON API."""

import json
import secrets
import threading
import time
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

import httpx

from stove_pilot.common.exceptions import (
    GatewayNackError,
    GatewayProtocolError,
    GatewayUnavailableError,
)

_ENDPOINT = "/cgi-bin/GetJson.js"
_PROTOCOL = "RWMSmaster"


@dataclass(frozen=True)
class NavelIdentity:
    """Module identity returned by ``Identity``."""

    device_type: str
    revision: str


class NavelClient:
    """Serialize JSON-envelope commands to one Navel module."""

    def __init__(
        self,
        *,
        base_url: str,
        device_id: str,
        security: str,
        timeout_s: float = 10.0,
        min_interval_s: float = 0.2,
    ) -> None:
        self._device_id = device_id.replace(":", "").upper()
        self._security = security
        self._session_id = str(secrets.randbelow(10**8))
        self._min_interval_s = min_interval_s
        self._lock = threading.Lock()
        self._last_request_at = 0.0
        self._identified = False
        self._http = httpx.Client(base_url=base_url, timeout=timeout_s)

    def identify(self) -> NavelIdentity:
        """Authenticate the session.

        :return: Module type and revision.
        """
        with self._lock:
            return self._identify_locked()

    def read(self, items: Sequence[int]) -> list[int]:
        """Read encoded items in one round trip.

        :param items: Items built with ``encode_item``.
        :return: One byte value per item.
        """
        payload = {"Cmd": "RequestReading", "Protocol": _PROTOCOL, **self._item_fields(items)}
        return self._values(self._command(payload), expected=len(items))

    def write(self, items: Sequence[int], values: Sequence[int]) -> list[int]:
        """Write encoded items.

        :param items: Items built with ``encode_item``.
        :param values: One byte value per item.
        :return: Values echoed by the module.
        """
        if len(items) != len(values):
            raise ValueError("items and values must have the same length")
        payload = {
            "Cmd": "RequestWriting",
            "Protocol": _PROTOCOL,
            **self._item_fields(items),
            "Values": list(values),
            "Masks": [0xFF] * len(items),
        }
        return self._values(self._command(payload), expected=len(items))

    def close(self) -> None:
        """Release HTTP resources."""
        self._http.close()

    def _command(self, payload: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            if not self._identified:
                self._identify_locked()
            try:
                return self._send(payload)
            except GatewayNackError:
                # Reason: the module may drop the session silently; re-authenticate once.
                self._identified = False
                self._identify_locked()
                return self._send(payload)

    def _identify_locked(self) -> NavelIdentity:
        pl = self._send({"Cmd": "Identity", "Id": self._device_id, "Security": self._security})
        device = (pl.get("Devices") or [{}])[0]
        self._identified = True
        return NavelIdentity(str(device.get("Type", "")), str(device.get("Revision", "")))

    def _send(self, payload: dict[str, Any]) -> dict[str, Any]:
        self._throttle()
        envelope = {"mt": "Q", "s": {"sj": self._session_id, "ss": "browser"}, "pl": payload}
        body = json.dumps(envelope, separators=(",", ":"))
        try:
            response = self._http.get(f"{_ENDPOINT}?{quote(body, safe='')}")
            response.raise_for_status()
            reply = response.json()
        except httpx.HTTPError as exc:
            raise GatewayUnavailableError(f"Navel request failed: {payload['Cmd']}") from exc
        except ValueError as exc:
            raise GatewayProtocolError(f"Non-JSON reply for {payload['Cmd']}") from exc
        finally:
            self._last_request_at = time.monotonic()
        pl = reply.get("pl") if isinstance(reply, dict) else None
        if not isinstance(pl, dict):
            raise GatewayProtocolError(f"Missing payload for {payload['Cmd']}")
        if reply.get("mt") == "N":
            raise GatewayNackError(payload["Cmd"], int(pl.get("NackErrCode", 0)))
        return pl

    def _throttle(self) -> None:
        wait = self._min_interval_s - (time.monotonic() - self._last_request_at)
        if wait > 0:
            time.sleep(wait)

    @staticmethod
    def _item_fields(items: Sequence[int]) -> dict[str, list[Any]]:
        if not items:
            raise ValueError("at least one item is required")
        count = len(items)
        return {"BitData": [8] * count, "Endianess": ["L"] * count, "Items": list(items)}

    @staticmethod
    def _values(pl: dict[str, Any], *, expected: int) -> list[int]:
        values = pl.get("Values")
        if not isinstance(values, list) or len(values) != expected:
            raise GatewayProtocolError("Unexpected Values in module reply")
        return [int(value) for value in values]
```

- [x] **Manual verification (read-only, real module):** `python -c` script that builds a client from `.env`, then prints `identify()` (expected `T009_3`/`0054`) and `read([0x01, 0x21, 0x8000 | 0x7D])` (expected ambient x2, state, setpoint).
- [x] **Checkpoint.**

---

### Task 3: Discovery CLI

**Files:**
- Create: `src/stove_pilot/service/__init__.py`, `src/stove_pilot/service/cli/{__init__,commands}.py`, `src/stove_pilot/service/composition.py` (first factory), `src/stove_pilot/cli.py`

**Interfaces:**
- Produces: `build_navel_client(settings) -> NavelClient`, `cli.main(argv: list[str] | None = None) -> int`.

| Command | Behavior |
| --- | --- |
| `identify` | `{"device_type", "revision"}` |
| `read ram:0x01 eeprom:0x7D ...` | `{"ram:0x01": 34, "eeprom:0x7D": 17}` |
| `dump --out discovery/<name>.json` | RAM and EEPROM 0x00 to 0xFF in chunks of 8 (module limit: larger batches return blank replies), `{"taken_at", "ram": {"0x00": v}, "eeprom": {...}}`. A NACKed chunk falls back to single reads (`null` on failure). |
| `diff a.json b.json [--memory eeprom]` | Changed addresses, `memory:0xNN old -> new` |
| `watch ram:0x21 ram:0x01 --interval 10 [--csv out.csv]` | One row per interval until Ctrl+C |
| `write eeprom:0x7D 16 --confirm-attended` | Refuses without the flag (exit code 2). Reads before, writes, reads after, prints all three. |
| `clock` | Stove clock (RAM `0x7A` to `0x80`) against the PC clock, with the drift in seconds |

Address parsing (`"eeprom:0x7D"`) stays as private helpers in `commands.py`.

- [x] **Step 1: Implement.**
- [x] **Step 2: Manual verification:** `stove-pilot identify`, `stove-pilot dump --out discovery/02-cli-baseline.json`, `stove-pilot clock` (expected drift about +900 s).
- [x] **Step 3:** copy `%TEMP%\stove-snaps\00-baseline.json` and `01-eeprom-after-setpoint17.json` into `discovery/` with their original names, never overwriting an existing file. Format note: `00-baseline.json` is `{ "ram": {...}, "eeprom": {...} }` (used as the simulator seed), while `01-eeprom-after-setpoint17.json` is a FLAT `{ "0x00": v, ... }` EEPROM map. Task 9's verification must accept the flat format.
- [x] **Checkpoint.**

---

### Task 4: Register and chrono mapping session (attended)

**Deliverables:** `core/stove/profiles/superior_maira.toml` and `core/chrono/layouts/superior_maira.toml` (created as data files, loaded in Tasks 5 and 9), plus a findings update in design section 1.2.

Protocol (user at the panel, developer at the CLI). Take a dump before and after each panel change, then `diff`:

- [x] **Step 1: Setpoint (already verified at EEPROM `0x7D`).** Confirm that the panel shows the value written on 2026-10-21 (17), then restore 16 from the panel and check that `read eeprom:0x7D` returns 16.
- [x] **Step 2: Power level.** Change it on the panel and diff. Candidate: EEPROM `0x7F`.
- [x] **Step 3: State codes.** Start the stove **from the panel**, then run `watch ram:0x21 ram:0x01 ram:0x3E --interval 10 --csv discovery/cycle.csv` through ignition, running, standby, stop, final cleaning, and off. Note the panel display for each code (the display text is also readable at RAM `0xD9` to `0xEF`).
- [x] **Step 4: Chrono layout.** For each panel chrono setting, take one dump, change one field, take another dump, and diff:
  - the master chrono enable flag (candidates `0x4C` to `0x4F`);
  - program 1 start and stop (for example 06:30 gives 39 in the 10 min unit), then its days, setpoint, power, and enable flag;
  - repeat for one field of each other program to confirm the block stride (candidate blocks start at `0x50`, `0x5B`, `0x68`, with 144 = unset).
- [x] **Step 5: RAM writes (start/stop).** From `OFF`, `write ram:0x21 1 --confirm-attended`, then watch the state. If that fails, try the IR key emulation (`write ram:0x58 0x5A`). Then stop while running (`ram:0x21 6`, or the IR key). Record the working recipes.
- [x] **Step 6: Clock write.** `write eeprom:0xF9 <BCD hour>` and `eeprom:0xFA <BCD minute>` to set the correct time. Check that `stove-pilot clock` shows a drift under 60 s and that the panel shows the right time.
- [x] **Step 6a: Settings and counters registers (attended, read-only diffs, no write).** Same protocol as Step 4: dump the EEPROM, change one thing on the panel, dump again, and diff. Restore each panel value afterwards. Record a skipped item as "not available on this panel".
  - ENERGY SAVING STOP offset (+1/+2/+3/OFF) and START offset (-1/-2/-3/OFF), plus any separate enable byte. Note how OFF is encoded.
  - INSTALLATEUR > DISPLAY: `DISPLAY ECO` ON/OFF and `UNITE DISPLAY` HEURES/TEMP.
  - MODE NUIT: only check whether the menu exists. Do not map it unless the user asks.
  - MEMOIRE COMPTEUR (read only, no panel change): the user reads total hours, partial hours, ignition count, and the 5 last alarms from the panel. Search the RAM and EEPROM dumps for those values; they are probably 16-bit, so try both byte orders.
  - The alarm code register: only if an alarm happens naturally. Never provoke one.
- [x] **Step 7: Write both TOML files.** The stove profile skeleton below carries the 2026-10-21 observations. Complete it with Steps 5, 6, and 6a:

```toml
[profile]
name = "superior_maira"

[registers.ambient_temp]
memory = "ram"
address = 0x01
scale = 0.5
verified = true

[registers.fumes_temp]
memory = "ram"
address = 0x3E
verified = true

[registers.state]
memory = "ram"
address = 0x21
verified = true

[registers.setpoint]
memory = "eeprom"
address = 0x7D
writable = true
min = 7
max = 30
verified = true

[registers.power_level]
memory = "eeprom"
address = 0x7F
writable = true
min = 1
max = 5
verified = true

# Step 6a, addresses and encodings to fill in; keep verified = false until written and read back on the real stove.
# [registers.energy_stop_offset]  memory = "eeprom", address = 0x??, writable = true, min = 1, max = 3, off_value = ??, verified = false
# [registers.energy_start_offset] memory = "eeprom", address = 0x??, writable = true, min = 1, max = 3, off_value = ??, verified = false
# [registers.display_eco]         memory = "eeprom", address = 0x??, writable = true, min = 0, max = 1, verified = false
# [counters] total_hours = { memory = "eeprom", address = 0x??, bytes = 2, endian = "little" } ... (read only)

[display]
memory = "ram"
start = 0xD8
length = 24

[states]
0 = { state = "OFF", label = "Off" }
1 = { state = "IGNITION", label = "Check" }
2 = { state = "IGNITION", label = "Ignition phase 1" }
3 = { state = "IGNITION", label = "Ignition phase 2" }
4 = { state = "RUNNING", label = "Working" }
6 = { state = "SHUTDOWN", label = "Final cleaning" }
7 = { state = "STANDBY", label = "Standby (energy saving)" }
# codes 5, 8+ not observed: they decode as UNKNOWN

[commands.start]
writes = [{ memory = "ram", address = 0x21, value = 0x01 }]
verified = false   # Step 5

[commands.stop]
writes = [{ memory = "ram", address = 0x21, value = 0x06 }]
verified = true    # 2026-10-21: 7 -> 6 -> 0 in 3 min 50 s

[clock.live]       # RAM, binary, read in every status batch
memory = "ram"
second = 0x7A
weekday = 0x7B
hour = 0x7C
minute = 0x7D
day = 0x7E
month = 0x7F
year = 0x80        # years since 2000

[clock.set]        # EEPROM, BCD, written by sync_clock
memory = "eeprom"
weekday = 0xF8     # encoding from Step 6 (Wednesday read as 4, Thursday as 5)
hour = 0xF9
minute = 0xFA
day = 0xFB
month = 0xFC
year = 0xFD
verified = false   # Step 6
```

Chrono layout (mapped and write-back verified on 2026-10-21 through the reset of each block; the always-1 bytes are deliberately absent):

```toml
[chrono]
time_unit_min = 10
unset_value = 144
day_mask_bits = "mon=0,tue=1,wed=2,thu=3,fri=4,sat=5,sun=6"

[[chrono.blocks]]
kind = "day"
enable = 0x50
allowed_days = []            # no day mask: every day
verified = true
programs = [
  { start = 0x51, stop = 0x52, power = 0x53, setpoint = 0x54 },
  { start = 0x56, stop = 0x57, power = 0x58, setpoint = 0x59 },
]

[[chrono.blocks]]
kind = "week"
enable = 0x5B
allowed_days = ["mon", "tue", "wed", "thu", "fri"]
verified = true
programs = [
  { start = 0x5C, stop = 0x5D, days = 0x5E, power = 0x5F, setpoint = 0x60 },
  { start = 0x62, stop = 0x63, days = 0x64, power = 0x65, setpoint = 0x66 },
]

[[chrono.blocks]]
kind = "weekend"
enable = 0x68
allowed_days = ["fri", "sat", "sun"]
verified = true
programs = [
  { start = 0x69, stop = 0x6A, days = 0x6B, power = 0x6C, setpoint = 0x6D },
  { start = 0x6F, stop = 0x70, days = 0x71, power = 0x72, setpoint = 0x73 },
]
```

The `verified = true` flags on the chrono blocks ship only once the user confirms the mapping at the Step 8 checkpoint; otherwise keep them `false`.

- [x] **Step 8: Go/No-Go (Go: RAM start verified live on 2026-10-21/22).** If the RAM start fails, manual start and the suspension's thermostat start are blocked on the real stove (the simulator still works; the API reports `capabilities.start = false`). The native chrono stays viable, because it lives in EEPROM, which is verified writable. Open a sub-plan for the ESP32 fallback only if a remote start turns out to be needed.
- [x] **Checkpoint:** the user validates the mapping.

---

## Phase 1 - Common base

### Task 5: `core/stove` models and profile loader

**Files:**
- Create: `src/stove_pilot/core/__init__.py`, `src/stove_pilot/core/stove/{__init__,models,profile,messages}.py`, `src/stove_pilot/core/stove/profiles/{__init__.py,superior_maira.toml}`

**Interfaces:**
- Produces:
  - `StoveState(StrEnum)`: `OFF, IGNITION, RUNNING, CLEANING, SHUTDOWN, STANDBY, ALARM, UNKNOWN`
  - `StoveStatus(frozen)`: `state, raw_state_code, state_label, ambient_temp_c, fumes_temp_c, setpoint_c, power_level, display_text, display_message, alarm_code, alarm, energy_saving, stove_time, clock_drift_s, polled_at`. The new fields are optional (`None` when not available).
  - `EnergySavingStatus(frozen)`: `enabled, stop_offset_c: int | None, start_offset_c: int | None, restart_below_c: float | None` (`None` offset = OFF; `restart_below_c` = setpoint - start offset).
  - `ClockStatus(frozen)`: `stove_time, server_time, drift_s, auto_sync: bool, last_sync_at: datetime | None`. `MaintenanceInfo(frozen)`: `total_hours, partial_hours, ignitions, hours_to_service, last_alarms: tuple[str, ...]`.
  - `messages.py` (pure, no I/O): `DisplayMessage(code, level: Literal["info", "warning", "error"], title_fr, action_fr)`, `AlarmInfo(code, title_fr, action_fr, requires_technician)`, `ALARMS: Mapping[str, AlarmInfo]` (E1, E2, E3, E4, E6, E7, E8, E9, E10, E12, E13, E14, E19, from design section 1.3), and `parse_display(text: str) -> DisplayMessage | None`. `parse_display` does a best-effort keyword match on the uppercased, space-collapsed buffer, because the display scrolls and a single read can be shuffled. Keywords, checked in this order: `VIDER` (error, before `NETTOYAGE`), `FERMER` (error), `SERVICE` (warning), `ENERGY OK` / `ENERGY` + `OK` (info), `REFROID` (info), `MANQUE DE TENSION` (warning), `NETTOYAGE` (info), `NO CHARGE` (warning), `CLAVIER BLOQUE` (info), and an `E<n>` alarm token (error, from `ALARMS`). The French texts may hold accents (UTF-8) because they are UI copy.
  - `CommandResult(StrEnum)`: `APPLIED, NO_OP, DEFERRED`. `CommandOutcome(frozen)`: `result, reason, status`. `EventSource(StrEnum)`: `USER, CHRONO, POLLER, SYSTEM` (rev 4: no `SCHEDULE`)
  - `RegisterDef(name, memory, address, scale=1.0, offset=0.0, writable=False, min_value, max_value, verified=False)` with `decode(raw) -> float` and `encode(value) -> int`
  - `WriteOp(memory, address, value)`, `CommandRecipe(writes, verified)`, `StateDef(code, state, label)`, `DisplayDef(memory, start, length)`
  - `StoveProfile(name, registers, states, commands, display, clock: ClockDef | None, counters: Mapping[str, CounterDef])` with `register(name)`, `has_register(name) -> bool`, and `decode_state(code) -> StateDef` (unknown codes give `UNKNOWN`). `ClockDef(live: dict[str, int] on RAM, set: dict[str, int] on EEPROM BCD, set_verified: bool)`. `CounterDef(memory, address, bytes, endian)`. Optional registers (`energy_stop_offset`, `energy_start_offset`, `display_eco`) may be absent; `RegisterDef` gains `off_value: int | None` (the raw value meaning OFF).
  - `StoveProfile` also carries `allow_unverified: bool` (default `False`). It is only a recorded flag: the loader does not enforce it.
  - `load_profile(name: str, *, allow_unverified: bool = False) -> StoveProfile` (`tomllib` + `importlib.resources`; raises `ConfigurationError` on an unknown name or missing `ambient_temp`/`state`). It does NOT raise for `writable=true` with `verified=false`, so a read-only live status works on the skeleton TOML. Composition passes `allow_unverified=(settings.gateway == "simulator")`; the `verified` gate applies only with `STOVE_PILOT_GATEWAY=navel` and is enforced at write time by `StoveGateway` (Task 6).
- [x] **Step 1: Implement.**
- [x] **Manual verification:** `python -c "from stove_pilot.core.stove.profile import load_profile; p = load_profile('superior_maira'); print(p.register('ambient_temp').decode(34), p.decode_state(99).state, p.decode_state(2).state)"` prints `17.0 UNKNOWN IGNITION`. `python -c "from stove_pilot.core.stove.messages import parse_display; print(parse_display('  ENERGY  OK '), parse_display('VIDER LE BRASERO'), parse_display('SERVICE OFF'))"` prints an info, an error, and a warning message.
- [x] **Checkpoint.**

### Task 6: `RegisterIO` port, Navel adapter, gateway, simulator

**Files:**
- Create: `src/stove_pilot/core/stove/{ports,service,simulator}.py`
- Modify: `src/stove_pilot/service/composition.py`

**Interfaces:**
- Produces:
  - `RegisterIO(Protocol)`: `read(targets: Sequence[tuple[Memory, int]]) -> list[int]`, `write(ops: Sequence[WriteOp]) -> None`
  - `NavelRegisterIO(client: NavelClient)` (encodes with `encode_item` and splits reads into chunks of 8; module limit: larger batches return blank replies)
  - `SimulatedRegisterIO(seed: Path | None, clock)`: RAM/EEPROM byte arrays seeded from a CLI dump JSON (`{ "ram": {...}, "eeprom": {...} }`), with `set_ram(address: int, value: int) -> None` to adjust the simulated RAM directly (verification scripts use it). A start write on the state register moves through the observed codes 1 (30 s), 2 (7 min), and 3 (2 min) to RUNNING (4). A stop write moves through 6 (4 min) to OFF (0). The clock bytes tick and the simulated clock runs 20 s per hour fast. The ambient temperature drifts toward the setpoint while running, and the stove goes to 7 when the ambient exceeds the setpoint. The display buffer shows the panel texts of design section 1.2.
  - `StoveGateway(io, profile, clock, tz, *, allow_unverified: bool = False)`: `read_status() -> StoveStatus` (one batched read including the display bytes, the RAM live clock, and the ENERGY SAVING offsets when mapped; it fills `display_message` with `parse_display`, `alarm` from `ALARMS`, `stove_time`, and `clock_drift_s`), `read_counters() -> MaintenanceInfo | None` (`None` when the profile has no counters), `write_clock(local: datetime) -> list[str]` (writes only the BCD fields that differ, returns their names; refused like an unverified register while `clock.set_verified` is false on `gateway=navel`), `write_register(name, value) -> bool` (read-before-write; `False` when unchanged; `ValidationFailedError` out of range or not writable; `UnverifiedRegisterError(name)` when the register is not `verified` and `allow_unverified` is false), `execute(command: str) -> None` (same refusal when the command recipe is not `verified`). When `allow_unverified` is true, each write to an unverified register or recipe logs one WARNING that names it (never any secret). Reads are never gated.
  - `composition.build_register_io(settings) -> RegisterIO` (`simulator` or `navel`; unaffected by `allow_unverified`). `composition` builds the gateway with `allow_unverified=(settings.gateway == "simulator")`.
- [x] **Step 1: Implement.**
- [x] **Manual verification:** a script using `build_register_io` with `STOVE_PILOT_GATEWAY=simulator` and `STOVE_PILOT_SIMULATOR_SEED=discovery/00-baseline.json` (the `{ "ram", "eeprom" }` snapshot, not the CLI dump `02-cli-baseline.json` unless it has the same format) prints a `StoveStatus` with 17.0 C and display text "ENERGY...". With `gateway=navel` (read only), it prints the live status even though the skeleton TOML files are still `verified = false`; a `write_register("setpoint", ...)` with `gateway=navel` on an unverified register must raise `UnverifiedRegisterError`.
- [x] **Checkpoint.**

### Task 7: `lib/sqlite` and `core/history`

**Files:**
- Create: `src/stove_pilot/lib/sqlite.py`, `src/stove_pilot/core/history/{__init__,models,service,repository}.py`

**Interfaces:**
- Produces:
  - `SqliteDatabase(path)` with `connection()` (WAL, `sqlite3.Row`, lock, commit/rollback) and `migrate(name, version, statements)` (table `schema_versions(name, version)`)
  - `TemperatureSample(at, ambient_c, fumes_c, state)`, `StoveEvent(at, kind, source, detail)`
  - `HistoryStore(db)`: `insert_sample`, `insert_event`, `samples(start, end)`, `events(limit)`, `last_event(kind, **match)`, `purge_before(cutoff)`
  - `HistoryRepository(store, clock)`: `record_sample(status)`, `record_event(kind, source, **detail)`, `temperatures(start, end, bucket_min)`, `recent_events(limit)`, `last_ignition_at()`, `purge(retention_days)`

```sql
CREATE TABLE IF NOT EXISTS temperature_samples (
  at TEXT PRIMARY KEY, ambient_c REAL, fumes_c REAL, state TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS stove_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT NOT NULL, kind TEXT NOT NULL,
  source TEXT NOT NULL, detail TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS ix_stove_events_at ON stove_events(at);
```

- [x] **Step 1: Implement.**
- [x] **Manual verification:** a script records 3 samples and 1 event, then prints `temperatures(..., bucket_min=10)` and `recent_events(5)`. Inspect the DB with `sqlite3 data/stove-pilot.sqlite ".tables"`.
- [x] **Checkpoint.**

### Task 8: Command rules, `StoveRepository`, `StatusPoller`

**Files:**
- Create: `src/stove_pilot/core/stove/{rules,repository,poller}.py`
- Modify: `src/stove_pilot/common/settings.py` (add `clock_auto_sync: bool = True`, `clock_sync_min_interval_h: int = 6`, `deferred_stop_expiry_min: int = 30`, `live_read_min_interval_s: int = 5`; rev 4: remove `schedule_tick_s` and `catch_up_min`, and change `features` to `set[Literal["chrono"]] = {"chrono"}`) and `.env.example` (same changes, `STOVE_PILOT_FEATURES=["chrono"]`). Agents do not edit `.env`: the checkpoint reminds the user to replace `STOVE_PILOT_FEATURES=["chrono","schedule"]` with `["chrono"]` and delete `STOVE_PILOT_SCHEDULE_TICK_S`, otherwise settings validation fails at startup.

**Interfaces:**
- Produces: `Decision`, `GuardResult`, `GuardPolicy`, `decide_start(status, *, last_ignition_at, now, policy)`, `decide_stop(status, *, source: EventSource, confirm_ignition_stop: bool = False)`. `StoveRepository(gateway, history, policy, clock, tz, stale_after, deferred_expiry, deferred_stop_expiry, clock_tolerance_s, clock_sync_min_interval, clock_auto_sync)` with:
  - `refresh`, `get_status(max_age_s: float | None = None)` (returns the cached status when it is younger than `max_age_s`, otherwise does one live `refresh()`. Live refreshes are serialized by the repository lock and never closer than `live_read_min_interval_s`, so concurrent UI ticks share one read. A gateway error during a live refresh returns the cached status marked stale instead of raising), `is_stale`, `start(*, source)`, `stop(*, source, confirm_ignition_stop: bool = False)`, `set_setpoint(value_c, *, source)`, `set_power(level, *, source)`, `pending_start()`, `pending_stop()`, `process_pending()`;
  - `clock_status() -> ClockStatus`, `sync_clock(*, force: bool = False, source: EventSource = SYSTEM) -> ClockStatus` (writes only when `force` or the drift exceeds the tolerance; records a `clock_synced` event with the drift before and after);
  - `settings() -> StoveSettings` (energy saving and display eco values plus `writable` flags; `None` parts when unmapped);
  - `set_energy_saving(*, stop_offset_c: int | None, start_offset_c: int | None, source)` (1..3 or `None` = OFF, written through `off_value`; `ValidationFailedError` out of range; `UnverifiedRegisterError` when unmapped or unverified on `gateway=navel`);
  - `set_display_eco(enabled: bool, *, source)`;
  - `maintenance() -> MaintenanceInfo | None` (read on demand, never polled; `hours_to_service = max(0, 2000 - partial_hours)`).
  - Every setting write is read-before-write and records a `setting_changed` event.
- `StatusPoller(repository, history, interval_s, sample_interval_s, retention_days, clock, stop_event)` with `run_once()`, `run_forever()`, and a `last_poll_at` attribute. After each successful poll it calls `repository.sync_clock()` when `clock_auto_sync` is on, the drift exceeds the tolerance, and the last sync is older than `clock_sync_min_interval_h`. A sync failure is logged, never raised.

```python
# src/stove_pilot/core/stove/rules.py
"""Pure guards protecting the stove from unsafe or useless commands."""

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum

from stove_pilot.core.stove.models import StoveState, StoveStatus


class Decision(StrEnum):
    """Outcome of a command guard."""

    APPLY = "apply"
    NO_OP = "no_op"
    DEFER = "defer"
    REJECT = "reject"


@dataclass(frozen=True)
class GuardResult:
    """Guard decision with a machine-readable reason."""

    decision: Decision
    reason: str | None = None


@dataclass(frozen=True)
class GuardPolicy:
    """Tunable safety limits."""

    min_restart_interval: timedelta


_ALREADY_ON = {StoveState.IGNITION, StoveState.RUNNING, StoveState.CLEANING, StoveState.STANDBY}


def decide_start(
    status: StoveStatus, *, last_ignition_at: datetime | None, now: datetime, policy: GuardPolicy
) -> GuardResult:
    """Decide whether a start command may be sent now.

    :param status: Fresh stove status.
    :param last_ignition_at: Last applied start, if any.
    :param now: Current time (aware).
    :param policy: Safety limits.
    :return: Guard decision.
    """
    if status.state in _ALREADY_ON:
        return GuardResult(Decision.NO_OP, "already_on")
    if status.state is StoveState.SHUTDOWN:
        return GuardResult(Decision.DEFER, "cooling_down")
    if status.state is not StoveState.OFF:
        return GuardResult(Decision.REJECT, f"state_{status.state.value.lower()}")
    if last_ignition_at and now - last_ignition_at < policy.min_restart_interval:
        return GuardResult(Decision.DEFER, "min_restart_interval")
    return GuardResult(Decision.APPLY)


def decide_stop(
    status: StoveStatus, *, source: EventSource, confirm_ignition_stop: bool = False
) -> GuardResult:
    """Decide whether a stop command may be sent now.

    :param status: Fresh stove status.
    :param source: Who asks for the stop.
    :param confirm_ignition_stop: User confirmed a stop during ignition.
    :return: Guard decision.
    """
    if status.state is StoveState.IGNITION:
        # Reason: a stop during ignition forces an on-site brazier emptying (manual, VIDER BRASERO).
        if source is not EventSource.USER:
            return GuardResult(Decision.DEFER, "ignition_in_progress")
        if not confirm_ignition_stop:
            return GuardResult(Decision.REJECT, "confirm_ignition_stop_required")
        return GuardResult(Decision.APPLY)
    if status.state in _ALREADY_ON:
        return GuardResult(Decision.APPLY)
    if status.state in {StoveState.OFF, StoveState.SHUTDOWN}:
        return GuardResult(Decision.NO_OP, "already_off")
    return GuardResult(Decision.REJECT, f"state_{status.state.value.lower()}")
```

(`EventSource` is imported from `core/stove/models.py`.)

Repository behavior:
- Each command takes an `RLock`, does a live `refresh()`, applies the guard, executes, refreshes again, records a `command` event, and returns `CommandOutcome`.
- `REJECT` raises `CommandRejectedError` after the event is recorded.
- A `DEFER` on start stores a pending start with an expiry. A `DEFER` on stop stores a pending stop (expiry `deferred_stop_expiry_min`).
- `process_pending()` (called by the poller) retries when allowed. A pending stop is applied as soon as the state leaves `IGNITION`.
- `stop()` clears the pending start, and `start()` clears the pending stop.
- `refresh()` records `state_changed` events.
- The poller swallows and logs `GatewayError`, so the status goes stale instead of crashing.

- [x] **Step 1: Implement.**
- [x] **Manual verification (simulator):** the simulator seed holds state code 7 (STANDBY), so the script must first call `set_ram(0x21, 0)` on `SimulatedRegisterIO` to begin from OFF. Then:
  - start from OFF (`applied`, state IGNITION), then start again (`no_op`);
  - a user stop during IGNITION without confirmation raises `CommandRejectedError("confirm_ignition_stop_required")`, and a `source=SYSTEM` stop returns `deferred`;
  - advance the simulator clock by 10 min (RUNNING), call `process_pending()`: the deferred stop is applied (SHUTDOWN);
  - start again (`deferred`, cooling or min interval), advance the clock by 16 min, call `process_pending()` (`applied`);
  - `clock_status()` shows the simulated drift, and `sync_clock(force=True)` brings it under 60 s.
- [x] **Checkpoint.**

---

## Phase 2 - Feature A: native chrono (`core/chrono`)

### Task 9: Chrono layout, models, validation

**Files:**
- Create: `src/stove_pilot/utilities/{__init__,weektime}.py`, `src/stove_pilot/core/chrono/{__init__,models,layout,validation}.py`, `src/stove_pilot/core/chrono/layouts/{__init__.py,superior_maira.toml}`

**Interfaces:**
- Produces:
  - `utilities/weektime.py` (common base): `Weekday(IntEnum)` `MON=0..SUN=6` with `key` (`"mon"`), `MINUTES_PER_DAY`, `MINUTES_PER_WEEK`, `parse_hhmm(text) -> int` (accepts `"24:00"`), `format_hhmm(minutes) -> str`, `minute_of_week(local) -> int`, `to_bcd(value) -> int`, `from_bcd(value) -> int`
  - `ChronoBlockKind(StrEnum)`: `DAY, WEEK, WEEKEND`. `ProgramLayout(start, stop, power, setpoint, days: int | None)`, `BlockLayout(kind, enable, allowed_days: frozenset[Weekday], programs: tuple[ProgramLayout, ...], verified)`, `ChronoLayout(time_unit_min, unset_value, blocks: tuple[BlockLayout, ...], allow_unverified: bool = False)` with `load_chrono_layout(name: str, *, allow_unverified: bool = False) -> ChronoLayout` and `all_addresses() -> list[int]` (EEPROM, layout fields only, so the always-1 bytes are never read for writing). An empty `allowed_days` in TOML means every day. The day mask uses bit 0 = Monday through bit 6 = Sunday. Like the profile loader, it never raises on `verified = false`; it only records `allow_unverified` on the layout. An unknown layout name still raises `ConfigurationError`. The clock is NOT in the chrono layout (it moved to the stove profile, Task 5).
  - `ChronoProgram(block, index, start_min: int | None, stop_min: int | None, days: frozenset[Weekday] | None, setpoint_c, power_level)` (None times mean unset, which the panel shows as OFF; `days` is None for the day block)
  - `ChronoBlock(kind, enabled, allowed_days, programs: tuple[ChronoProgram, ...])`
  - `ChronoConfig(blocks: tuple[ChronoBlock, ...], fingerprint: str)` with the property `enabled` (any block enabled)
  - `decode_config(layout, raw: Mapping[int, int]) -> ChronoConfig` and `encode_config(layout, config) -> dict[int, int]` (pure, in `layout.py`)
  - `effective_week(config) -> dict[Weekday, list[tuple[int | None, int | None, ChronoBlockKind, int]]]` (pure: what runs each day, for the preview and the summary)
  - `validate_chrono(config, layout, setpoint_range, power_range) -> ChronoValidation(issues, warnings)`:
    - issue codes: `granularity`, `order` (`start == stop` when both are set; `start > stop` is an overnight program, accepted with the `overnight` warning), `days_not_allowed`, `overlap` (programs of enabled blocks on the same day), `setpoint_range`, `power_range`, `unverified_layout`;
    - warning codes: `gap_short` (< 120 min between a stop and the next start on the same day), `start_off`, `stop_off`.
    - `allow_unverified` is read from `layout.allow_unverified` (no extra parameter). `unverified_layout` is reported when any written field of a block is unverified and `layout.allow_unverified` is false (422 in the API).
- [x] **Step 1: Implement.** The fingerprint is the SHA-256 of the sorted raw bytes, truncated to 16 hex characters.
- [x] **Manual verification:**
  - Decode `discovery/05-chrono-before.json` and print the blocks (all disabled, programs unset, power 3, setpoint 20 expected), then check that `encode_config(decode_config(raw)) == raw` for the layout addresses.
  - Decode `discovery/12-chrono-weekend-set.json`: weekend enabled, P1 21:50/21:40 on Saturday, P2 21:30/21:20 on Friday and Sunday, setpoint 9; `validate_chrono` reports `order` issues for these test values.
  - Snapshot files come in three shapes, and the script must accept all of them (convert the keys to int EEPROM addresses):
    - `{ "ram": {...}, "eeprom": {...} }` (full dumps such as `00-baseline.json`);
    - a FLAT `{ "0x00": v, ... }` EEPROM map (`01-eeprom-after-setpoint17.json`);
    - zone reads `{ "eeprom:0x40": v, ... }` (`05` to `13`).
- [x] **Checkpoint.**

### Task 10: Chrono gateway, repository, clock sync

**Files:**
- Create: `src/stove_pilot/core/chrono/{service,repository}.py`

**Interfaces:**
- Consumes: `RegisterIO`, `StoveProfile` (ranges), `HistoryRepository`, `ChronoLayout` (its `allow_unverified` flag, set by composition from `settings.gateway == "simulator"`), and a `clock_sync: Callable[[], object]` injected by composition (bound to `StoveRepository.sync_clock`).
- Produces:
  - `ChronoGateway(io, layout)`: `read_raw() -> dict[int, int]`, `write_bytes(changes: Mapping[int, int]) -> None`. No clock methods: the clock lives in `core/stove` (Task 6 and Task 8).
  - `ChronoRepository(gateway, layout, profile, history, clock, tz, clock_sync: Callable[[], object], *, is_suspended: Callable[[], bool] = lambda: False, allow_unverified: bool = False, cache_ttl_s: int = 300)`:
    - `get(max_age_s: float | None = None) -> ChronoConfig` (served from a cache younger than `cache_ttl_s`, or younger than `max_age_s` when given; save, block toggles, suspend, and resume invalidate it);
    - `fingerprint(max_age_s: float | None = None) -> str`, `validate(config) -> ChronoValidation`, `save(config, *, expected_fingerprint) -> ChronoConfig`;
    - `is_enabled() -> bool` (cached), `set_block_enabled(kind: ChronoBlockKind, enabled: bool) -> ChronoConfig` (raises `FeatureConflictError("programming_suspended")` when enabling while `is_suspended()` is true; `is_suspended: Callable[[], bool]` is injected by composition, default `lambda: False`);
    - `suspend() -> dict` (writes 0 to the enabled blocks' enable bytes, read-before-write, and returns `{"enabled_blocks": [...]}`), `resume(snapshot: dict) -> None` (writes 1 to the listed blocks only). Each one records a `chrono_suspended` / `chrono_resumed` event; `summary(now) -> ProgrammingSummary` (current program if one is running now according to `effective_week`, else the next start within 8 days; `ProgrammingSummary` is defined in the feature: `active, current: (start, stop, setpoint_c, power_level) | None, next_at: datetime | None, blocked_reason`)

`save` algorithm:
1. `read_raw()`. If its fingerprint differs from `expected_fingerprint`, raise `VersionConflictError`.
2. Validate and raise `ValidationFailedError` on issues (including `unverified_layout` when the layout is unverified and `allow_unverified` is false). When `allow_unverified` is true, log one WARNING per write naming the unverified field (never any secret).
3. If a block goes from disabled to enabled while `is_suspended()` is true, raise `FeatureConflictError("programming_suspended")` (program contents may still change).
4. `encode_config`, then write only the changed bytes.
5. Read back and compare. Raise `GatewayProtocolError` on a mismatch. An empty module reply on a write is never retried blindly: read the byte back, then rewrite only if it differs.
6. Call `clock_sync()` (it writes only when the drift exceeds the tolerance). A clock sync failure is logged and does not fail the save.
7. Record a `chrono_saved` event (`changed_bytes` count, source `USER`) and return the new config.

- [x] **Step 1: Implement.**
- [x] **Manual verification (simulator):**
  - Save a week-block program 1 Mon-Fri 06:30 to 08:00 at 20 C, print the changed bytes (block enable, start 39, stop 48, days 31, setpoint), call `get()` again, and check that it round-trips.
  - Saving with a stale fingerprint raises `VersionConflictError`. Saturday in the week block raises `days_not_allowed`.
  - `suspend()` returns `{"enabled_blocks": ["week"]}` and leaves the program bytes unchanged. `resume()` with that snapshot restores the enable byte. Two `get()` calls within 5 min issue one EEPROM read.
- [x] **Manual verification (attended, real stove):** first check, read only, that with `gateway=navel` and an unverified layout `get()` works but `save()` raises `ValidationFailedError` with the `unverified_layout` issue. Then, after the layout fields are marked `verified = true`, save the same program, check it on the panel chrono menu (SEMAINE), then reset it with the user's go.
  > DONE 2026-10-22: `save()` wrote 8 bytes (week P1 06:30-08:00 and P2 18:00-22:00, Mon-Fri), the user confirmed the panel, and the reset restored fingerprint `50d72c714ace67b4`. Finding: the week block also accepts Sat and Sun, so `allowed_days` was widened.
- [x] **Checkpoint.**

---

## Phase 3 - Feature B: server schedule (deferred, rev 4)

Tasks 11 and 12 are **not implemented in the MVP**. Their content is kept in Appendix A for a later revival. Do not create `core/schedule`.

---

## Phase 4 - REST API (`service/api`)

### Task 13: API core (factory, auth, errors, feature registry, health/features/stove/history)

**Files:**
- Create: `src/stove_pilot/service/app.py`, `src/stove_pilot/service/features.py`, `src/stove_pilot/service/api/{__init__,auth,errors}.py`, `src/stove_pilot/service/api/routers/{__init__,health,features,stove,settings,summary,history}.py`, `src/stove_pilot/service/api/schemas/{__init__,common,stove,settings,summary,history}.py`, `src/stove_pilot/server_api.py`
- Modify: `src/stove_pilot/service/composition.py`

**Interfaces:**
- Produces:
  - `create_api_app(*, settings, get_stove_repo, get_history_repo, get_health, feature_registrars: Sequence[Callable[[Blueprint], None]], summary_providers: Sequence[Callable[[datetime], ProgrammingSummaryDto]]) -> Flask`
  - `create_api_app` receives `feature_registrars` and `summary_providers` as plain callables. The caller (`server_api.main`) builds them from each enabled `FeatureSpec` with `functools.partial(spec.register_api, container=container)` and `functools.partial(spec.summary, container)`, so the app factory never sees `Container` and the summary router never imports a feature.
  - `features.py`: `FeatureSpec(name, label_fr, register_api: Callable[[Blueprint, Container], None], start_threads: Callable[[Container, threading.Event], list[threading.Thread]] | None, summary: Callable[[Container, datetime], ProgrammingSummaryDto] | None)` and `FEATURES: dict[str, FeatureSpec]` (empty in Task 13, filled in Task 14). `start_threads` stays in the contract (unused by the chrono) so a feature with a background runner can be added later. `Container` is a small dataclass in `composition.py` holding the built repositories (`chrono: ChronoRepository | None`, `stove`, `history`). The `chrono` field is declared here and stays `None` until Task 14.
  - `composition.build_container(settings) -> Container` wires ONLY `stove` (gateway built with `allow_unverified=(settings.gateway == "simulator")`) and `history` in this task. Task 14 adds `build_chrono_repository` and the suspension wiring to it.

```python
def build_container(settings: Settings) -> Container:
    """Build the stove and history repositories (features are wired in Task 14).

    :param settings: Application settings.
    :return: Composed container with ``chrono`` set to ``None``.
    """
    history = build_history_repository(settings)
    stove = build_stove_repository(settings, history)
    return Container(stove=stove, history=history, chrono=None)
```

  - `server_api.main()`: logging, secret registration (when set), a lock file `<db_dir>/api.lock`, `build_container`, start the poller plus each feature's threads, build the feature registrars with `functools.partial`, `waitress.serve(app, host, port, threads=4)`, and a graceful stop on SIGINT/SIGTERM. When `api_key` is empty it logs exactly one WARNING at startup: "API key disabled, API must stay bound to localhost or a private Docker network".
  - DTOs:
    - `StoveStatusDto` (+ `display_text`, `display_message {code, level, title, action} | null`, `alarm {code, title, action, requires_technician} | null`, `energy_saving {enabled, stop_offset_c, start_offset_c, restart_below_c} | null`, `stove_time`, `clock_drift_s`, `stale`, `capabilities {start, stop, setpoint, power, energy_saving, display_eco, clock_sync}` as booleans, true when the corresponding write is allowed: the register or command recipe is `verified`, or `allow_unverified` is on);
    - `CommandOutcomeDto`, `StopRequest {confirm_ignition_stop: bool = false}` (optional body), `SetpointRequest`, `PowerRequest`, `ProblemDto`, `TemperaturePointDto`, `EventDto`;
    - `ClockStatusDto`, `StoveSettingsDto`, `EnergySavingRequest {stop_offset_c: int | None, start_offset_c: int | None}`, `DisplayEcoRequest {enabled}`, `MaintenanceDto`;
    - `ProgrammingSummaryDto {feature, label, active, current: {start, stop, setpoint_c, power_level} | null, next: {at, action} | null, blocked_reason}` (in `schemas/summary.py`, the only shared contract);
    - `SummaryDto {stove: StoveStatusDto, alert: {level, title, action} | null, programming: list[ProgrammingSummaryDto]}`. `alert` is the alarm if any, else the display message, else the ENERGY SAVING wait ("Veille economie - rallumage automatique sous X C") when the state is `STANDBY`, else a clock warning when `abs(clock_drift_s) > tolerance`.
  - `GET /stove/status` and `GET /summary` accept `max_age_s` (float, `0` = force a live read, subject to `live_read_min_interval_s`) and pass it to `StoveRepository.get_status`. `SummaryDto` gains `suspension {suspended, since, until, thermostat_mode} | null` (always `null` in Task 13; Task 14 wires the provider through an optional `get_suspension` getter passed to `create_api_app`).
  - Extra endpoints (design section 7): `GET /summary`, `GET /stove/clock`, `POST /stove/clock/sync`, `GET /stove/settings`, `PUT /stove/settings/energy-saving`, `PUT /stove/settings/display-eco`, `GET /stove/maintenance` (404 `not_mapped` while the profile has no counters). `POST /stove/commands/stop` accepts the optional `StopRequest` body. `CommandRejectedError("confirm_ignition_stop_required")` gives 409 `command_rejected` with that reason.

Rules: `X-API-Key` is optional. When `api_key` is empty, the check is skipped (one startup WARNING, see above). When set, it is checked with `hmac.compare_digest` (`/health` exempt). The API stays bound to `127.0.0.1` by default and its port is not published in Docker. Problem+json errors (`CommandRejectedError` gives 409 `command_rejected`, `UnverifiedRegisterError` gives 409 `unverified_register`, `FeatureConflictError` gives 409 `feature_conflict`, `ValidationFailedError` gives 422 with `issues`, `VersionConflictError` gives 412, `GatewayError` gives 503, a Pydantic error gives 422); a start/stop rate limit of 6 per 10 min (429). `GET /features` returns `{name: name in settings.features}` for every `FEATURES` key (today only `chrono`).

- [x] **Step 1: Implement.**
- [x] **Manual verification (simulator):** `stove-pilot-api`, then:
  - `curl http://127.0.0.1:8710/api/v1/health` returns 200.
  - With an empty `STOVE_PILOT_API_KEY`: `curl .../stove/status` returns the DTO (with `capabilities`), and the log shows exactly one "API key disabled" WARNING at startup. With `STOVE_PILOT_API_KEY=test-key`: `curl -H "X-API-Key: test-key" .../stove/status` returns the DTO and a call without the key returns 401 (`/health` stays open).
  - `curl -X POST .../stove/commands/start` returns `applied`, then `no_op` (the simulator seed holds state code 7, so set simulator RAM `0x21` to 0 first, for example through a `STOVE_PILOT_SIMULATOR_SEED` file with that value).
  - With `STOVE_PILOT_GATEWAY=navel` and the skeleton profile (unverified recipes), `curl -X POST .../stove/commands/start` returns `409 unverified_register` and sends nothing to the stove (run this check only while the recipe is still unverified).
  - `curl -X PUT ... /stove/setpoint -d '{"value_c": 99}'` returns 422.
  - On the simulator, during IGNITION, `curl -X POST .../stove/commands/stop` returns `409 confirm_ignition_stop_required`, and the same call with `{"confirm_ignition_stop": true}` returns `applied`.
  - `curl .../summary` returns `stove`, `alert` (the ENERGY SAVING wait on the seed state 7), and an empty `programming` list (no feature wired yet). `curl .../stove/clock` shows the simulated drift, and `POST .../stove/clock/sync` brings it under 60 s.
  - `curl .../stove/settings` returns `null` parts while the Step 6a registers are not in the profile, and `GET .../stove/maintenance` returns 404 `not_mapped`.
  - `curl ".../summary?max_age_s=0"` twice within 5 s: the API log shows a single live read, and `polled_at` is identical. Without `max_age_s`, `polled_at` follows the 30 s poller.
- [x] **Checkpoint.**

### Task 14: Chrono router and programming suspension

**Files:**
- Create: `src/stove_pilot/service/api/routers/{chrono,programming}.py`, `src/stove_pilot/service/api/schemas/{chrono,programming}.py`, `src/stove_pilot/core/suspension/{__init__,models,ports,service,repository}.py`
- Modify: `src/stove_pilot/service/features.py` (one entry), `src/stove_pilot/service/composition.py` (`build_chrono_repository`, `build_suspension_repository`, and the participant wiring inside `build_container`), `src/stove_pilot/core/stove/poller.py` (an optional `on_poll: Callable[[], None]` hook, used for auto-resume and the override check)

**Interfaces:**
- Produces:
  - `core/suspension` (design section 6.5, common base, never imports a feature):
    - `ports.py`: `Suspendable(Protocol)` with `name: str`, `is_active() -> bool`, `suspend() -> dict`, `resume(snapshot: dict) -> None`;
    - `models.py`: `SuspensionState(suspended, since, until, thermostat_mode, snapshots: dict[str, dict])`, `SuspendOutcome(state, start_outcome: CommandOutcome | None, warnings: tuple[str, ...])`;
    - `service.py`: `SuspensionStore(db)` with `load() -> SuspensionState`, `save(state)` (table `programming_suspension`, one row, `snapshots_json`);
    - `repository.py`: `SuspensionRepository(store, participants: Sequence[Suspendable], stove: StoveRepository, history, clock)` with `get()`, `is_suspended()`, `suspend(*, until: datetime | None, thermostat_mode: bool) -> SuspendOutcome` (atomic: on a participant error, the participants already suspended are resumed and the error is re-raised; `FeatureConflictError("already_suspended")` when suspended; thermostat start only when the stove is `OFF`; warning `energy_saving_stop_off` when the energy saving STOP offset is OFF, `energy_saving_unmapped` when unknown), `resume() -> SuspensionState` (`FeatureConflictError("not_suspended")`), `check() -> None` (auto-resume when `until` is past; clears the state with a `suspension_overridden` event when a participant is active while suspended).

```sql
CREATE TABLE IF NOT EXISTS programming_suspension (
  id INTEGER PRIMARY KEY CHECK (id = 1), suspended INTEGER NOT NULL, since TEXT, until TEXT,
  thermostat_mode INTEGER NOT NULL, snapshots_json TEXT NOT NULL, updated_at TEXT NOT NULL);
```

  - `programming.py` router (common, registered by `create_api_app` when at least one participant exists): `GET /programming`, `POST /programming/suspend` (`SuspendRequest {until: datetime | None, thermostat_mode: bool = true}`), `POST /programming/resume`. DTOs: `SuspensionDto`, `SuspendOutcomeDto {state, start_outcome, warnings}`. `FeatureConflictError` maps to 409 with its reason.
  - `FeatureSpec` gains `participant: Callable[[Container], Suspendable] | None`. Composition builds the participants from the enabled specs (an adapter object exposing the feature repository's `is_enabled`/`suspend`/`resume`), builds `container.suspension`, injects `is_suspended=container.suspension.is_suspended` into the chrono repository (late-bound lambda, because the suspension is built after them), and passes `on_poll=container.suspension.check` to the poller.
  - `chrono.py`: `register_chrono_api(bp, container)` with `GET/PUT /chrono` (`ETag` = fingerprint, `If-Match` required, 428 if missing), `GET /chrono/fingerprint?max_age_s=` (cheap change detection for the open editor), and `PUT /chrono/blocks/<kind>/state`. The clock endpoints are common (`/stove/clock`, Task 13), not chrono ones. DTOs:
    - `ChronoProgramDto(index, start: "HH:MM" | None, stop, days: list["mon"..] | None, setpoint_c, power_level)`;
    - `ChronoBlockDto(kind: "day" | "week" | "weekend", enabled, allowed_days: list["mon"..], programs)`;
    - `ChronoConfigDto(enabled, blocks, warnings: list[{code, block, index, message}], capabilities)`, where `capabilities` is `{setpoint_range, power_range, writable}` and `writable` is true when the layout fields are verified or `allow_unverified` is on.
    - Writes on an unverified layout return `422` with the `unverified_layout` issue.
  - `summarize_chrono(container, now) -> ProgrammingSummaryDto` maps `ChronoRepository.summary(now)` to the shared DTO (label "Chrono poele"). It lives in the chrono router module.
  - Composition (this task): `build_chrono_repository` loads the layout with `allow_unverified=(settings.gateway == "simulator")`, passes the same flag to `ChronoRepository`, and injects `clock_sync=container.stove.sync_clock`. `build_container` builds the chrono repository only when `"chrono" in settings.features`; there is no conflict check (rev 4, a single programming feature).
  - `FEATURES = {"chrono": FeatureSpec("chrono", "Chrono poele", register_chrono_api, None, summarize_chrono, chrono_participant)}`. `chrono_participant` lives in the chrono router module.
- Only `routers/chrono.py` and `schemas/chrono.py` import `core.chrono`. `core/suspension` and `routers/programming.py` import no feature.

- [x] **Step 1: Implement.**
- [x] **Manual verification (simulator):**
  - `GET /chrono` shows the programs, the ETag, and `capabilities.writable` (true on the simulator, false on `gateway=navel` with the skeleton layout, where `PUT` returns `422 unverified_layout`).
  - On the simulator, `PUT` with a valid program returns 200 and a new ETag. `PUT` with the old ETag returns 412.
  - `STOVE_PILOT_FEATURES=[]` makes `/chrono` return 404, `/features` report `chrono: false`, `/summary` return an empty `programming` list, and `/programming` return 404 (no participant); the dashboard still works.
  - `GET /summary` lists one `programming` entry. With an enabled week-block program running now on the simulator, it shows `active: true` and `current`.
  - Suspension (simulator, the stove `OFF`, the week block enabled):
    - `POST /programming/suspend {"thermostat_mode": true}` disables the week block, starts the stove (`start_outcome.result = applied`), and `GET /summary` shows `suspension.suspended = true`;
    - `PUT /chrono/blocks/week/state {"enabled": true}` returns `409 programming_suspended`;
    - `POST /programming/resume` re-enables only the week block and does not stop the stove;
    - suspend with `until` 1 min ahead: the poller resumes automatically and records `programming_resumed`;
    - suspend, then re-enable the block directly in the simulator EEPROM: the next poll records `suspension_overridden`.
  - Check the coupling: `rg "core\.chrono" src/stove_pilot/core/stove src/stove_pilot/core/history src/stove_pilot/core/suspension src/stove_pilot/service/api/routers/programming.py src/stove_pilot/service/api/routers/summary.py` returns nothing, and `rg "core\.schedule|schedule_tick|catch_up_min|SCHEDULE" src/` returns nothing.
- [x] **Checkpoint:** the backend is feature-complete.

---

## Phase 5 - Web UI (`service/ui`, daisyUI)

### Task 15: daisyUI toolchain, layout, navigation, dashboard, glance widget

**Files:**
- Create: `stove-pilot/tools/{build-css.ps1,build-css.sh}`, `src/stove_pilot/service/ui/static/css/{input.css,app.css}`, `src/stove_pilot/service/ui/static/js/{htmx.min.js,refresh.js}` (vendored HTMX 2.x, plus the refresh controller), `src/stove_pilot/service/ui/{__init__,client,viewmodels,nav}.py`, `src/stove_pilot/service/ui/routes/{__init__,dashboard,widget}.py`, `src/stove_pilot/service/ui/i18n/fr.json` (including the key `common.unverified` = "Non verifie - etape de reperage requise"), `src/stove_pilot/service/ui/templates/{base,dashboard,widget}.html`, `src/stove_pilot/service/ui/templates/partials/{glance,controls,events,flash,refresh_selector,suspend_modal}.html`, `src/stove_pilot/server_ui.py`
- Modify: `src/stove_pilot/service/app.py` (`create_ui_app`), `src/stove_pilot/common/settings.py` (add `ui_frame_ancestors: str = "'self'"`, `ui_refresh_default_s: Literal[0, 10, 30, 60, 300] = 30`), `.env.example`

- [x] **Step 1: CSS toolchain** (`tools/build-css.ps1`; `build-css.sh` is equivalent):

```powershell
$ErrorActionPreference = "Stop"
$tools = $PSScriptRoot
$static = Join-Path $tools "..\src\stove_pilot\service\ui\static\css"
if (-not (Test-Path "$tools\tailwindcss.exe")) {
  Invoke-WebRequest "https://github.com/tailwindlabs/tailwindcss/releases/latest/download/tailwindcss-windows-x64.exe" -OutFile "$tools\tailwindcss.exe"
}
if (-not (Test-Path "$tools\daisyui.mjs")) {
  Invoke-WebRequest "https://github.com/saadeghi/daisyui/releases/latest/download/daisyui.mjs" -OutFile "$tools\daisyui.mjs"
  Invoke-WebRequest "https://github.com/saadeghi/daisyui/releases/latest/download/daisyui-theme.mjs" -OutFile "$tools\daisyui-theme.mjs"
}
& "$tools\tailwindcss.exe" -i "$static\input.css" -o "$static\app.css" --minify
```

```css
/* src/stove_pilot/service/ui/static/css/input.css */
@import "tailwindcss";
@source "../../templates";
@source "../js";
@plugin "../../../../../../tools/daisyui.mjs" {
  themes: caramellatte --default, coffee --prefersdark;
}
```

`app.css` is committed, so runtime needs no build. Rerun the script after adding new classes.

- [x] **Step 2: Interfaces**
  - `StovePilotApiClient(base_url, api_key: str = "", timeout_s=5.0)` (the `X-API-Key` header is sent only when `api_key` is non-empty) with `features()`, `status(max_age_s: float | None = None)`, `summary(max_age_s: float | None = None)`, `programming()`, `suspend(until: datetime | None, thermostat_mode: bool)`, `resume()`, `start()`, `stop(confirm_ignition_stop: bool = False)`, `set_setpoint(value_c)`, `set_power(level)`, `temperatures(start, end, bucket_min)`, `events(limit)`, `get(path) -> tuple[dict, str | None]`, `put(path, body, etag: str | None = None) -> tuple[dict, str | None]`, `post(path) -> dict`. Problems raise `ApiProblem(status, code, detail, issues)`. Feature pages use the generic `get`/`put`/`post`, so the client never names feature endpoints.
  - `nav.py`: `NavEntry(key, label_key, endpoint, icon)` and `NAV_ENTRIES` (`dashboard`, `chrono`, `history`), plus `visible_entries(features: dict[str, bool])`. `dashboard` and `history` are always visible.
  - `viewmodels.py`: `StatusView.from_api(payload)` (badge class `badge-neutral|warning|error|info|secondary` by state, French label, temperatures, `display_text`, stale flag, `is_ignition`, and the `capabilities` booleans `start`, `stop`, `setpoint`, `power`), `GlanceView.from_summary(payload)` (ambient, setpoint, fumes, power, state badge, stove time `HH:MM` plus a drift badge when `abs(drift) > 120 s`, display text, the alert with its daisyUI class `alert-info|alert-warning|alert-error`, and one French line per programming entry: "en cours 06:30-08:00, 20 C" / "prochain : demain 06:30" / blocked reason), and `sparkline_points(points, width, height) -> str`.
  - `create_ui_app(*, settings, get_api_client, ui_registrars: Sequence[Callable[[Flask], None]])`. Feature UI modules register their own blueprints through this list. There is no login: every UI page is open on the LAN. `ui_secret_key` is optional; when empty, `create_ui_app` uses `secrets.token_hex(32)` generated at startup (CSRF tokens become invalid after a restart, which is acceptable). Flask-WTF `CSRFProtect` stays enabled (HTMX sends the header). Templates and static files use package-relative `template_folder`/`static_folder`.
- [x] **Step 3: Templates** (daisyUI components):
  - `base.html`: `navbar` (desktop), `dock` (mobile, `lg:hidden`), nav from `visible_entries`, toast area, CSRF header for HTMX (`hx-headers='{"X-CSRFToken": "{{ csrf_token() }}"}'` on `<body>`), PWA manifest link.
  - Display refresh (design section 8.5):
    - `refresh.js` (about 80 LOC, no framework):
      - it reads the interval from `localStorage["stovePilot.refreshS"]`, falling back to `data-refresh-default` on `<body>` (from `settings.ui_refresh_default_s`); allowed values are 0 (manual), 10, 30, 60, and 300;
      - it dispatches `refresh-tick` on `document.body` every N s, pauses while `document.visibilityState === "hidden"`, and fires one tick on return to visible;
      - it exposes `window.stovePilotRefresh.now()`, which fires a tick with `max_age_s=0`;
      - on an `htmx:configRequest` for an element with `data-live`, it adds `max_age_s` (the interval, or 0 for a forced tick) to the request parameters;
      - it listens to the `refresh-now` event (sent by the UI server through `HX-Trigger` after any command, setting, suspend, or resume) and fires an immediate forced tick, then another one 5 s later;
      - it updates every `[data-polled-at]` element to "Mis a jour il y a X s / min" every second, adding `text-warning` when the age exceeds twice the interval (or immediately when the region reports `data-stale="true"`).
    - `partials/refresh_selector.html`: a daisyUI `dropdown` (clock icon, current value) with 10 s / 30 s / 1 min / 5 min / Manuel. Selecting a value writes `localStorage` and restarts the timer. Next to it, a `btn btn-ghost btn-sm` "Actualiser" button calls `stovePilotRefresh.now()`; it is always visible, and it is the only refresh in manual mode. `base.html` includes the selector in the navbar; Task 18 reuses it in Reglages for mobile.
    - Live regions declare `data-live hx-get="..." hx-trigger="refresh-tick from:body"` with `hx-swap="outerHTML"`. Each partial endpoint passes `max_age_s` through to the API client. Errors keep the previous content and only set `data-stale="true"`.
  - Suspension UI (design sections 6.5 and 8.1):
    - `partials/controls.html` shows "Suspendre les programmations" (`btn btn-outline`) or "Reprendre les programmations" (`btn btn-secondary`) from `GET /programming`, hidden when the API has no participant;
    - `partials/suspend_modal.html`: an optional "Jusqu'au" `datetime-local` field, and the checkbox "Passer en mode thermostat (allumer le poele s'il est eteint)", checked by default when the stove is `OFF` and disabled when the start capability is false (label `common.unverified`);
    - the response shows `start_outcome` (applied / deferred with the reason / no_op) and the warnings as toasts;
    - the glance card shows the suspension `alert-info` with since/until, and greys out the programming lines.
  - `partials/glance.html` (the at-a-glance card, design section 8.1): a large ambient temperature with the setpoint target, fumes, power, the state `badge`, the stove clock with its drift badge, the panel display text in a `kbd`-style mono box, one line per programming entry, and the alert. It takes a `compact` flag (one-line layout). It must not contain any control or form.
  - `dashboard.html`: the glance card as hero (live region `hx-get="/partials/glance"` with the refresh trigger above, plus a `[data-polled-at]` freshness line), then `partials/controls.html` (also a live region, because the buttons depend on the state):
    - `join` steppers for setpoint and power (`hx-trigger="change delay:1500ms"`);
    - a large `btn btn-primary` "Allumer" or a `btn btn-error` "Arreter" with a confirm `modal`. During ignition, the modal adds "Arreter pendant l'allumage imposera de vider le brasero sur place avant de rallumer." and sends `confirm_ignition_stop=true`.
    - Any action whose `capabilities` flag is false is rendered `disabled` with the French label from `fr.json` key `common.unverified`, "Non verifie - etape de reperage requise".
    - Then an inline SVG sparkline (refreshed every `max(N, 300)` s) and a `timeline` of the last 5 events (live region).
  - `widget.html` + `routes/widget.py` (`GET /widget`):
    - a minimal page without navbar, dock, toasts, or controls; it renders only `partials/glance.html` and refreshes it through `refresh.js` every `refresh` seconds (query parameter 10, 30, 60, or 300, default 30; `0` and unknown values fall back to 30; it ignores `localStorage`);
    - `theme=light|dark|auto` sets `data-theme` (`caramellatte`, `coffee`, or the system preference), and `compact=1` passes the compact flag;
    - the response sets `Content-Security-Policy: frame-ancestors {settings.ui_frame_ancestors}`; every other UI response sets `X-Frame-Options: SAMEORIGIN`;
    - the route and its partial endpoint are CSRF-exempt (GET only, read only).
- [x] **Step 4: Build the CSS** with `tools/build-css.ps1`.
- [x] **Manual verification:**
  - Run both apps on the simulator and open `http://<pc-ip>:8711` on a phone (no login, the dashboard opens directly):
    - the glance hero shows 17.0 C, the setpoint, fumes, the stove clock with a drift badge, the display text, and the "Veille economie" alert on the seed state 7;
    - start shows the ignition badge (simulator RAM `0x21` set to 0 first, see Task 8), and stop during ignition shows the brazier warning;
    - the dark theme follows the phone setting.
  - With `gateway=navel` and the skeleton profile, the live status renders, and the actions whose recipe or register is unverified are disabled with the "Non verifie - etape de reperage requise" label.
  - Opening `/login` returns 404.
  - Refresh:
    - with 10 s selected, the browser network tab shows one `/partials/glance` call about every 10 s and the freshness line resets;
    - switching to Manuel stops the calls, and "Actualiser" triggers one call with `max_age_s=0`;
    - a hidden tab makes no call, and the page refreshes at once when it becomes visible again;
    - after "Allumer", the glance updates immediately and again 5 s later;
    - the choice survives a page reload and differs per browser.
  - Suspension: "Suspendre les programmations" with the thermostat box checked on a simulator `OFF` stove starts it and shows the suspension alert. "Reprendre" removes the alert without stopping the stove.
  - Iframe test:
    - put `iframe-test.html` with `<iframe src="http://127.0.0.1:8711/widget?compact=1" width="420" height="90"></iframe>` in a temp folder, then serve it with `py -3.13 -m http.server 8799`;
    - with `STOVE_PILOT_UI_FRAME_ANCESTORS="'self' http://127.0.0.1:8799"`, `http://127.0.0.1:8799/iframe-test.html` renders the widget; with the default `'self'` the browser blocks it;
    - `/widget?theme=dark` uses the dark theme.
  - Check `rg "stove_pilot\.(core|lib)" src/stove_pilot/service/ui` returns nothing.
- [x] **Checkpoint.**

### Task 16: "Chrono poele" page (Feature A UI)

**Files:**
- Create: `src/stove_pilot/service/ui/routes/chrono.py`, `src/stove_pilot/service/ui/templates/chrono.html`, `src/stove_pilot/service/ui/templates/partials/{chrono_program,chrono_preview,chrono_issues}.html`, `src/stove_pilot/service/ui/static/js/chrono-editor.js`
- Modify: `src/stove_pilot/service/ui/i18n/fr.json` (`chrono.*` keys), `server_ui.py` (registrar list)

UX contract:
- Header:
  - a clock `stat` ("Horloge du poele 20:03, +15 min") with `btn btn-sm` "Synchroniser", which calls the common `POST /stove/clock/sync` through the generic client `post`;
  - a `409` with reason `programming_suspended` shown as `alert-warning` "Programmations suspendues - Reprendre pour modifier l'activation".
- Three block `card`s, in the order "Tous les jours" (day), "Semaine" (week, any day), "Week-end (VE-DI)" (weekend). Each card has:
  - an enable `toggle`;
  - 2 programs, each with time inputs `step="600"`, "Pas d'allumage" / "Pas d'arret" checkboxes that send `null` (shown as OFF on the panel), setpoint and power `select`s, and "Effacer" (sets both times unset);
  - day chips (`btn btn-sm btn-outline`, toggled with `btn-active`) limited to the block's `allowed_days`, in the week and weekend blocks only.
- Warnings from the API (`warnings`) and from client pre-validation (gap under 2 h) appear as a non-blocking `alert-warning` list. There is also a short info line: "Si des programmes se chevauchent, le poele suit le premier allumage et le premier arret."
- A read-only weekly preview (`partials/chrono_preview.html`) that merges the 3 blocks per day, recomputed client-side by `chrono-editor.js` (about 150 LOC).
- When `capabilities.writable` is false, the save button, the programs' inputs, and the block toggles are rendered disabled with the French label from `fr.json` key `common.unverified` ("Non verifie - etape de reperage requise"); the read-only view stays available.
- Save `PUT`s with the ETag. `422` shows the issues inline, `412` opens a `modal` "Le chrono a ete modifie sur le poele, recharger", success shows a toast.
- Live updates: on each `refresh-tick`, `chrono-editor.js` calls `GET /chrono/fingerprint` (through a UI partial endpoint) instead of re-rendering the cards. It compares the result with the loaded ETag:
  - unchanged: nothing happens;
  - changed and the form is clean: the cards reload;
  - changed and the form is dirty: a sticky `alert-warning` "Le chrono a ete modifie sur le poele - Recharger" appears, and local edits are never overwritten.
- While programming is suspended (`GET /programming`), the block toggles are disabled with the banner "Programmations suspendues - Reprendre pour modifier l'activation". Program contents stay editable.
- [x] **Step 1: Implement**, then rebuild the CSS.
> DONE ATTENDED 2026-10-22: real save of Semaine P1 from the UI verified by fresh read-back and on the panel, then reset to the baseline (fingerprint 50d72c714ace67b4). Not exercised on the real stove: block toggle from the UI (module degraded to batches of 4 or less during the session).
- [x] **Manual verification (simulator, then attended real stove):** change a chrono byte directly in the simulator while the page is open: a clean form reloads at the next tick, and a dirty form shows the banner. In the Semaine block, create Mon-Fri 06:30 to 08:00 at 20 C, save, reload (persisted). On the real stove, check the SEMAINE chrono menu on the panel, then reset it with the user's go. The Semaine block offers all seven day chips; a Saturday chip never appears in the Jour block (no day mask there).
- [x] **Checkpoint.**

### Task 17: deferred (rev 4)

The "Programmation serveur" page is **not implemented in the MVP**; see Appendix A.

### Task 18: History page, Reglages page, and PWA

**Files:**
- Create: `src/stove_pilot/service/ui/routes/{history,settings}.py`, `src/stove_pilot/service/ui/templates/{history.html,settings.html,partials/chart.html}`, `src/stove_pilot/service/ui/static/{manifest.webmanifest,img/icon-192.png,img/icon-512.png}`
- Modify: `src/stove_pilot/service/ui/nav.py` (add `settings`, always visible, label "Reglages"), `i18n/fr.json` (`settings.*` keys), `server_ui.py`
- Create (Step 3): `src/stove_pilot/service/ui/templates/{sw.js,offline.html}`, `src/stove_pilot/service/ui/static/js/sw-register.js`
- Modify (Step 3): `src/stove_pilot/service/ui/routes/pwa.py`, `templates/base.html`, `i18n/fr.json` (`offline.*` keys), `README.md` (HTTPS prerequisite)

- [x] **Step 1: Implement History:** a `tabs` component for 24 h (bucket 10) / 7 d (60) / 30 d (240), an SVG chart, an event `timeline` with French labels (including `clock_synced` and `setting_changed`) and a source `badge`, the manifest (name "Poele", theme color from `caramellatte`).
- [x] **Step 2: Implement Reglages** (design section 8.1c), one `card` per block. Each block is rendered disabled with `common.unverified` when its `capabilities`/`writable` flag is false, or hidden with "Non disponible sur ce poele" when the API returns `null` or 404:
  - "Economie d'energie": `toggle` plus two `select`s (+1/+2/+3/OFF, -1/-2/-3/OFF), the live sentence "S'eteint a consigne +X C, se rallume a consigne -Y C", and the hint "Si le poele s'allume et s'eteint plus d'une fois par 90 min, preferez l'arret OFF (modulation en P1)". `PUT /stove/settings/energy-saving`.
  - "Afficheur": `toggle` "Eteindre l'afficheur apres 1 min sans appui" (`PUT /stove/settings/display-eco`).
  - "Horloge du poele": time, drift, auto-sync on/off (read only, from settings), last sync, and the "Synchroniser" button.
  - "Entretien": total and partial hours, ignitions, a `progress` bar toward the 2000 h service, and the last 5 alarms with their French titles from the API.
  - "Affichage": `partials/refresh_selector.html` (the same control as the navbar), for mobile where the navbar dropdown is hidden.
  - Live updates: only the clock card is a live region. Forms are never refreshed while one of their inputs has focus.
  - History live updates: the chart and the timeline are live regions refreshed every `max(N, 60)` s (manual mode: only on "Actualiser").
  - Then rebuild the CSS.
- [x] **Step 3: PWA service worker (rev 5).** Complete the PWA with a service worker and an offline page (design section 8.6), following Steps 3a to 3f.

Placement: `routes/pwa.py` already owns the PWA surface (the manifest), so the service worker and the offline page join it.

**Caching rules.** The UI pilots a live device, so a cached page must never pass for the current stove state:

| Request | Strategy |
| --- | --- |
| Non-GET (commands, forms, CSRF) | never intercepted |
| HTMX fragments (`HX-Request` header) | never intercepted: network only, and the existing stale banner logic is unchanged |
| `/widget` | never intercepted: the embed keeps its own framing headers |
| Shell assets (CSS, JS, icons, manifest, offline page) | cache first, from a cache versioned by a hash of their content |
| Page navigations | network first; on network failure, the cached offline page, never a cached dashboard |

**Secure context.** Browsers only register service workers on HTTPS or `localhost`. On plain `http://<lan-ip>:8711`, registration is skipped and the UI behaves exactly as today. Phones need the internal HTTPS name described in Step 3f.

- [x] **Step 3a: `templates/sw.js`.** It is a Jinja template, so the asset version and the precache list come from the server. Flask does not autoescape `.js` templates, and `tojson` emits valid JSON.

```javascript
// Stove Pilot UI service worker: offline shell only, it never caches live stove data.
const CACHE_PREFIX = "stove-pilot-ui-";
const CACHE_NAME = CACHE_PREFIX + "{{ asset_version }}";
const OFFLINE_URL = "{{ offline_url }}";
const PRECACHE = {{ precache | tojson }};
const PRECACHE_SET = new Set(PRECACHE);
const BYPASS_PATHS = new Set({{ bypass_paths | tojson }});

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches
      .open(CACHE_NAME)
      .then((cache) => cache.addAll(PRECACHE.map((url) => new Request(url, { cache: "reload" }))))
      .then(() => self.skipWaiting()),
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((keys) =>
        Promise.all(
          keys
            .filter((key) => key.startsWith(CACHE_PREFIX) && key !== CACHE_NAME)
            .map((key) => caches.delete(key)),
        ),
      )
      .then(() => self.clients.claim()),
  );
});

self.addEventListener("fetch", (event) => {
  const request = event.request;
  if (request.method !== "GET" || request.headers.has("HX-Request")) {
    return;
  }
  const url = new URL(request.url);
  if (url.origin !== self.location.origin || BYPASS_PATHS.has(url.pathname)) {
    return;
  }
  if (PRECACHE_SET.has(url.pathname)) {
    event.respondWith(caches.match(url.pathname).then((cached) => cached || fetch(request)));
    return;
  }
  if (request.mode === "navigate") {
    event.respondWith(fetch(request).catch(() => caches.match(OFFLINE_URL)));
  }
});
```

- [x] **Step 3b: `static/js/sw-register.js`.** Load it with `defer` in `base.html`, right after `refresh.js`:

```javascript
// Registers the service worker where the browser allows it (HTTPS or localhost only).
if ("serviceWorker" in navigator && window.isSecureContext) {
  window.addEventListener("load", () => {
    navigator.serviceWorker.register("/sw.js", { scope: "/" }).catch((error) => {
      console.warn("Service worker registration failed", error);
    });
  });
}
```

- [x] **Step 3c: `routes/pwa.py`.** Replace the module docstring with "PWA routes: web app manifest, service worker and offline page." Keep `_manifest()` and add `"id": "/"` to it. Add:

```python
SERVICE_WORKER_PATH: Final = "/sw.js"
OFFLINE_PATH: Final = "/offline"
MANIFEST_PATH: Final = "/manifest.webmanifest"
_JS_MIMETYPE: Final = "text/javascript"
_ASSET_VERSION_LENGTH: Final = 12
# Reason: the shell assets referenced by base.html and the page templates; keep in sync with them.
_PRECACHED_STATIC: Final = (
    "css/app.css",
    "js/htmx.min.js",
    "js/refresh.js",
    "js/sw-register.js",
    "js/chrono-logic.js",
    "js/chrono-editor.js",
    "icons/icon-192.png",
    "icons/icon-512.png",
    "images/favicon-32.png",
)
_VERSIONED_TEMPLATES: Final = ("sw.js", "offline.html")


def _asset_version(app: Flask) -> str:
    """Hash the precached static files and the PWA templates (changes on every asset deploy).

    :param app: UI application.
    :return: Short hexadecimal version.
    """
    digest = hashlib.sha256()
    static_root = Path(app.static_folder or "")
    for name in _PRECACHED_STATIC:
        digest.update(name.encode("utf-8"))
        digest.update((static_root / name).read_bytes())
    for name in _VERSIONED_TEMPLATES:
        source, _, _ = app.jinja_env.loader.get_source(app.jinja_env, name)
        digest.update(source.encode("utf-8"))
    return digest.hexdigest()[:_ASSET_VERSION_LENGTH]


def register_pwa_routes(app: Flask) -> None:
    """Add the manifest, the service worker and the offline page.

    :param app: UI application.
    """
    version = _asset_version(app)
    static_urls = [f"{app.static_url_path}/{name}" for name in _PRECACHED_STATIC]
    precache = [*static_urls, OFFLINE_PATH, MANIFEST_PATH]

    @app.get(MANIFEST_PATH)
    def manifest() -> Response:
        return Response(json.dumps(_manifest()), mimetype=MANIFEST_MIMETYPE)

    @app.get(SERVICE_WORKER_PATH)
    def service_worker() -> Response:
        body = render_template(
            "sw.js",
            asset_version=version,
            offline_url=OFFLINE_PATH,
            precache=precache,
            bypass_paths=[WIDGET_PATH],
        )
        return Response(body, mimetype=_JS_MIMETYPE)

    @app.get(OFFLINE_PATH)
    def offline() -> str:
        return render_template("offline.html")
```

  Notes:
  - Before coding, list the static files the templates actually reference with `rg -o "filename='[^']+'" src/stove_pilot/service/ui/templates`, and align `_PRECACHED_STATIC` with it. Large source images that no template uses stay out.
  - New imports at the top of `pwa.py`: `hashlib`, `pathlib.Path`, `flask.render_template`, and `WIDGET_PATH` from `stove_pilot.service.ui.security`.
  - `security.py` keeps its headers unchanged, so `/sw.js` and `/offline` get `Cache-Control: no-store`. The browser revalidates the worker script anyway, and the Cache API stores the offline page regardless of that header.

- [x] **Step 3d: `templates/offline.html`.** It is standalone and does **not** extend `base.html`, so it needs neither the API nor the navigation context.
  - `<html lang="fr">`, the `app.css` link, the theme-color meta and the 32 px favicon. The light and dark themes still follow the system preference through the daisyUI theme configuration.
  - A centered daisyUI `card` with `t("offline.title")`, `t("offline.body")`, and a `btn btn-primary` link to `/` labelled `t("offline.retry")`. No script.
  - Add the keys to `i18n/fr.json`, in plain ASCII like the existing copy: `"offline.title": "Hors ligne"`, `"offline.body": "Le serveur du poele n'est pas joignable. Verifiez le Wi-Fi ou le VPN, puis reessayez."`, `"offline.retry": "Reessayer"`.
  - Then rebuild the CSS with `tools/build-css.ps1`.
- [x] **Step 3e: `base.html`.** Add the `sw-register.js` script tag (Step 3b). Nothing else changes in the templates.
- [x] **Step 3f: HTTPS prerequisite (user infrastructure, out of scope for agents).** The user's existing internal DNS, VPN and TLS termination publish the UI under an internal HTTPS name, with a certificate the phones trust, forwarding to port 8711. The plan only states the requirements:
  - The UI needs no change behind a TLS-terminating proxy: every URL it emits is relative and the service worker scope is `/`. The proxy must serve the UI at the root path, not under a sub-path.
  - The UI stays internal (LAN and VPN only), as stated in design section 11.
  - Plain `http://<lan-ip>:8711` keeps working, without the service worker.
  - The session cookie `Secure` flag stays off, so that plain-HTTP LAN access keeps working.
  - The README documents only these requirements, not a specific proxy or VPN.
- [x] **Manual verification:**
  - PWA service worker (Step 3), with the simulator API and desktop Chrome on `http://localhost:8711` (a secure context):
    - **Registration and cache.** DevTools > Application:
      - the service worker is "activated and running" with scope `/`;
      - Cache Storage holds a single `stove-pilot-ui-<version>` cache that lists exactly the precache entries;
      - the Manifest panel shows no installability error, and Chrome offers "Install app".
    - **Live data.** The Network tab shows the HTMX polling fragments and every POST command going to the network, not "(ServiceWorker)".
    - **Offline.** Set DevTools Network to "Offline", then reload the dashboard: the offline page appears, never a stale dashboard. Go back online and click "Reessayer": the live dashboard returns.
    - **Update.** Add a harmless comment to `refresh.js` and restart the UI. On reload, the new worker activates and the old cache is deleted. Then revert the comment and restart.
    - **Plain HTTP and widget.**
      - `http://<lan-ip>:8711` from another device: no registration, no console error, the UI works as before.
      - `/widget` still renders inside its iframe host.
    - **Phone (user)**, through the internal HTTPS URL:
      - install to the home screen;
      - the app launches standalone with the icon and theme color;
      - with Wi-Fi and VPN disconnected, it opens on the offline page.
  - The charts render after a few hours of simulator data, and "Add to home screen" works on the phone.
  - On the simulator, Reglages saves the energy saving offsets and display eco (with `allow_unverified`). On `gateway=navel` before Step 6a, the blocks show "Non disponible sur ce poele" or the unverified label, and no write is sent.
  > PENDING USER (phone): install to the home screen through the internal HTTPS URL, check standalone launch with icon and theme color, and check the offline page with Wi-Fi and VPN off. Desktop service worker checks passed on the simulator 2026-10-22.
- [x] **Checkpoint.**

---

## Phase 6 - Deployment and acceptance

### Task 19: Packaging, deployment, real-stove acceptance

**Files:**
- Create: `stove-pilot/{Dockerfile,compose.yaml,requirements.lock,MANIFEST.in}` (`MANIFEST.in` already exists with `recursive-include src/stove_pilot *.toml *.html *.css *.js *.json *.png *.webmanifest`, `prune tools`, `prune discovery`; keep it in sync with the shipped data files, and do not touch `pyproject.toml`), `stove-pilot/deploy/systemd/{stove-pilot-api,stove-pilot-ui}.service`, `stove-pilot/deploy/windows/run.ps1` (runs both apps on demand on the PC)
- Modify: `stove-pilot/README.md` (setup, `.env`, discovery CLI, CSS build, feature flags and removal procedure, deployment, backup, safety notes)

> NOTE: Docker is not installed on the dev PC; Dockerfile and compose.yaml were reviewed statically and NOT built or run (to verify on the deployment host).
- [x] **Step 1:** Dockerfile (`python:3.12-slim`, non-root user, non-editable `pip install -r requirements.lock .` so the package data from `MANIFEST.in` ships), and `compose.yaml` with `api` (not published, `/data` volume, healthcheck on `/api/v1/health`) and `ui` (port 8711, `STOVE_PILOT_UI_API_BASE_URL=http://api:8710`). Build for `linux/arm64` when the target is a Raspberry Pi.
- [x] **Step 2:** deployment modes in the README (the chrono runs on the stove in both cases; a host outage never stops the heating):
  - **Recommended, always-on host:** Docker on the NAS or Pi, with `restart: unless-stopped`. It keeps the clock auto-sync (about 8 min/day of drift otherwise), the history, and the suspension auto-resume running.
  - **On demand:** `deploy/windows/run.ps1` on the PC when needed. The README tells the user to press "Synchroniser" at least once a week, and notes that a suspension with "Jusqu'au" only resumes while the API runs.
- [x] **Step 3:** a daily `sqlite3 .backup` (history and suspension state), plus router hardening (DHCP reservation, optional egress block for the module, no port forwarding, the existing VPN for remote access).
- [ ] **Step 4: Acceptance (attended):**
  - Packaging: in a clean venv, a non-editable `pip install .`, then `python -c "import importlib.resources as r; print(r.files('stove_pilot.core.stove.profiles').joinpath('superior_maira.toml').is_file())"` must print `True`. Then start both apps from a directory outside the project (for example `%TEMP%`) and check that the pages and the CSS load.
  - Security posture: the API port is not published in Docker, the UI is the only exposed port (8711), and 8711 is not exposed beyond the LAN or VPN (there is no UI login).
  - Status matches the panel (temperature within 0.5 C, state, display text). The glance hero and `/widget` show the same values, and the decoded alert matches the panel message (for example `SERVICE`).
  - Start/stop from the phone works, a restart during cooling is deferred then executed, and a stop during ignition asks for the brazier confirmation.
  - Clock: after 24 h with the API running, the drift stays under 2 min (auto-sync events in the history).
  - Suspension (attended): with a chrono block enabled, "Suspendre les programmations" in thermostat mode disables it on the panel, the stove regulates through ENERGY SAVING (`ENS`, then `ENERGY OK` standby), and "Reprendre" re-enables exactly the same block.
  - Refresh: a tablet left open on the dashboard for 1 h in 30 s mode stays current, and the module shows no extra empty replies (compare the API log error count with the poller-only baseline).
  - Settings (if mapped in Step 6a): DISPLAY ECO turns the panel display off after 1 min, and the energy saving offsets match the panel menu.
  - Native chrono: a week-block program written, visible on the panel, the stove ignites at the programmed time with the API stopped, and the clock is synced.
  - Removal drill: `STOVE_PILOT_FEATURES=[]` hides the chrono menu and the suspend button, and the dashboard, history, and Reglages keep working.
- [ ] **Checkpoint:** MVP accepted.

---

## Amendment 1 (2026-10-22, user-approved): history retention and paginated events

Scheduled after Task 19 Steps 1-3; lands before the UI split Task U1 (contract baseline). Verification is manual only (throwaway scripts in %TEMP%, no tests/ folder), per user decision.

- [x] **AM1-a (backend):** history_retention_days default 0 = keep forever (HistoryRepository.purge no-op on 0, negative invalid, .env.example updated); history schema v2 (additive nullable 	emperature_samples.power_level INTEGER, setpoint_c REAL, stored by 
ecord_sample, timestamps stay UTC ISO 8601); GET /api/v1/history/events gains rom, 	o, efore_at, efore_id (keyset, row-value (at, id) < (?, ?), order t DESC, id DESC, response stays a list, EventDto.id: int additive); setting ui_history_events_page_size (default 25, 5..100, env STOVE_PILOT_UI_HISTORY_EVENTS_PAGE_SIZE) in .env.example.
- [x] **AM1-b (UI + deployment):** History page paginated timeline (page_size + 1 fetch, Newer/Older controls, cursor in URL query, live refresh on first page only; remove EVENT_FETCH_LIMIT, EVENT_DISPLAY_LIMIT and the truncated message); README: daily backup copied to another host (NAS), plus the new settings.
## Amendment 2 (2026-10-22, user feedback during acceptance)

- [x] **AM2-a (UI):** Reglages "Affichage" card removed; icon-only navigation (top bar and dock, with aria-label and title); cog icon for Reglages; PWA icons from the Calcifer logo (maskable variant added); dashboard age indicator fixed by aligning the refresh timer just after each poll (no extra stove read).
- [x] **AM2-b (live verification, attended 2026-10-22):** energy_start_offset (0x91, 2 -> 3 -> 2) and display_eco (0x29, 1 -> 0 -> 1) written and read back on the real stove, profile flags set to erified = true. display_idle_content (0x4C) stays unverified.
- [x] **AM2-c (backend + UI):** runtime toggle for the clock auto-sync (PUT /api/v1/stove/clock/auto-sync, persisted in SQLite), default drift tolerance 300 s, auto-sync scheduled so the minutes byte lands on the PC minute boundary. > PENDING ATTENDED: verify on the real stove that a scheduled sync lands cleanly and whether the stove resets its seconds (read RAM 0x7A right after the write).
## Backlog

Feature B server schedule (Appendix A), notifications (ntfy/Telegram), MQTT + Home Assistant discovery, a Svelte SPA on `/api/v1`, an ESP32 bridge `RegisterIO` adapter, pellet consumption estimates, weather-aware preheating.

---

## Appendix A - Feature B: server schedule (deferred, do not implement)

Kept verbatim from rev 3 for a later revival. See the revival checklist at the end.


### Deferred Task 11: Schedule models, validation, planner

**Files:**
- Create: `src/stove_pilot/core/schedule/{__init__,models,validation,planner}.py`

**Interfaces:**
- Consumes: `utilities/weektime.py` only (plus `core/stove` types). **No import from `core/chrono`.**
- Produces: `Slot(start_min, end_min, setpoint_c, power_level)`, `WeeklySchedule(enabled, days, paused_until, version)` with `empty()`, `ScheduleLimits(...)`, `ValidationIssue(day, slot_index, code, message)` (its own class, not shared with chrono), `validate_schedule(schedule, limits)`, `Segment`, `Occurrence(key, segment, minutes_since_start)`, `Transition(at, action, setpoint_c, power_level)`, `build_timeline`, `active_segment`, `is_continuation`, `current_occurrence`, `next_transition(schedule, now, tz)`.

Validation codes: `granularity`, `order`, `too_short` (< 30 min), `overlap`, `too_many_slots` (> 6), `gap_too_short` (< 120 min, the manual's cooling recommendation, including across midnight and the week wrap; exact adjacency is allowed), `setpoint_range`, `power_range`. `ScheduleLimits.min_gap_min` defaults to 120.

```python
# src/stove_pilot/core/schedule/planner.py (core functions)
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from stove_pilot.core.schedule.models import WeeklySchedule
from stove_pilot.utilities.weektime import MINUTES_PER_DAY, MINUTES_PER_WEEK, minute_of_week


@dataclass(frozen=True)
class Segment:
    """Scheduled ON interval in minutes of week, ``end`` exclusive."""

    start: int
    end: int
    setpoint_c: int | None
    power_level: int | None


@dataclass(frozen=True)
class Occurrence:
    """Concrete ON or OFF period identified by a stable key."""

    key: str
    segment: Segment | None
    minutes_since_start: int | None


def build_timeline(schedule: WeeklySchedule) -> tuple[Segment, ...]:
    """Flatten daily slots onto a sorted minute-of-week timeline.

    :param schedule: Weekly schedule.
    :return: Sorted segments.
    """
    segments = [
        Segment(
            start=day * MINUTES_PER_DAY + slot.start_min,
            end=day * MINUTES_PER_DAY + slot.end_min,
            setpoint_c=slot.setpoint_c,
            power_level=slot.power_level,
        )
        for day, slots in schedule.days.items()
        for slot in slots
    ]
    return tuple(sorted(segments, key=lambda segment: segment.start))


def active_segment(timeline: tuple[Segment, ...], minute: int) -> Segment | None:
    """Return the segment covering ``minute`` of week.

    :param timeline: Output of ``build_timeline``.
    :param minute: Minute of week.
    :return: Active segment or ``None``.
    """
    return next((s for s in timeline if s.start <= minute < s.end), None)


def is_continuation(timeline: tuple[Segment, ...], segment: Segment) -> bool:
    """Tell whether another segment ends exactly where ``segment`` starts (week wrap included).

    :param timeline: Output of ``build_timeline``.
    :param segment: Segment to test.
    :return: ``True`` when the stove keeps running across the boundary.
    """
    target = segment.start % MINUTES_PER_WEEK
    return any(o is not segment and o.end % MINUTES_PER_WEEK == target for o in timeline)


def _monday(local: datetime) -> date:
    return local.date() - timedelta(days=local.weekday())


def current_occurrence(timeline: tuple[Segment, ...], local: datetime) -> Occurrence:
    """Identify the ON/OFF period containing ``local``; keys embed the local date.

    :param timeline: Output of ``build_timeline``.
    :param local: Aware datetime in the schedule timezone.
    :return: Current occurrence.
    """
    minute = minute_of_week(local)
    monday = _monday(local)
    segment = active_segment(timeline, minute)
    if segment is not None:
        day = monday + timedelta(days=segment.start // MINUTES_PER_DAY)
        key = f"on:{day.isoformat()}:{segment.start % MINUTES_PER_DAY:04d}"
        return Occurrence(key, segment, minute - segment.start)
    if not timeline:
        return Occurrence("off:none", None, None)
    ended = [s.end for s in timeline if s.end <= minute]
    end, base = (max(ended), monday) if ended else (timeline[-1].end, monday - timedelta(days=7))
    day = base + timedelta(days=end // MINUTES_PER_DAY)
    return Occurrence(f"off:{day.isoformat()}:{end % MINUTES_PER_DAY:04d}", None, None)
```

`next_transition`: scan the boundaries over the next 8 days (non-continuation starts become `start`, continuation starts become `adjust`, ends without a continuation become `stop`), convert the first boundary after `now` to an aware datetime (a nonexistent DST time moves to the next valid instant), and return `None` when the schedule is disabled or empty.

- **Step 1: Implement.**
- **Manual verification:** a script prints `current_occurrence` and `next_transition` for Mon 06:30 to 08:00 at Mon 07:00 (expected key `on:<monday>:0390`), at Mon 09:00, and for Sun 22:00 to 24:00 plus Mon 00:00 to 02:00 at Mon 01:00 (expected `stop` at 02:00).
- **Checkpoint.**

### Deferred Task 12: Schedule store, repository, runner

**Files:**
- Create: `src/stove_pilot/core/schedule/{service,repository,runner}.py`

**Interfaces:**
- Produces:
  - `RunnerState(last_applied_key, last_applied_kind)`
  - `ScheduleStore(db)`: `load`, `save`, `load_runtime`, `save_runtime`
  - `ScheduleRepository(store, limits, clock, tz, conflict_check: Callable[[], str | None])`: `get()`, `replace(schedule, *, expected_version)`, `set_state(*, enabled, paused_until)` (raises `FeatureConflictError` when enabling while `conflict_check()` returns a reason), `next_transition()`, `runtime()`, `save_runtime(state)`, `blocked_reason() -> str | None`, `summary(now) -> ProgrammingSummary` (its own dataclass in `core/schedule/models.py` with the same field names as the chrono one: `active, current, next_at, blocked_reason`; no shared type, so the features stay independent), `suspend() -> dict` (`{"enabled": bool}`, then disables the schedule), `resume(snapshot) -> None`. `set_state(enabled=True)` raises `FeatureConflictError("programming_suspended")` while the injected `is_suspended()` is true.
  - `RunnerAction(StrEnum)`: `NONE, START, STOP, ADJUST`. `decide_action(occurrence, last_kind, timeline, catch_up_min) -> RunnerAction`. `ScheduleRunner(schedules, stove, clock, tz, catch_up_min, tick_s, stop_event)` with `tick()`, `run_forever()`, and a `last_tick_at` attribute

```sql
CREATE TABLE IF NOT EXISTS schedule (
  id INTEGER PRIMARY KEY CHECK (id = 1), version INTEGER NOT NULL, enabled INTEGER NOT NULL,
  paused_until TEXT, days_json TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS schedule_runtime (
  id INTEGER PRIMARY KEY CHECK (id = 1), last_applied_key TEXT, last_applied_kind TEXT,
  updated_at TEXT NOT NULL);
```

```python
def decide_action(
    occurrence: Occurrence,
    last_kind: Literal["on", "off"] | None,
    timeline: tuple[Segment, ...],
    catch_up_min: int,
) -> RunnerAction:
    """Map an occurrence change to an action (stops always, starts only if recent).

    :param occurrence: Current occurrence (its key differs from the last applied one).
    :param last_kind: Kind of the last applied occurrence.
    :param timeline: Current timeline.
    :param catch_up_min: Maximum lateness for a start.
    :return: Action to apply.
    """
    if occurrence.segment is None:
        return RunnerAction.STOP if last_kind == "on" else RunnerAction.NONE
    if last_kind == "on" and is_continuation(timeline, occurrence.segment):
        return RunnerAction.ADJUST
    if occurrence.minutes_since_start is not None and occurrence.minutes_since_start <= catch_up_min:
        return RunnerAction.START
    return RunnerAction.NONE
```

`tick()`:
1. When disabled, paused, or `blocked_reason()` is set, reset the runtime and return `NONE`.
2. When the occurrence key is unchanged, return `NONE`.
3. Apply the action: `START` is a start plus setpoint/power, `ADJUST` is setpoint/power only, `STOP` is a stop. Every command uses `source=SCHEDULE`.
4. `CommandRejectedError`: save the key (no retry). A `deferred` outcome (for example a stop during ignition): save the key, because `StoveRepository` owns the pending command. `GatewayError`: do not save the key (retried at the next tick).
5. Save the `RunnerState`.

- **Step 1: Implement.**
- **Manual verification (simulator):** with `schedule_tick_s=5` and a slot starting 2 min from now, run the API in Phase 4 (or a script calling `tick()` in a loop) and watch the events: `START` at the boundary, a manual stop respected until the end, `STOP` at the end.
- **Checkpoint.**

### Deferred Task 17: "Programmation serveur" page (Feature B UI)

**Files:**
- Create: `src/stove_pilot/service/ui/routes/schedule.py`, `src/stove_pilot/service/ui/presets.py`, `src/stove_pilot/service/ui/templates/schedule.html`, `src/stove_pilot/service/ui/templates/partials/{schedule_grid,schedule_day_editor,schedule_issues}.html`, `src/stove_pilot/service/ui/static/js/schedule-editor.js`
- Modify: `i18n/fr.json` (`schedule.*` keys), `server_ui.py`

UX contract:
- A banner (`alert-info`): "Ce mode necessite que le serveur reste allume."
- A weekly grid of 7 columns with blocks positioned as a percentage of 24 h and colored by setpoint band. Tapping a day opens the `modal` day editor (slots, `step="600"` time inputs, setpoint, power with "inchange", delete, "+ Ajouter", "Copier vers..." chips).
- Presets (`Semaine travail`, `Teletravail`, `Vide`) from `presets.py` replace the working copy after confirmation.
- A `toggle` "Actif", "Pause jusqu'au" (`input type="date"`), a next-transition line, and `blocked_reason` as `alert-warning`. On each `refresh-tick`, only the next-transition line and `blocked_reason` refresh; the grid is never re-rendered while the working copy is dirty. While programming is suspended, the "Actif" toggle is locked with the suspension banner.
- `schedule-editor.js` (about 200 LOC): working copy, re-render, desktop drag-to-paint on 30 min cells, client pre-validation (order, overlap, minimum length, gap of 120 min), `beforeunload` guard, save with ETag (412 modal, 422 issues).
- **Step 1: Implement**, then rebuild the CSS.
- **Manual verification (simulator):** build a full week in under 2 minutes with a preset plus "Copier vers", save, watch the runner act at the next boundary (`schedule_tick_s=5`), and check the conflict banner when the chrono is enabled.
- **Checkpoint.**

### Revival checklist (if Feature B is ever approved)

1. Prerequisite: the RAM start recipe is verified on the real stove (Task 4 Step 5), because the runner starts the stove remotely.
2. Settings: restore `schedule_tick_s: int = 30`, `catch_up_min: int = 60`, and `features: set[Literal["chrono", "schedule"]]`. Restore `EventSource.SCHEDULE`.
3. Implement Deferred Tasks 11 and 12 (`core/schedule`, never importing `core/chrono`).
4. API: `routers/schedule.py` and `schemas/schedule.py` (`GET/PUT /schedule` with `ETag` = version, `PUT /schedule/state`, `GET /schedule/next`), `summarize_schedule`, `schedule_participant`, and the `FEATURES["schedule"]` entry with `start_threads=start_schedule_runner`. `Container` gains `schedule: ScheduleRepository | None`.
5. Mutual exclusion, injected by composition only: `ChronoRepository` and `ScheduleRepository` take `conflict_check: Callable[[], str | None]`; enabling one while the other is active raises `FeatureConflictError` (`server_schedule_active` / `native_chrono_active`, 409), and the runner skips its tick while the chrono is enabled.
6. UI: Deferred Task 17, the `schedule` nav entry, and the chrono page banner for `server_schedule_active`.
7. Deployment: the schedule requires the always-on host. Acceptance: one week of operation; kill the API during an ON slot and restart it after the slot end, and the stop is applied.
