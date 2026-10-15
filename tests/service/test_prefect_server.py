"""The Prefect server of the Sync image is Prefect's own application, started like the CLI.

``create_prefect_server()`` must return ``create_app(final=True)`` and add nothing, and
``main()`` must pass uvicorn the values that ``prefect server start`` passes
(``prefect/cli/_server_utils.py:341-352`` in Prefect 3.8.6).
"""

from __future__ import annotations

import json
import os
import subprocess  # noqa: S404 -- fixed argv interpreter probes
import sys
from typing import TYPE_CHECKING, Any

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("prefect")

from prefect.settings import (
    PREFECT_SERVER_API_HOST,
    PREFECT_SERVER_API_KEEPALIVE_TIMEOUT,
    PREFECT_SERVER_API_PORT,
    PREFECT_SERVER_API_WEBSOCKET_PING_INTERVAL,
    PREFECT_SERVER_API_WEBSOCKET_PING_TIMEOUT,
    PREFECT_SERVER_LOGGING_LEVEL,
    temporary_settings,
)

from infrahub_sync.service import prefect_server

if TYPE_CHECKING:
    from pathlib import Path

FACTORY = "infrahub_sync.service.prefect_server:create_prefect_server"

# Each application is built in its own interpreter: ``final=True`` lets Prefect drop
# router state and collect garbage, so building two final applications in one process,
# or in the test process, would not show what one server process builds.
ROUTE_PATHS_SCRIPT = """
import json, sys
from starlette.routing import Mount

def paths(routes, prefix=""):
    # FastAPI 0.137+ wraps included routers in objects with no path, so the OpenAPI
    # document of each mounted FastAPI application lists their operations.
    found = []
    for route in routes:
        path = prefix + getattr(route, "path", "")
        if hasattr(route, "path"):
            found.append(path)
        if isinstance(route, Mount):
            found.extend(paths(route.routes, path))
            if hasattr(route.app, "openapi"):
                document = route.app.openapi()
                found.extend(
                    f"{method.upper()} {path}{operation_path}"
                    for operation_path, operations in document["paths"].items()
                    for method in operations
                )
    return found

if sys.argv[1] == "sync":
    from infrahub_sync.service.prefect_server import create_prefect_server
    app = create_prefect_server()
else:
    from prefect.server.api.server import create_app
    app = create_app(final=True)
print(json.dumps(sorted(paths(app.routes))))
"""


def test_the_factory_returns_prefects_final_application(monkeypatch: pytest.MonkeyPatch) -> None:
    sentinel = object()
    calls: list[tuple[tuple[Any, ...], dict[str, Any]]] = []

    def fake_create_app(*args: object, **kwargs: object) -> object:
        calls.append((args, kwargs))
        return sentinel

    monkeypatch.setattr("prefect.server.api.server.create_app", fake_create_app)

    assert prefect_server.create_prefect_server() is sentinel
    assert calls == [((), {"final": True})]


def _route_paths(which: str, tmp_path: Path) -> list[str]:
    environment = {
        **os.environ,
        "PREFECT_HOME": str(tmp_path / "home"),
        "PREFECT_UI_STATIC_DIRECTORY": str(tmp_path / "ui"),
        "PREFECT_API_DATABASE_CONNECTION_URL": f"sqlite+aiosqlite:///{tmp_path / 'prefect.db'}",
    }
    completed = subprocess.run(  # noqa: S603 - fixed interpreter and script
        [sys.executable, "-c", ROUTE_PATHS_SCRIPT, which],
        capture_output=True,
        text=True,
        check=True,
        env=environment,
        timeout=120,
    )
    return json.loads(completed.stdout.strip().splitlines()[-1])


def test_the_factory_adds_no_route(tmp_path: Path) -> None:
    sync_paths = _route_paths("sync", tmp_path / "sync")
    prefect_paths = _route_paths("prefect", tmp_path / "prefect")

    assert "/api/health" in prefect_paths
    assert sync_paths == prefect_paths


def test_main_passes_the_cli_uvicorn_arguments(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[tuple[Any, ...], dict[str, Any]]] = []
    monkeypatch.setattr(prefect_server.uvicorn, "run", lambda *args, **kwargs: calls.append((args, kwargs)))

    with temporary_settings(
        updates={
            PREFECT_SERVER_API_HOST: "0.0.0.0",  # noqa: S104 - value under test, nothing binds
            PREFECT_SERVER_API_PORT: 4321,
            PREFECT_SERVER_API_KEEPALIVE_TIMEOUT: 17,
            PREFECT_SERVER_API_WEBSOCKET_PING_INTERVAL: 11.5,
            PREFECT_SERVER_API_WEBSOCKET_PING_TIMEOUT: 12.5,
            PREFECT_SERVER_LOGGING_LEVEL: "WARNING",
        }
    ):
        prefect_server.main()

    assert calls == [
        (
            (FACTORY,),
            {
                "factory": True,
                "host": "0.0.0.0",  # noqa: S104 - value under test, nothing binds
                "port": 4321,
                "timeout_keep_alive": 17,
                "ws_ping_interval": 11.5,
                "ws_ping_timeout": 12.5,
                "log_level": "warning",
            },
        )
    ]


MAIN_ARGUMENTS_SCRIPT = """
import json
import uvicorn
from infrahub_sync.service import prefect_server

uvicorn.run = lambda *args, **kwargs: print(json.dumps({"args": args, "kwargs": kwargs}))
prefect_server.main()
"""


def test_main_reads_the_prefect_environment_variables(tmp_path: Path) -> None:
    # Prefect loads its settings from the environment once per process, so the variables
    # are set on a fresh interpreter, as the container sets them.
    environment = {
        **os.environ,
        "PREFECT_HOME": str(tmp_path),
        "PREFECT_SERVER_API_HOST": "127.0.0.2",
        "PREFECT_SERVER_API_PORT": "4299",
        "PREFECT_SERVER_API_KEEPALIVE_TIMEOUT": "9",
    }
    completed = subprocess.run(  # noqa: S603 - fixed interpreter and script
        [sys.executable, "-c", MAIN_ARGUMENTS_SCRIPT],
        capture_output=True,
        text=True,
        check=True,
        env=environment,
        timeout=120,
    )
    call = json.loads(completed.stdout.strip().splitlines()[-1])

    assert call["args"] == [FACTORY]
    assert call["kwargs"]["factory"] is True
    assert call["kwargs"]["host"] == "127.0.0.2"
    assert call["kwargs"]["port"] == 4299
    assert call["kwargs"]["timeout_keep_alive"] == 9
