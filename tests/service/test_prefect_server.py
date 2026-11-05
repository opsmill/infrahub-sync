"""The Prefect server factory returns Prefect's own application, built from the current settings.

No case here starts the application's lifespan, so no database migration and no
background service runs in the unit tier: each application is built and its
routes are read, and the few requests made go through a test client that is never
entered as a context manager. The health check against a running server is a
property of the Compose stacks and is checked there.

Prefect caches each application it builds by a hash of the settings. Every case
replaces that cache with an empty one, so a case reads the application its own
settings build and never one another case or another test module built first.
"""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING

import pytest

if sys.version_info < (3, 11):
    pytest.skip("the Sync service supports Python 3.11 and later", allow_module_level=True)

pytest.importorskip("prefect")

from fastapi import FastAPI
from fastapi.testclient import TestClient
from prefect.server.api import server
from prefect.settings import (
    PREFECT_API_DATABASE_CONNECTION_URL,
    PREFECT_UI_ENABLED,
    PREFECT_UI_STATIC_DIRECTORY,
    temporary_settings,
)

from infrahub_sync.service.prefect_server import create_prefect_app

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

HTML = {"Accept": "text/html"}


@pytest.fixture(autouse=True)
def _empty_application_caches(monkeypatch: pytest.MonkeyPatch) -> None:
    """Give each case empty Prefect application caches."""
    monkeypatch.setattr(server, "APP_CACHE", {})
    monkeypatch.setattr(server, "API_APP_CACHE", {})


@pytest.fixture
def database(tmp_path: Path) -> str:
    """Name a SQLite database under the case's directory; building the application never opens it."""
    return f"sqlite+aiosqlite:///{tmp_path / 'prefect.db'}"


@pytest.fixture
def ui_disabled(database: str) -> Iterator[FastAPI]:
    """Build the application with the UI turned off."""
    with temporary_settings({PREFECT_API_DATABASE_CONNECTION_URL: database, PREFECT_UI_ENABLED: False}):
        yield create_prefect_app()


@pytest.fixture
def ui_enabled(database: str, tmp_path: Path) -> Iterator[FastAPI]:
    """Build the application with the UI turned on and its files copied to a temporary directory."""
    static = tmp_path / "ui"
    static.mkdir()
    with temporary_settings(
        {
            PREFECT_API_DATABASE_CONNECTION_URL: database,
            PREFECT_UI_ENABLED: True,
            PREFECT_UI_STATIC_DIRECTORY: str(static),
        }
    ):
        yield create_prefect_app()


def mounted(app: FastAPI, name: str) -> FastAPI:
    """Return the sub-application mounted under `name`."""
    for route in app.routes:
        if getattr(route, "name", None) == name:
            return route.app  # ty: ignore[unresolved-attribute] -- a Mount carries its application
    pytest.fail(f"no route named {name!r} is mounted")


def test_the_factory_returns_a_fastapi_application(ui_disabled: FastAPI) -> None:
    """The factory returns the application uvicorn serves."""
    assert isinstance(ui_disabled, FastAPI)


def test_the_application_serves_the_health_check_under_the_api_prefix(ui_disabled: FastAPI) -> None:
    """The REST API is mounted at `/api` and includes the `/health` route Compose probes."""
    api = mounted(ui_disabled, "api")
    assert any(getattr(route, "path", None) == "/health" for route in api.routes)
    assert any(getattr(route, "path", None) == "/api" for route in ui_disabled.routes)


def test_with_the_ui_disabled_no_ui_is_served(ui_disabled: FastAPI) -> None:
    """The UI settings report no UI, and a browser request for `/` finds nothing."""
    client = TestClient(ui_disabled)
    assert client.get("/ui-settings").json()["available_uis"] == []
    assert client.get("/", headers=HTML).status_code == 404


def test_with_the_ui_enabled_the_ui_is_served_from_the_static_directory(ui_enabled: FastAPI, tmp_path: Path) -> None:
    """The UI settings report a UI, `/` answers with its page, and its files sit in the configured directory."""
    client = TestClient(ui_enabled)
    assert client.get("/ui-settings").json()["available_uis"] != []
    page = client.get("/", headers=HTML, follow_redirects=True)
    assert page.status_code == 200
    assert page.headers["content-type"].startswith("text/html")
    assert any((tmp_path / "ui").iterdir())
