"""The application served at the Sync API address: the Sync API under ``/api`` and the web interface.

``create_app()`` builds the Sync API with its routes at its own root. ``create_site()`` mounts it at
``/api`` and serves the built web interface for every other path, so the page paths of the web
interface, such as ``/runs/{run_id}``, never collide with Sync API routes.

Starlette does not run the lifespan of a mounted application, so the site enters the Sync API's
lifespan inside its own. Without that, the run liveness checks would never start.
"""

from __future__ import annotations

import base64
import hashlib
import re
from contextlib import asynccontextmanager
from html import escape
from typing import TYPE_CHECKING

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from starlette.datastructures import MutableHeaders

from .settings import WEB_DIRECTORY_ENV

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator
    from pathlib import Path

    from starlette.types import ASGIApp, Message, Receive, Scope, Send

API_PATH_PREFIX = "/api"
IMAGE_REFERENCE = "registry.opsmill.io/opsmill/infrahub-sync"

# The web interface loads its font from rsms.me (`frontend/index.html`); everything else is its own.
_FONT_SOURCES = "'self' https://rsms.me"
_INLINE_SCRIPT = re.compile(r"<script(?![^>]*\bsrc\s*=)[^>]*>(.*?)</script>", re.DOTALL | re.IGNORECASE)
_IMMUTABLE = "public, max-age=31536000, immutable"

_NO_WEB_INTERFACE_PAGE = f"""<!doctype html>
<html lang="en">
  <head>
    <meta charset="UTF-8" />
    <title>Infrahub Sync API</title>
  </head>
  <body>
    <h1>Infrahub Sync API</h1>
    <p>This installation serves the Sync API at <a href="{API_PATH_PREFIX}/docs">{API_PATH_PREFIX}</a>.
    It does not include the web interface.</p>
    <p>The web interface is included in the released container image,
    <code>{escape(IMAGE_REFERENCE)}</code>.</p>
  </body>
</html>
"""


def is_api_path(path: str) -> bool:
    """Return whether a request path belongs to the Sync API mount."""
    return path == API_PATH_PREFIX or path.startswith(f"{API_PATH_PREFIX}/")


def inline_script_hashes(index_html: str) -> tuple[str, ...]:
    """Return the content security policy source of each inline script in ``index_html``."""
    return tuple(
        f"'sha256-{base64.b64encode(hashlib.sha256(body.encode()).digest()).decode()}'"
        for body in _INLINE_SCRIPT.findall(index_html)
    )


def content_security_policy(script_hashes: tuple[str, ...]) -> str:
    """Return the policy of every response outside ``/api``: own files, the listed inline scripts, no frames."""
    script_sources = " ".join(("'self'", *script_hashes))
    return "; ".join(
        (
            "default-src 'self'",
            f"script-src {script_sources}",
            "style-src 'self' 'unsafe-inline'",
            "img-src 'self' data:",
            f"font-src {_FONT_SOURCES}",
            "connect-src 'self'",
            "frame-ancestors 'none'",
            "base-uri 'self'",
            "form-action 'self'",
        )
    )


class WebInterfaceHeaders:
    """Add the security and cache headers to every response for a path other than ``/api`` or below it.

    ``/api/docs`` loads Swagger UI from a CDN, so the Sync API keeps its own headers. Only a built
    file under ``/assets/`` is cached as immutable: an error, or ``index.html`` served as the
    fallback for a missing file, is never kept.
    """

    def __init__(self, app: ASGIApp, *, policy: str) -> None:
        self._app = app
        self._policy = policy

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Pass Sync API requests through unchanged and add the headers to every other response."""
        if scope["type"] != "http" or is_api_path(scope["path"]):
            await self._app(scope, receive, send)
            return
        is_asset = scope["path"].startswith("/assets/")

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                headers["Content-Security-Policy"] = self._policy
                headers["X-Frame-Options"] = "DENY"
                headers["X-Content-Type-Options"] = "nosniff"
                is_built_file = message["status"] == 200 and not headers.get("content-type", "").startswith("text/html")
                headers["Cache-Control"] = _IMMUTABLE if is_asset and is_built_file else "no-cache"
            await send(message)

        await self._app(scope, receive, send_with_headers)


def create_site(api: FastAPI, *, web_directory: Path | None) -> FastAPI:
    """Return the application that serves ``api`` under ``/api`` and the web interface everywhere else.

    Raises:
        ValueError: ``web_directory`` is set but does not hold a built web interface; the message
            names ``INFRAHUB_SYNC_SERVICE_WEB_DIRECTORY``.
    """

    @asynccontextmanager
    async def lifespan(_site: FastAPI) -> AsyncGenerator[None]:
        async with api.router.lifespan_context(api):
            yield

    site = FastAPI(docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
    site.mount(API_PATH_PREFIX, api)

    if web_directory is None:
        _serve_no_web_interface_page(site)
        script_hashes: tuple[str, ...] = ()
    else:
        try:
            site.frontend("/", directory=web_directory, fallback="index.html", check_dir=True)
            index_html = (web_directory / "index.html").read_text(encoding="utf-8")
        except (RuntimeError, OSError) as exc:
            msg = (
                f"{WEB_DIRECTORY_ENV} must name a readable directory that contains index.html: {web_directory} ({exc})"
            )
            raise ValueError(msg) from exc
        script_hashes = inline_script_hashes(index_html)

    site.add_middleware(WebInterfaceHeaders, policy=content_security_policy(script_hashes))
    return site


def _serve_no_web_interface_page(site: FastAPI) -> None:
    """Answer ``/`` with a page that names the released image, and every other path with 404 and that page."""

    @site.get("/", include_in_schema=False)
    def no_web_interface() -> HTMLResponse:
        return HTMLResponse(_NO_WEB_INTERFACE_PAGE)

    @site.exception_handler(404)
    async def not_found(_request: Request, _exc: Exception) -> HTMLResponse:  # noqa: RUF029
        return HTMLResponse(_NO_WEB_INTERFACE_PAGE, status_code=404)
