"""Development-only supervisor: restart Waitress when a watched file is saved.

Waitress has no reload. Gunicorn's ``--reload`` does not run on Windows, which is the
current development machine. This file is the whole feature. Production does not import
it unless ``STOVE_PILOT_DEV`` is true, and Docker and the scheduled task leave that unset.

To remove it, delete this module and the seams below. Nothing else depends on it.

- ``common/settings.py``: the ``dev`` field
- ``server_api.py`` and ``server_ui.py``: the ``if settings.dev`` block
- ``service/app.py``: the ``apply_ui_dev`` call
- ``templates/base.html``: the ``dev_mode`` branch
- ``deploy/windows/run.ps1``: the ``-Dev`` switch
- ``.env.example`` and ``README.md``: the dev-reload notes
"""

import logging
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Literal

from flask import Flask

from stove_pilot.common.logging import configure_logging
from stove_pilot.common.settings import get_settings

_LOGGER = logging.getLogger(__name__)

# Reason: the child must not supervise itself. Not a Settings field (extra env is ignored).
DEV_CHILD_ENV = "STOVE_PILOT_DEV_CHILD"
_POLL_S = 1.0
_STOP_TIMEOUT_S = 10
_Kind = Literal["api", "ui"]
_UI_SUFFIXES = frozenset({".html", ".js", ".css", ".json"})
_API_SUFFIXES = frozenset({".toml"})
_Signature = dict[str, tuple[int, int]]


def enter(kind: _Kind) -> int | None:
    """Supervise this process, or tell the caller to serve.

    :param kind: ``api`` or ``ui``.
    :return: The supervisor exit code, or ``None`` when this process is the server.
    """
    if os.environ.get(DEV_CHILD_ENV) == "1":
        return None
    return supervise(kind)


def apply_ui_dev(app: Flask) -> None:
    """Read templates and static files on each request, and skip the service worker.

    :param app: UI application.
    """
    app.config["TEMPLATES_AUTO_RELOAD"] = True
    app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 0
    app.jinja_env.globals["dev_mode"] = True


def supervise(kind: _Kind) -> int:
    """Run the server in a child and restart it when a watched file changes.

    :param kind: ``api`` watches Python outside the UI plus profiles; ``ui`` watches
        Python plus templates, scripts, stylesheets and the French catalog.
    :return: Exit code of the child when it stops on its own.
    """
    configure_logging("INFO")
    settings = get_settings()
    if settings.gateway == "navel":
        _LOGGER.warning(
            "Dev reload is on with the navel gateway: a save restarts the process "
            "and reconnects to the stove"
        )
    env = os.environ.copy()
    env[DEV_CHILD_ENV] = "1"
    command = _command()
    _LOGGER.info("Dev reload is on for the %s", kind)
    child: subprocess.Popen[bytes] | None = None
    try:
        while True:
            child = subprocess.Popen(command, env=env)
            baseline = _snapshot(_watched(kind), {})
            restart = False
            while child.poll() is None:
                time.sleep(_POLL_S)
                current = _snapshot(_watched(kind), baseline)
                if current != baseline:
                    _LOGGER.info(
                        "Dev reload: %s changed, restarting the %s",
                        _describe(baseline, current),
                        kind,
                    )
                    restart = True
                    _stop(child)
                    break
            code = child.wait()
            child = None
            if not restart:
                return code
    except KeyboardInterrupt:
        return 0
    finally:
        if child is not None and child.poll() is None:
            _stop(child)


def _command() -> list[str]:
    """Rebuild the command that launched this process.

    :return: Arguments for the child server.
    """
    argv = list(sys.orig_argv) or [sys.argv[0]]
    launched = Path(argv[0])
    # Reason: a Windows console script is an .exe launcher. Re-run it, not a bare python.
    if (
        os.name == "nt"
        and launched.suffix.lower() == ".exe"
        and launched.name.lower() not in {"python.exe", "pythonw.exe"}
        and launched.is_file()
    ):
        return argv
    return [sys.executable, *argv[1:]]


def _package_root() -> Path:
    """Return the ``stove_pilot`` package directory.

    :return: Absolute package path.
    """
    return Path(__file__).resolve().parents[1]


def _is_ui_source(path: Path) -> bool:
    """Tell whether a path lives under ``service/ui``.

    :param path: File path.
    :return: ``True`` for the UI package.
    """
    parts = path.parts
    return any(
        part == "service" and parts[index + 1] == "ui" for index, part in enumerate(parts[:-1])
    )


def _watched(kind: _Kind) -> list[Path]:
    """List the files that should restart this process.

    :param kind: Which server is being supervised.
    :return: Existing files. The list is rebuilt on each poll so new files are seen.
    """
    found: list[Path] = []
    for path in _package_root().rglob("*"):
        if not path.is_file() or "__pycache__" in path.parts:
            continue
        suffix = path.suffix.lower()
        ui_source = _is_ui_source(path)
        if suffix == ".py" and not (kind == "api" and ui_source):
            found.append(path)
        elif kind == "ui" and suffix in _UI_SUFFIXES:
            found.append(path)
        elif kind == "api" and suffix in _API_SUFFIXES:
            found.append(path)
    env_file = Path.cwd() / ".env"
    if env_file.is_file():
        found.append(env_file)
    return found


def _snapshot(paths: list[Path], previous: _Signature) -> _Signature:
    """Record size and mtime. A file that cannot be stated keeps its previous stamp.

    :param paths: Files to observe.
    :param previous: Last snapshot, used when a save still holds the file.
    :return: Path to ``(mtime_ns, size)``.
    """
    current: _Signature = {}
    for path in paths:
        key = str(path)
        try:
            stat = path.stat()
        except OSError:
            if key in previous:
                current[key] = previous[key]
            continue
        current[key] = (stat.st_mtime_ns, stat.st_size)
    return current


def _describe(before: _Signature, after: _Signature) -> str:
    """Name one changed file for the log.

    :param before: Previous snapshot.
    :param after: Current snapshot.
    :return: A file name, with a count when several changed.
    """
    changed = [path for path in set(before) | set(after) if before.get(path) != after.get(path)]
    if not changed:
        return "a watched file"
    name = Path(changed[0]).name
    if len(changed) == 1:
        return name
    return f"{name} (+{len(changed) - 1})"


def _stop(child: subprocess.Popen[bytes]) -> None:
    """Stop the child and the process tree the Windows launcher may have spawned.

    :param child: Supervised server process.
    """
    if child.poll() is not None:
        return
    if sys.platform == "win32":
        subprocess.run(
            ["taskkill", "/PID", str(child.pid), "/T", "/F"],
            check=False,
            capture_output=True,
        )
    else:
        child.terminate()
    try:
        child.wait(timeout=_STOP_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        child.kill()
        child.wait(timeout=_STOP_TIMEOUT_S)
