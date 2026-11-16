"""Serve the Sync API under ``/api`` and the built web interface at every other address.

The outer application mounts the API application at ``/api`` and answers every other address with
one route that accepts every method. The route applies decision 1 of ADR 0043
(``dev/adr/0043-the-web-interface-is-served-at-the-root-and-the-api-under-api.md``) in this order:

1. A method other than ``GET`` or ``HEAD``: the 404 envelope naming ``/api``.
2. No built web interface: a plain-text 404 for a browser, the envelope otherwise.
3. A regular file inside the built directory: that file.
4. A path under ``assets/``: the envelope, never the entry page.
5. A browser (``Accept`` includes ``text/html``): ``index.html``, so the web interface routes it.
6. Anything else: the envelope.

Starlette does not run the lifespan of a mounted application, so the outer lifespan enters the API
application's lifespan; otherwise the liveness reconciler loop would never start.
"""

from __future__ import annotations

import base64
import hashlib
import os
import stat
from contextlib import asynccontextmanager
from html.parser import HTMLParser
from typing import TYPE_CHECKING

import structlog
from starlette.applications import Starlette
from starlette.responses import JSONResponse, PlainTextResponse, Response
from starlette.routing import Mount, Route, request_response
from starlette.staticfiles import StaticFiles

from .app import _error_content
from .models import ErrorDetail, ErrorEnvelope

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable
    from pathlib import Path

    from fastapi import FastAPI
    from starlette.requests import Request
    from starlette.types import Receive, Scope, Send

API_PREFIX = "/api"
NOT_FOUND_MESSAGE = "the Sync API is served under /api"
NO_WEB_INTERFACE_MESSAGE = "The web interface is not included in this install. The Sync API is served under /api."
INDEX_CACHE_CONTROL = "no-cache"
ASSET_CACHE_CONTROL = "public, max-age=31536000, immutable"

logger = structlog.get_logger(__name__)


class _InlineScriptCollector(HTMLParser):
    """Collect the exact content of every ``<script>`` element that has no ``src`` attribute."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=False)
        self.scripts: list[str] = []
        self._current: list[str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "script" and not any(name == "src" for name, _value in attrs):
            self._current = []

    def handle_data(self, data: str) -> None:
        if self._current is not None:
            self._current.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "script" and self._current is not None:
            self.scripts.append("".join(self._current))
            self._current = None


def _inline_script_hashes(index_html: str) -> tuple[str, ...]:
    """Return the CSP source of each inline script of ``index_html``, in document order."""
    collector = _InlineScriptCollector()
    collector.feed(index_html)
    collector.close()
    return tuple(
        f"'sha256-{base64.b64encode(hashlib.sha256(script.encode('utf-8')).digest()).decode('ascii')}'"
        for script in collector.scripts
    )


def content_security_policy(script_hashes: tuple[str, ...]) -> str:
    """Return the ``Content-Security-Policy`` of the web interface, allowing the given inline scripts.

    ``style-src`` allows inline styles: React Aria's press handling inserts a ``<style>`` element
    into the page head at run time, which ``style-src 'self'`` blocks. Scripts stay limited to the
    service and the hashed inline scripts.
    """
    script_src = " ".join(("'self'", *script_hashes))
    return (
        f"default-src 'self'; script-src {script_src}; style-src 'self' 'unsafe-inline'; "
        "font-src 'self' https://rsms.me; "
        "img-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'none'; "
        "form-action 'self'; frame-ancestors 'none'"
    )


def _not_found_envelope() -> JSONResponse:
    envelope = ErrorEnvelope(error=ErrorDetail(code="not-found", message=NOT_FOUND_MESSAGE, status=404))
    return JSONResponse(status_code=404, content=_error_content(envelope))


def _accepts_html(request: Request) -> bool:
    return "text/html" in request.headers.get("accept", "").lower()


def _relative_path(path: str) -> str:
    """Normalize a request path the way ``StaticFiles.get_path`` does, with ``..`` and ``.`` folded."""
    return os.path.normpath("/".join(part for part in path.split("/") if part))


def _under_assets(raw_path: str, relative: str) -> bool:
    """Return whether a request path is under ``assets/``, before or after ``..`` and ``.`` are folded.

    Empty segments are dropped first, so ``//assets/x`` counts. The unfolded form keeps
    ``assets/../../x`` under ``assets/``, so a browser gets the envelope rather than the entry page.
    """
    collapsed = "/".join(part for part in raw_path.split("/") if part)
    return collapsed.startswith("assets/") or relative.startswith("assets/")


class _EveryMethod:
    """Hand a request of any method to the web interface endpoint.

    A ``Route`` with a function endpoint accepts only the methods it lists, and Starlette answers any
    other method with a plain 405 that has no security headers. An endpoint that is not a function
    leaves ``Route.methods`` unset, so every method reaches the endpoint and gets the contract's answer.
    """

    def __init__(self, endpoint: Callable[[Request], Response]) -> None:
        self._app = request_response(endpoint)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        await self._app(scope, receive, send)


def create_site(api: FastAPI, directory: Path | None) -> Starlette:
    """Build the service application: the API under ``/api`` and the web interface everywhere else.

    Args:
        api: The Sync API application from ``create_app``; its routes stay unprefixed.
        directory: The resolved directory of the built web interface, or ``None`` when there is none.
            The caller checked that it holds a regular file ``index.html``.
    """
    files: StaticFiles | None = None
    index_file = ""
    script_hashes: tuple[str, ...] = ()
    if directory is not None:
        resolved = directory.resolve()
        try:
            index_html = (resolved / "index.html").read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as error:
            # Never the error text: a decoding error quotes bytes of the file.
            logger.warning(
                "web interface index.html cannot be read; the web interface is not served",
                path=str(resolved / "index.html"),
                error_type=type(error).__name__,
            )
        else:
            files = StaticFiles(directory=resolved, check_dir=False)
            index_file = str(resolved / "index.html")
            script_hashes = _inline_script_hashes(index_html)
    security_headers = {
        "Content-Security-Policy": content_security_policy(script_hashes),
        "X-Content-Type-Options": "nosniff",
        "X-Frame-Options": "DENY",
        "Referrer-Policy": "no-referrer",
    }

    def lookup(built: StaticFiles, path: str) -> tuple[str, os.stat_result | None]:
        try:
            return built.lookup_path(path)
        except (OSError, ValueError):
            # A name too long, a null byte or an unreadable entry: never a file to send.
            return "", None

    def file_answer(built: StaticFiles, request: Request, found: tuple[str, os.stat_result], relative: str) -> Response:
        full_path, stat_result = found
        response = built.file_response(full_path, stat_result, request.scope)
        if full_path == index_file:
            response.headers["Cache-Control"] = INDEX_CACHE_CONTROL
        elif relative.startswith("assets/"):
            response.headers["Cache-Control"] = ASSET_CACHE_CONTROL
        return response

    def from_directory(built: StaticFiles, request: Request) -> Response | None:
        """Answer rows 3 to 5 of the contract; ``None`` leaves the request to the envelope."""
        raw_path: str = request.path_params.get("path", "")
        relative = _relative_path(raw_path)
        full_path, stat_result = lookup(built, relative)
        if stat_result is not None and stat.S_ISREG(stat_result.st_mode):
            return file_answer(built, request, (full_path, stat_result), relative)
        if _under_assets(raw_path, relative) or not _accepts_html(request):
            return None
        index_path, index_stat = lookup(built, "index.html")
        if index_stat is None or not stat.S_ISREG(index_stat.st_mode):
            return None
        return file_answer(built, request, (index_path, index_stat), "index.html")

    def decide(request: Request) -> Response:
        if request.method not in {"GET", "HEAD"}:
            return _not_found_envelope()
        if files is None:
            if _accepts_html(request):
                return PlainTextResponse(NO_WEB_INTERFACE_MESSAGE, status_code=404)
            return _not_found_envelope()
        return from_directory(files, request) or _not_found_envelope()

    # A plain function: Starlette runs it in its thread pool, so the file lookups do not block the loop.
    def web_interface(request: Request) -> Response:
        response = decide(request)
        response.headers.update(security_headers)
        return response

    @asynccontextmanager
    async def lifespan(_application: Starlette) -> AsyncIterator[None]:
        async with api.router.lifespan_context(api):
            yield

    return Starlette(
        routes=[
            Mount(API_PREFIX, app=api),
            Route("/{path:path}", _EveryMethod(web_interface)),
        ],
        lifespan=lifespan,
    )
