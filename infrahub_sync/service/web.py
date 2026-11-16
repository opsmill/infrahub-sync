"""Serve the Sync API under ``/api`` and the built web interface at every other address.

The outer application mounts the API application at ``/api`` and answers every other address with
one route, following the decision order of the HTTP routing contract of spec 017:

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

from starlette.applications import Starlette
from starlette.responses import JSONResponse, PlainTextResponse, Response
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from .app import _error_content
from .models import ErrorDetail, ErrorEnvelope

if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from pathlib import Path

    from fastapi import FastAPI
    from starlette.requests import Request

API_PREFIX = "/api"
NOT_FOUND_MESSAGE = "the Sync API is served under /api"
NO_WEB_INTERFACE_MESSAGE = "The web interface is not included in this install. The Sync API is served under /api."
INDEX_CACHE_CONTROL = "no-cache"
ASSET_CACHE_CONTROL = "public, max-age=31536000, immutable"
_ALL_METHODS = ["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"]


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
    """Return the ``Content-Security-Policy`` of the web interface, allowing the given inline scripts."""
    script_src = " ".join(("'self'", *script_hashes))
    return (
        f"default-src 'self'; script-src {script_src}; style-src 'self'; font-src 'self' https://rsms.me; "
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
        files = StaticFiles(directory=resolved, check_dir=False)
        index_file = str(resolved / "index.html")
        script_hashes = _inline_script_hashes((resolved / "index.html").read_text(encoding="utf-8"))
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
        if raw_path.startswith("assets/") or not _accepts_html(request):
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
            Route("/{path:path}", web_interface, methods=_ALL_METHODS),
        ],
        lifespan=lifespan,
    )
