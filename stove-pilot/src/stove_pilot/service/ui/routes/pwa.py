"""PWA routes: web app manifest, service worker and offline page."""

import hashlib
import json
from pathlib import Path
from typing import Any, Final

from flask import Flask, Response, render_template

from stove_pilot.service.ui.i18n import t
from stove_pilot.service.ui.security import WIDGET_PATH

THEME_COLOR: Final = "#9a5b2c"
BACKGROUND_COLOR: Final = "#fff7ed"
MANIFEST_MIMETYPE: Final = "application/manifest+json"
SERVICE_WORKER_PATH: Final = "/sw.js"
OFFLINE_PATH: Final = "/offline"
MANIFEST_PATH: Final = "/manifest.webmanifest"
_JS_MIMETYPE: Final = "text/javascript"
_ASSET_VERSION_LENGTH: Final = 12
_ICON_SIZES: Final = (192, 512)
_MASKABLE_ICON: Final = "icon-maskable-512.png"
# Reason: the shell assets loaded by base.html and the page templates; keep in sync with them.
# Every entry must exist, otherwise cache.addAll fails and the worker never installs.
_PRECACHED_STATIC: Final = (
    "css/app.css",
    "js/theme.js",
    "js/htmx.min.js",
    "js/refresh.js",
    "js/chart-hover.js",
    "js/sw-register.js",
    "js/settings.js",
    "js/chrono-logic.js",
    "js/chrono-preview.js",
    "js/chrono-editor.js",
    "icons/icon-192.png",
    "icons/icon-512.png",
    "icons/icon-maskable-512.png",
    "images/favicon-32.png",
    "images/calcifer-144.png",
)
_VERSIONED_TEMPLATES: Final = ("sw.js", "offline.html")


def _manifest() -> dict[str, Any]:
    """Build the web app manifest.

    :return: Manifest members.
    """
    return {
        "id": "/",
        "name": t("app.title"),
        "short_name": t("app.short_title"),
        "description": t("app.manifest_description"),
        "lang": "fr",
        "start_url": "/",
        "scope": "/",
        "display": "standalone",
        "theme_color": THEME_COLOR,
        "background_color": BACKGROUND_COLOR,
        "icons": [
            {
                "src": f"/static/icons/icon-{size}.png",
                "sizes": f"{size}x{size}",
                "type": "image/png",
                "purpose": "any",
            }
            for size in _ICON_SIZES
        ]
        + [
            {
                "src": f"/static/icons/{_MASKABLE_ICON}",
                "sizes": "512x512",
                "type": "image/png",
                "purpose": "maskable",
            }
        ],
    }


def _static_root(app: Flask) -> Path:
    """Return the static folder and fail fast if a precached file is missing.

    :param app: UI application.
    :return: Static folder path.
    :raises FileNotFoundError: When a precached static file does not exist.
    """
    root = Path(app.static_folder or "")
    missing = [name for name in _PRECACHED_STATIC if not (root / name).is_file()]
    if missing:
        raise FileNotFoundError(f"Service worker precache lists missing static files: {missing}")
    return root


def _template_source(app: Flask, name: str) -> str:
    """Read a template source through the application loader.

    :param app: UI application.
    :param name: Template name.
    :return: Template source text.
    """
    loader = app.jinja_env.loader
    if loader is None:
        raise RuntimeError("The UI Jinja environment has no template loader")
    source, _, _ = loader.get_source(app.jinja_env, name)
    return source


def _asset_version(app: Flask, static_root: Path) -> str:
    """Hash the precached files, the PWA templates and the manifest (changes on every asset deploy).

    :param app: UI application.
    :param static_root: Static folder path.
    :return: Short hexadecimal version.
    """
    digest = hashlib.sha256()
    for name in _PRECACHED_STATIC:
        digest.update(name.encode("utf-8"))
        digest.update((static_root / name).read_bytes())
    for name in _VERSIONED_TEMPLATES:
        digest.update(_template_source(app, name).encode("utf-8"))
    digest.update(json.dumps(_manifest(), sort_keys=True).encode("utf-8"))
    return digest.hexdigest()[:_ASSET_VERSION_LENGTH]


def register_pwa_routes(app: Flask) -> None:
    """Add the manifest, the service worker, the offline page and ``/favicon.ico``.

    :param app: UI application.
    """
    version = _asset_version(app, _static_root(app))
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

    @app.get("/favicon.ico")
    def favicon() -> Response:
        # Reason: browsers probe the site root for the icon whatever the page declares.
        return app.send_static_file("favicon.ico")
