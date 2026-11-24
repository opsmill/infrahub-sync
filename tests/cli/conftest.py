"""Shared fixtures of the CLI tests."""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING

import httpx
import pytest

from infrahub_sync.client import SyncClient

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

VERSION = {"server_version": "3.0.0", "api_versions": ["v3-unstable"], "stability": "unstable"}


@pytest.fixture(autouse=True)
def _restore_cli_package_logging() -> Iterator[None]:
    """Undo the level and the handler that the CLI's `main` sets on the `infrahub_sync` logger.

    `main` leaves the package logger at the level of the invocation, for example WARNING with
    `--quiet`. Without this restore, a later test in the same process that reads an INFO record
    of the package through `caplog` sees nothing, and the result depends on the test order.
    """
    package_logger = logging.getLogger("infrahub_sync")
    level = package_logger.level
    handlers = list(package_logger.handlers)
    yield
    package_logger.setLevel(level)
    for handler in list(package_logger.handlers):
        if handler not in handlers:
            package_logger.removeHandler(handler)
            handler.close()


@pytest.fixture(autouse=True)
def session_directory(tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point the session file of the CLI at a temporary directory, so no test reads or writes the user's own."""
    directory = tmp_path_factory.mktemp("app-dir") / "infrahub-sync"
    monkeypatch.setattr("infrahub_sync.cli_session.default_directory", lambda: directory)
    return directory


class FakeSyncAPI:
    """Answer the requests of a real `SyncClient` and record them, in place of a Sync API.

    ``answers`` maps ``"METHOD /path"`` (the path without ``/api``) to the answer of the route.
    A route without an answer gets ``404 not-found``. ``GET /version`` is always answered and
    never recorded.
    """

    def __init__(self) -> None:
        self.answers: dict[str, httpx.Response] = {}
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        route = f"{request.method} {request.url.path.removeprefix('/api')}"
        if route == "GET /version":
            return httpx.Response(200, json=VERSION)
        self.requests.append(request)
        return self.answers.get(route, self.error(404, "not-found"))

    @staticmethod
    def error(status: int, code: str, message: str = "text of the Sync API") -> httpx.Response:
        """Return the error answer of the Sync API for a status and a code."""
        return httpx.Response(status, json={"error": {"code": code, "message": message, "status": status}})

    @property
    def routes(self) -> list[str]:
        """Return every recorded request as ``"METHOD /path"``."""
        return [f"{request.method} {request.url.path.removeprefix('/api')}" for request in self.requests]

    @property
    def bearers(self) -> list[str | None]:
        """Return the `Authorization` header of every recorded request."""
        return [request.headers.get("Authorization") for request in self.requests]

    def body(self, index: int = -1) -> object:
        """Return the JSON body of one recorded request."""
        return json.loads(self.requests[index].content)


@pytest.fixture
def sync_api(monkeypatch: pytest.MonkeyPatch) -> FakeSyncAPI:
    """Make every client that the CLI builds send its requests to a `FakeSyncAPI`."""
    api = FakeSyncAPI()

    class _Client(SyncClient):
        def __init__(
            self,
            service_url: str,
            token: str,
            *,
            timeout: float = 30.0,
            transport: httpx.BaseTransport | None = None,  # noqa: ARG002 - the fake replaces any transport.
        ) -> None:
            super().__init__(service_url, token, timeout=timeout, transport=httpx.MockTransport(api))

    monkeypatch.setattr("infrahub_sync.cli.SyncClient", _Client)
    return api
