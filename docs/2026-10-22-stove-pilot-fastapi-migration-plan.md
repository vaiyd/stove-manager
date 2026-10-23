# Stove Pilot - FastAPI Migration Plan (optional)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. Do **not** create git commits: the user reviews and commits. Each task ends with a manual verification and a "Checkpoint" for user review.

> **Start condition:** start this plan only when the user explicitly asks for it. Its prerequisite is that `2026-10-22-stove-pilot-ui-split-plan.md` is complete, Task U5 checkpoint included.
> - Reason: this plan removes Flask and Waitress from the `stove-pilot` dependencies, which is only possible once the Flask UI has left the package.
> - It also reuses `stove-pilot/tools/contract_snapshot.py` from Task U1.
> - The plan can run any time after the UI split. It captures a fresh baseline first (Task F1), because the API may have changed in the meantime.

**Goal:** Migrate the API in `stove-pilot` from Flask + Waitress to FastAPI + uvicorn with async routes, without any change to the HTTP contract the UI consumes. The UI project `stove-pilot-ui` is not touched.

**Architecture:** FastAPI app factory; `APIRouter`s registered by the same `register_*` closure functions.
- **Async routes at the edge only.** Every call into a repository runs in a worker thread through `asyncio.to_thread`. `core/`, `lib/` and `utilities/` stay synchronous and unchanged: the `threading.RLock` reentrancy and the single serialized `NavelClient` remain the concurrency model.
- **Poller.** It runs as an asyncio task, started and stopped by the FastAPI lifespan, that wraps the unchanged `StatusPoller.run_forever` in a worker thread.

**Tech Stack:** Python 3.12+, FastAPI 0.159 (`fastapi>=0.159,<1`), uvicorn 0.54 (`uvicorn>=0.54,<1`, no extras), Pydantic 2, pydantic-settings, httpx, stdlib `sqlite3`.

## Decision Record (summary)

- **Context:** the rest of the user's codebase is FastAPI. The API is the only Flask service left once the UI split is done.
- **Decision:** API on FastAPI, with async routes at the edge only. The sync core is reached through `asyncio.to_thread`.
- **Alternatives considered:**
  - **Full async core** (`httpx.AsyncClient`, `asyncio.Lock`, aiosqlite): rejected. There is no gain for a single serial device that accepts one request at a time, and it would break the `RLock` reentrancy the repositories rely on.
  - **Keep Flask:** rejected by the user, for codebase consistency.
- **Consequences:**
  - The API gains an OpenAPI document.
  - Concurrency is unchanged in practice: the default thread pool replaces Waitress's 4 threads, and the `NavelClient` lock still serializes module traffic.
  - No infrastructure cost change.

## Global Constraints

- **No automated tests** (no `tests/`, no pytest). Verification is manual: CLI, `curl`, browser, and the `tools/contract_snapshot.py` comparison.
- **Contract freeze:** the following stay identical across `/api/v1`. The only allowed exceptions are listed under "Accepted contract differences" below.
  - Every path, method and status code.
  - The problem fields `type`, `title`, `status`, `code`, `reason` and `register`.
  - The `ETag`, `If-Match` and `Retry-After` headers.
  - Every response body shape.
  - Anything else found by the comparison is a bug to fix, unless the user approves it at a checkpoint.
- The framework changes, the business behavior does not. `core/`, `lib/`, `utilities/` and `stove-pilot-ui/` are not modified.
- **Simulator only** for agents. Every run sets `STOVE_PILOT_GATEWAY=simulator` explicitly in the shell. Real-stove checks happen only in the step marked **(attended)**.
- Agents never read, print or edit `.env` files.
- **Contract runs** use:
  - a throwaway database directory: `$env:TEMP\stove-pilot-contract`;
  - port `8720`;
  - a throwaway key, `contract-check-key`, never the real key.
- English for code, comments, logs and identifiers. No emojis. Short Sphinx docstrings, type hints everywhere, files under 500 LOC, imports at the top of modules.
- Packaging follows the existing convention: setuptools auto-discovery, src layout, no extra tool tables. The console scripts `stove-pilot-api` and `stove-pilot` keep their names.
- Before coding, read the current FastAPI documentation for these topics: lifespan, `add_exception_handler` / `RequestValidationError`, router-level dependencies, `fastapi.security.APIKeyHeader`, and `uvicorn.Config` / `uvicorn.Server`. Use Context7, or fastapi.tiangolo.com when Context7 is unavailable.

## Accepted Contract Differences

1. **Query parameter errors** (`max_age_s`, `bucket_min`, `limit`, `from`, `to`) keep status 422 and code `validation_failed`, but the `issues` texts become Pydantic messages. For example, `max_age_s: Input should be greater than or equal to 0` replaces `max_age_s must be a finite number >= 0`. The UI never displays these texts.
2. **Request bodies must be JSON** (`Content-Type: application/json`, or no content type). Flask accepted any content type. The UI always sends JSON through httpx.
3. **Invalid stop bodies:** an invalid `POST /stove/commands/stop` body no longer consumes a rate-limit slot, because body validation now runs before the handler.
4. **405 responses** gain the `Allow` header. The problem `detail` of 404 and 405 changes wording (not part of the contract).
5. **`HEAD` requests** on GET routes return 405, where Flask answered them implicitly. Healthchecks must use GET.
6. **New endpoints** `GET /api/v1/openapi.json` and `GET /api/v1/docs`, not protected by the API key. They expose the schema only, never data. The API stays bound to localhost or the private Docker network.

---

### Task F1: Fresh Flask baseline

**Files:**
- Create (generated): `stove-pilot/contract/flask-nokey.json`, `stove-pilot/contract/flask-key.json`

- [ ] **Step 1: Capture without a key.** From `stove-pilot/` with the backend venv active, start the throwaway simulator API in shell 1, then capture from shell 2:

```powershell
# Shell 1
$env:STOVE_PILOT_GATEWAY = "simulator"
$env:STOVE_PILOT_DB_PATH = "$env:TEMP\stove-pilot-contract\stove-pilot.sqlite"
$env:STOVE_PILOT_API_PORT = "8720"
$env:STOVE_PILOT_API_KEY = ""
Remove-Item -Recurse -Force "$env:TEMP\stove-pilot-contract" -ErrorAction SilentlyContinue
stove-pilot-api

# Shell 2
python tools/contract_snapshot.py capture --base-url http://127.0.0.1:8720 --out contract/flask-nokey.json
```

- [ ] **Step 2: Capture with a key.**
  - Shell 1: stop the API, wipe the database directory, set `$env:STOVE_PILOT_API_KEY = "contract-check-key"`, then restart.
  - Shell 2: set `$env:CONTRACT_API_KEY = "contract-check-key"`, then run `python tools/contract_snapshot.py capture --base-url http://127.0.0.1:8720 --out contract/flask-key.json --api-key-env CONTRACT_API_KEY`.
- [ ] **Step 3: Check the tool.** If the API changed since the UI split, first add probes to `_probes()` for any new route, then redo Steps 1 and 2. Repeat Step 1 into `contract/repeat-nokey.json` and compare it with `flask-nokey.json`: the result must be `0 failure(s)`. Delete the repeat file.
- [ ] **Manual verification:**
  - Expected statuses:
    - `no_key_status` is 401 `unauthorized`;
    - `start_7` is 429 with `retry-after`;
    - `chrono_put_no_if_match` is 428;
    - `block_unknown` is 404 `unknown_block`.
  - `chrono` carries an `etag` header.
- [ ] **Checkpoint.**

### Task F2: FastAPI app factory, routers and entrypoint

Tasks F2 and F3 form one cutover. The API is not runnable between the start of F2 and its manual verification, so run them back to back.

**Files:**
- Modify: `stove-pilot/pyproject.toml`
- Modify: `src/stove_pilot/service/api/{errors.py,auth.py}`
- Create: `src/stove_pilot/service/api/params.py` (replaces `parsing.py`, which is deleted in Task F3)
- Modify: `src/stove_pilot/service/api/routers/{health,features,stove,settings,summary,history,programming,chrono}.py`
- Modify: `src/stove_pilot/service/{app.py,features.py}`, `src/stove_pilot/server_api.py`

- [ ] **Step 1: Dependencies.** In `pyproject.toml`, replace `flask` and `waitress` with `"fastapi>=0.159,<1"` and `"uvicorn>=0.54,<1"`.
  - Use the plain packages, without `[standard]` extras: this avoids building uvloop or httptools on arm64, and throughput is irrelevant here.
  - Keep `httpx`: the Navel client and the contract tool use it.
  - Reinstall the backend venv.

- [ ] **Step 2: `errors.py`.** Keep `ApiProblemError`, `format_pydantic_issues`, the domain mapping branches, and every code, title and detail string unchanged. Change only the framework types, and add the request-validation normalization:

```python
"""Problem+json error responses and the mapping of exceptions to HTTP statuses."""

import logging
from collections.abc import Mapping
from http import HTTPStatus
from typing import Any, Final

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import ValidationError as PydanticValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException

# ... same domain exception imports, _LOGGER, PROBLEM_MIMETYPE, _TYPE_PREFIX, _INTERNAL_DETAIL ...

_REQUEST_SOURCES: Final = frozenset({"body", "query", "path", "header"})
_BODY_REQUIRED: Final = "request body is required"
_BODY_NOT_OBJECT: Final = "request body must be a JSON object"


def problem_response(
    status: int,
    code: str,
    title: str,
    detail: str,
    *,
    headers: Mapping[str, str] | None = None,
    **extra: Any,
) -> JSONResponse:
    """Build a problem+json response (same members and order as before)."""
    body: dict[str, Any] = {
        "type": f"{_TYPE_PREFIX}{code}",
        "title": title,
        "status": status,
        "detail": detail,
        "code": code,
    }
    body.update({key: value for key, value in extra.items() if value is not None})
    return JSONResponse(
        body, status_code=status, media_type=PROBLEM_MIMETYPE, headers=dict(headers or {})
    )


def format_request_issues(exc: RequestValidationError) -> list[str]:
    """Flatten FastAPI request errors into ``field: message`` strings (source segment dropped).

    :param exc: Request validation error.
    :return: One string per error; body-level errors keep the Flask-era wording.
    """
    issues: list[str] = []
    for error in exc.errors():
        loc = tuple(error.get("loc", ()))
        kind = str(error.get("type", ""))
        if loc[:1] == ("body",) and (len(loc) == 1 or kind == "json_invalid"):
            issues.append(_BODY_REQUIRED if kind == "missing" else _BODY_NOT_OBJECT)
            continue
        parts = loc[1:] if loc and loc[0] in _REQUEST_SOURCES else loc
        location = ".".join(str(part) for part in parts)
        message = str(error.get("msg", ""))
        issues.append(f"{location}: {message}" if location else message)
    return issues
```

  Handlers become `async def _handle_x(_request: Request, exc: X) -> JSONResponse`:
  - `_handle_request_validation` returns the same 422 `validation_failed` problem as `_handle_pydantic_error`, with `issues=format_request_issues(exc)`.
  - `_handle_http_error` takes a `StarletteHTTPException`:
    - `status = exc.status_code`, and the code is computed as today;
    - `title = HTTPStatus(status).phrase` when the status is known, otherwise `"HTTP error"`;
    - `detail = str(exc.detail)`;
    - `headers=exc.headers`, which adds `Allow` on 405 (accepted difference 4).
  - `register_error_handlers(app: FastAPI)` calls `app.add_exception_handler(...)` for `ApiProblemError`, `StovePilotError`, `RequestValidationError`, `PydanticValidationError`, `StarletteHTTPException` and `Exception`, in that order.
  - Starlette routes the `Exception` handler through its server-error middleware, which re-raises after responding, so uvicorn logs a second traceback for unexpected errors. This is acceptable; add no workaround.

- [ ] **Step 3: `auth.py`.** The key check becomes a router dependency, not a middleware, because an `HTTPException` raised in a `BaseHTTPMiddleware` surfaces as a 500. `/health` is registered on a router without this dependency, which replaces `_EXEMPT_ENDPOINTS`.

```python
"""Optional ``X-API-Key`` check, applied as a dependency of the protected API router.

``api_key_dependencies`` defines its check as a closure over the key; this is the documented
closure pattern used by every ``register_*`` function of the API.
"""

import hmac
from collections.abc import Sequence
from typing import Annotated, Final

from fastapi import Depends, Security, params
from fastapi.security import APIKeyHeader

from stove_pilot.service.api.errors import ApiProblemError

API_KEY_HEADER: Final = "X-API-Key"
_API_KEY_SCHEME: Final = APIKeyHeader(name=API_KEY_HEADER, auto_error=False)


def api_key_dependencies(api_key: str) -> Sequence[params.Depends]:
    """Build the router dependencies requiring the key (none when no key is configured).

    :param api_key: Expected key; an empty value disables the check.
    :return: Dependencies for ``APIRouter(dependencies=...)``.
    """
    if not api_key:
        return []
    expected = api_key.encode("utf-8")

    async def check_api_key(
        provided: Annotated[str | None, Security(_API_KEY_SCHEME)],
    ) -> None:
        if hmac.compare_digest((provided or "").encode("utf-8"), expected):
            return
        raise ApiProblemError(
            401,
            "unauthorized",
            "Unauthorized",
            f"A valid {API_KEY_HEADER} header is required.",
        )

    return [Depends(check_api_key)]
```

- [ ] **Step 4: `params.py`** holds the shared request parameters. The `If-Match` parsing reproduces `werkzeug.http.unquote_etag`.

```python
"""Shared request parameters: ``max_age_s``, the strong ``If-Match`` precondition, local times."""

from datetime import datetime, tzinfo
from typing import Annotated

from fastapi import Header, Query

from stove_pilot.service.api.errors import ApiProblemError

MaxAgeQuery = Annotated[float | None, Query(ge=0, allow_inf_nan=False)]


def require_if_match(if_match: Annotated[str, Header()] = "") -> str:
    """Return the ETag the client last saw (empty for a weak tag, which never matches).

    :param if_match: Raw ``If-Match`` header.
    :return: The unquoted strong tag.
    :raises ApiProblemError: The header is missing (428).
    """
    header = if_match.strip()
    if not header:
        raise ApiProblemError(
            428,
            "precondition_required",
            "Precondition required",
            "An If-Match header with the ETag of the chrono is required.",
        )
    if header.startswith(("W/", "w/")):
        return ""
    if len(header) >= 2 and header[0] == header[-1] == '"':
        return header[1:-1]
    return header


def with_local_tz(value: datetime | None, tz: tzinfo) -> datetime | None:
    """Attach ``tz`` to a naive datetime.

    :param value: Parsed datetime or ``None``.
    :param tz: Local timezone.
    :return: Aware datetime, or ``None``.
    """
    return value if value is None or value.tzinfo is not None else value.replace(tzinfo=tz)
```

  `require_if_match` is a dependency, so FastAPI runs it before validating the body. A request with no `If-Match` and an invalid body still gets 428, as with Flask.

- [ ] **Step 5: Router pattern.** Each `register_*(router: APIRouter, ...)` keeps its signature (`Blueprint` becomes `APIRouter`) and its closure style.
  - **Route shape.** Each route is an `async def` closure. It receives typed parameters (`Body`, `Query`, path) and calls the cheap rate limiter on the event loop. It then awaits `asyncio.to_thread(...)` on a **module-level sync helper** that holds the former handler body unchanged.
  - **What runs in the helper.** Anything that receives a repository, the container or a provider. `/health` and `/features` touch no lock and stay direct.
  - **Serialization.** Handlers return `dump(...)` dicts, so serialization keeps a single path (`model_dump(mode="json")`) and response bodies do not drift. Declaring `response_model` DTOs for OpenAPI is a later, separate step.
  - **Module docstrings.** Update the sentence to: "Route handlers are async closures defined inside ``register_*`` functions (see ``health``); repository calls run in a worker thread through ``asyncio.to_thread``."

  Reference implementation for `routers/stove.py`:

```python
def register_stove_routes(
    router: APIRouter,
    get_stove_repo: Callable[[], StoveRepository],
    limiter: SlidingWindowLimiter,
) -> None:
    """Add the stove routes.

    :param router: Protected API router.
    :param get_stove_repo: Lazy getter of the stove repository.
    :param limiter: Rate limiter applied to the start and stop commands.
    """

    @router.get("/stove/status")
    async def status(max_age_s: MaxAgeQuery = None) -> dict[str, Any]:
        return await asyncio.to_thread(_status, get_stove_repo(), max_age_s)

    @router.post("/stove/commands/start")
    async def start() -> dict[str, Any]:
        limiter.hit("start")
        return await asyncio.to_thread(_start, get_stove_repo())

    @router.post("/stove/commands/stop")
    async def stop(body: Annotated[StopRequest | None, Body()] = None) -> dict[str, Any]:
        limiter.hit("stop")
        return await asyncio.to_thread(_stop, get_stove_repo(), body or StopRequest())

    @router.put("/stove/setpoint")
    async def set_setpoint(body: SetpointRequest) -> dict[str, Any]:
        return await asyncio.to_thread(_set_setpoint, get_stove_repo(), body.value_c)

    @router.put("/stove/power")
    async def set_power(body: PowerRequest) -> dict[str, Any]:
        return await asyncio.to_thread(_set_power, get_stove_repo(), body.level)


def _status(repository: StoveRepository, max_age_s: float | None) -> dict[str, Any]:
    return dump(StoveStatusDto.load(repository, max_age_s))


def _start(repository: StoveRepository) -> dict[str, Any]:
    outcome = repository.start(source=EventSource.USER)
    return dump(CommandOutcomeDto.from_domain(outcome, repository))


def _stop(repository: StoveRepository, body: StopRequest) -> dict[str, Any]:
    outcome = repository.stop(
        source=EventSource.USER, confirm_ignition_stop=body.confirm_ignition_stop
    )
    return dump(CommandOutcomeDto.from_domain(outcome, repository))


def _set_setpoint(repository: StoveRepository, value_c: float) -> dict[str, Any]:
    changed = repository.set_setpoint(value_c, source=EventSource.USER)
    return dump(SettingChangeDto(changed=changed))


def _set_power(repository: StoveRepository, level: int) -> dict[str, Any]:
    changed = repository.set_power(level, source=EventSource.USER)
    return dump(SettingChangeDto(changed=changed))
```

- [ ] **Step 6: Remaining routers.** Apply the same pattern with these parameter mappings:

| Module | Route | Parameters | Worker-thread helper |
| --- | --- | --- | --- |
| `health` | GET `/health` | none | none, return `get_health()` |
| `features` | GET `/features` | none | none |
| `settings` | GET `/stove/clock`, POST `/stove/clock/sync`, GET `/stove/settings`, GET `/stove/maintenance` | none | the former bodies (maintenance raises `ApiProblemError` 404 `not_mapped` from the helper) |
| `settings` | PUT `/stove/settings/energy-saving`, PUT `/stove/settings/display-eco` | `body: EnergySavingRequest`, `body: DisplayEcoRequest` | the former bodies |
| `summary` | GET `/summary` | `max_age_s: MaxAgeQuery = None` | the whole former body (status load, providers, suspension getter) |
| `history` | GET `/history/temperatures` | `start: Annotated[datetime \| None, Query(alias="from")] = None`, `end: Annotated[datetime \| None, Query(alias="to")] = None`, `bucket_min: Annotated[int, Query(ge=1, le=MAX_BUCKET_MIN)] = DEFAULT_BUCKET_MIN` | defaults and `with_local_tz` as before, then the repository call |
| `history` | GET `/history/events` | `limit: Annotated[int, Query(ge=1, le=MAX_EVENT_LIMIT)] = DEFAULT_EVENT_LIMIT` | the former body |
| `programming` | GET `/programming`, POST `/programming/resume` | none | the former bodies |
| `programming` | POST `/programming/suspend` | `body: Annotated[SuspendRequest \| None, Body()] = None` | `body or SuspendRequest()`, `with_local_tz(until, tz)`, then the former body |

  For `routers/chrono.py`:
  - `register_chrono_api(router: APIRouter, container: Container)`.
  - `_config_response` splits in two:
    - a sync `_render_config(container, config) -> tuple[dict[str, Any], str]` that returns the `dump(dto)` payload and `config.fingerprint`, and runs in the worker thread;
    - an event-loop `_etag_response(payload, fingerprint) -> JSONResponse` that returns `JSONResponse(payload, headers={"ETag": f'"{fingerprint}"'})`.
  - GET `/chrono` keeps the "cache is opt-in" `# Reason:` comment and `0 if max_age_s is None else max_age_s`.
  - PUT `/chrono` declares `body: ChronoConfigRequest` and `expected: Annotated[str, Depends(require_if_match)]`. Its helper runs `body.to_domain(repository.get())`, then `repository.save(...)` with the `_readable` rewrite, then `_render_config`.
  - PUT `/chrono/blocks/{kind}/state` declares `body: BlockStateRequest` and keeps `kind: str` with the manual `ChronoBlockKind(kind)` check, so an unknown kind stays 404 `unknown_block` instead of becoming a 422.
  - Delete `_expected_fingerprint` and the werkzeug import.

- [ ] **Step 7: `service/features.py`.** Replace `flask.Blueprint` with `fastapi.APIRouter`, and rename `start_threads` to `background_loops`. In the class docstring, replace the `start_threads` sentence with: "``background_loops`` returns blocking loop callables; the API lifespan runs each one in a worker thread and sets the stop event on shutdown".

```python
    register_api: Callable[[APIRouter, Container], None]
    background_loops: Callable[[Container, threading.Event], Sequence[Callable[[], None]]] | None
    summary: Callable[[Container, datetime], ProgrammingSummaryDto] | None
    participant: Callable[[Container], Suspendable] | None
```

- [ ] **Step 8: `service/app.py`.** `create_api_app` keeps its keyword arguments, adds `background_loops` and `stop_event`, and returns a `FastAPI` app. The lifespan is a module-level async context manager bound with `functools.partial`.

```python
API_PREFIX: Final = "/api/v1"
_SHUTDOWN_WAIT_S: Final = 10.0
_LOGGER = logging.getLogger(__name__)


def create_api_app(
    *,
    settings: Settings,
    get_stove_repo: Callable[[], StoveRepository],
    get_history_repo: Callable[[], HistoryRepository],
    get_health: Callable[[], dict[str, Any]],
    feature_registrars: Sequence[Callable[[APIRouter], None]],
    summary_providers: Sequence[Callable[[datetime], ProgrammingSummaryDto]],
    background_loops: Sequence[Callable[[], None]],
    stop_event: threading.Event,
    get_suspension: Callable[[], SuspensionDto | None] | None = None,
    get_suspension_repo: Callable[[], SuspensionRepository] | None = None,
) -> FastAPI:
    """Build the JSON API application (served by uvicorn).

    :param background_loops: Blocking loops run in worker threads for the app lifetime.
    :param stop_event: Set on shutdown to make every background loop return.
    (other parameters unchanged)
    :return: The FastAPI application.
    """
    tz = ZoneInfo(settings.timezone)
    app = FastAPI(
        title="Stove Pilot API",
        lifespan=partial(_lifespan, loops=tuple(background_loops), stop_event=stop_event),
        openapi_url=f"{API_PREFIX}/openapi.json",
        docs_url=f"{API_PREFIX}/docs",
        redoc_url=None,
        redirect_slashes=False,
    )
    public = APIRouter(prefix=API_PREFIX)
    register_health_routes(public, get_health)
    protected = APIRouter(
        prefix=API_PREFIX,
        dependencies=api_key_dependencies(settings.api_key.get_secret_value()),
    )
    register_features_routes(protected, settings, tuple(FEATURES))
    register_stove_routes(protected, get_stove_repo, SlidingWindowLimiter())
    register_settings_routes(protected, get_stove_repo)
    register_summary_routes(
        protected, settings, get_stove_repo, summary_providers, get_suspension, tz
    )
    register_history_routes(protected, get_history_repo, tz)
    if get_suspension_repo is not None:
        register_programming_routes(protected, get_suspension_repo, get_stove_repo, tz)
    for registrar in feature_registrars:
        registrar(protected)
    app.include_router(public)
    app.include_router(protected)
    register_error_handlers(app)
    return app


@asynccontextmanager
async def _lifespan(
    _app: FastAPI, *, loops: Sequence[Callable[[], None]], stop_event: threading.Event
) -> AsyncIterator[None]:
    """Run each background loop in a worker thread, then stop and await them on shutdown."""
    tasks = [asyncio.create_task(asyncio.to_thread(loop)) for loop in loops]
    try:
        yield
    finally:
        stop_event.set()
        if tasks:
            done, pending = await asyncio.wait(tasks, timeout=_SHUTDOWN_WAIT_S)
            for task in done:
                if not task.cancelled() and task.exception() is not None:
                    _LOGGER.error("Background loop failed", exc_info=task.exception())
            if pending:
                _LOGGER.warning("%d background loop(s) still running at shutdown", len(pending))
```

  `redirect_slashes=False` keeps the Flask behavior: a trailing slash gives 404, not a 307. The `SlidingWindowLimiter` keeps its `threading.Lock`; its critical section is tiny and never blocks the event loop.

- [ ] **Step 9: `server_api.py`.** `main()` stays as it is: settings validation, secret redaction, `InstanceLock`, the API key warning, container build, and `container.close()` plus `lock.release()` in `finally`. `_serve` now builds the loop list and runs uvicorn on the calling thread.

```python
def _serve(settings: Settings, container: Container, stop_event: threading.Event) -> None:
    """Build the app and serve it with uvicorn until SIGINT/SIGTERM (uvicorn handles signals).

    :param settings: Application settings.
    :param container: Composed repositories.
    :param stop_event: Set by the lifespan on shutdown to stop every background loop.
    """
    poller = build_status_poller(settings, container, stop_event)
    loops: list[Callable[[], None]] = [poller.run_forever]
    registrars: list[Callable[[APIRouter], None]] = []
    providers: list[Callable[[datetime], ProgrammingSummaryDto]] = []
    no_suspension = container.suspension is None
    for spec in _enabled_features(settings):
        registrars.append(partial(spec.register_api, container=container))
        if spec.summary is not None:
            providers.append(partial(spec.summary, container))
        if spec.background_loops is not None:
            loops.extend(spec.background_loops(container, stop_event))
    app = create_api_app(
        settings=settings,
        get_stove_repo=lambda: container.stove,
        get_history_repo=lambda: container.history,
        get_health=partial(_health_snapshot, settings, poller),
        feature_registrars=registrars,
        summary_providers=providers,
        background_loops=loops,
        stop_event=stop_event,
        get_suspension=None if no_suspension else partial(_suspension_summary, container),
        get_suspension_repo=None if no_suspension else partial(_require_suspension, container),
    )
    config = uvicorn.Config(
        app,
        host=settings.api_host,
        port=settings.api_port,
        log_config=None,
        access_log=False,
        server_header=False,
        lifespan="on",
        timeout_graceful_shutdown=_GRACEFUL_SHUTDOWN_S,
    )
    uvicorn.Server(config).run()
```

  Changes around `_serve`:
  - Delete `_SERVER_THREADS`, `_JOIN_TIMEOUT_S`, and the `waitress`, `flask` and `install_signal_handlers` imports.
  - Add `_GRACEFUL_SHUTDOWN_S: Final = 10`.
  - `log_config=None` keeps the JSON root logger and the secret redaction filter for the uvicorn loggers.
  - The `InstanceLock` remains the guard against a second process; uvicorn runs a single worker when it is given an app object.

- [ ] **Manual verification** (simulator, port 8720, throwaway database):
  - **Startup:** `stove-pilot-api` starts. The log shows "Status poller started" and the uvicorn startup line, both in JSON, with no secret.
  - **Endpoints:** `curl http://127.0.0.1:8720/api/v1/health` returns 200, and `/api/v1/docs` renders.
  - **OpenAPI paths:** open `/api/v1/openapi.json`. The `paths` object has 22 keys, matching `/health`, `/features`, the stove routes of Step 5 and the table of Step 6. That is 23 operations, since `/chrono` has GET and PUT. Introspect paths this way, not through `app.routes`, because FastAPI wraps included routers.
  - **Shutdown:** Ctrl+C stops the API cleanly within about 10 s, logging "Status poller stopped" and "API stopped". A restart works immediately, which shows the lock file was released.
- [ ] **Checkpoint.**

### Task F3: Contract verification, cleanup and packaging

**Files:**
- Delete: `src/stove_pilot/service/api/parsing.py`, `src/stove_pilot/service/signals.py`
- Create (generated): `stove-pilot/contract/fastapi-nokey.json`, `stove-pilot/contract/fastapi-key.json`
- Modify: the API `requirements.lock`; the API `Dockerfile` only if it references Waitress; the compose healthcheck only if it uses HEAD

- [ ] **Step 1: Contract comparison.** Repeat Task F1 Steps 1 and 2 against the FastAPI build, writing `contract/fastapi-nokey.json` and `contract/fastapi-key.json`. Compare each with its Flask baseline.
  - Fix every `FAIL` line in the API, unless it is listed under "Accepted contract differences" and the user confirms it at the checkpoint.
  - `INFO` lines are expected only for differences 1 and 4.
- [ ] **Step 2: Cleanup.** Delete `parsing.py` and `signals.py`. Then `rg -n "flask|werkzeug|waitress|parse_body|query_float|query_int|query_datetime|install_signal_handlers|start_threads|Blueprint" stove-pilot/src` must return nothing.
- [ ] **Step 3: Packaging.**
  - Regenerate the API `requirements.lock` the same way Task 19 did.
  - Check that the API Dockerfile `CMD` is still `stove-pilot-api`.
  - Rebuild the API image with `docker compose build --no-cache api`. The UI image is untouched by this plan.
- [ ] **Step 4: Concurrency smoke.** While the API runs on the simulator, send 20 parallel status reads and, at the same time, one `PUT /stove/setpoint` from a second shell. The reads come from this command (it works on Windows PowerShell 5.1): `python -c "import httpx, concurrent.futures as cf; u='http://127.0.0.1:8720/api/v1/stove/status?max_age_s=0'; print(list(cf.ThreadPoolExecutor(20).map(lambda _: httpx.get(u, timeout=60).status_code, range(20))))"`.
  - Every request answers with 200 or a documented problem.
  - The log shows no deadlock warning, and the poller keeps logging on time.
- [ ] **Manual verification:**
  - Run `stove-pilot-ui` against the FastAPI API, with an API key configured on both sides. Repeat the UI smoke of the UI split plan, Task U3, and also check:
    - the chrono conflict;
    - a rate-limit toast after 7 quick starts on the simulator;
    - the stale banner when the API is stopped.
  - The user runs `docker compose up`: the API healthcheck turns green and the UI works.
- [ ] **Checkpoint:** the contract comparison is clean, or its differences are approved.

### Task F4: Design doc rev 7, old plan appendix, attended acceptance

**Files:**
- Modify: `zold/code/iot/docs/2026-10-21-stove-pilot-design.md` (rev 7)
- Modify: `zold/code/iot/docs/2026-10-21-stove-pilot-implementation-plan.md` (Appendix A only)
- Modify: `stove-pilot/README.md`

- [ ] **Step 1: Design doc rev 7.** Add a rev 7 note to the header, then update:
  - Section 4.1: the Mermaid API label becomes "Stove Pilot API - FastAPI".
  - Section 4.2: the API row becomes "FastAPI + uvicorn, single process".
  - ADR-005: async edge, sync core through `asyncio.to_thread`, poller as a lifespan task wrapping `run_forever`, and `FeatureSpec.background_loops`.
  - New ADR-006 "API framework": the decision record summary of this plan.
  - Section 11: the API key becomes a router dependency (`APIKeyHeader`), and the docs endpoints are unauthenticated.
- [ ] **Step 2: Old plan Appendix A.** In revival checklist item 4, replace `start_threads=start_schedule_runner` with `background_loops` returning the runner's blocking loop. Deferred Task 12's runner becomes a `run_forever`-style loop on the shared stop event. Touch nothing else.
- [ ] **Step 3: README.** Describe FastAPI and uvicorn, the `/api/v1/docs` page, and how to use the contract tool.
- [ ] **Step 4: Acceptance (attended, real stove, user present).**
  - Run the API with `STOVE_PILOT_GATEWAY=navel` together with the UI, and check the status, summary, history, Reglages (read only) and the chrono editor read.
  - With the user's explicit go-ahead, make one setpoint change and revert it.
  - Leave the stack running for 1 hour. The poller must keep `last_poll_at` fresh on `/health`, with no `Unexpected poll failure` lines and flat memory (Task Manager or `docker stats`).
- [ ] **Checkpoint:** migration accepted. **End of plan.**

---

## Self-review notes

- **Requirement coverage:**
  - FastAPI with async edge routes and the sync core through `asyncio.to_thread`: Task F2, Steps 5 and 6.
  - Poller as an asyncio task: Task F2, Step 8.
  - Contract preservation: Tasks F1 and F3.
  - Docs: Task F4.
  - Decoupled from the UI split: the start condition, and the UI project is untouched.
- **Risks:**
  - **Thread pool.** The default pool (`min(32, cpu + 4)` workers) permanently holds one thread for the poller. Navel traffic stays serialized by the client lock, so a burst of slow reads queues in the pool instead of in Waitress's 4 threads: same back-pressure, more headroom.
  - **Shutdown during a Navel request.** Shutdown waits for that request to finish, up to `navel_timeout_s`, before the event loop closes. The wait is bounded and logged.
  - **Client disconnects.** When a client disconnects and its handler is cancelled, the worker thread is not cancelled: the core call completes, matching Flask behavior for writes.
- **Effort:** about 2 to 2.5 working days: F1 a short session, F2 and F3 one and a half days, F4 half a day plus the attended hour. No infrastructure cost change.
