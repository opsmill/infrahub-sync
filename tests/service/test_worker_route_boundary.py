"""The ``worker`` account passes the worker routes only: every other route answers ``403 forbidden``.

The cases are generated from the OpenAPI document of an application built with every optional
router (configurations and schedules), so a route added later is covered with no change here.
The OpenAPI document lists the routes of the included routers, which ``app.routes`` holds behind
FastAPI's included-router entries.

Exempt routes, which read no bearer value at all, so no account is refused on them:

- ``GET /version`` and ``GET /status``: the compatibility and health answers that a client reads
  before it authenticates.
- ``POST /auth/sign-in``: it takes a name and a password, not a token; the ``worker`` account is
  refused there by ``tests/service/test_worker_account.py``.

The worker routes are the two routes of ``WORKER_ROUTES``; a case holds the list exact, so a
third worker route cannot be added without a change here. The refusal of other accounts on each
worker route is tested with the route, in ``tests/service/test_worker_credentials_route.py`` and
``tests/service/test_worker_proposed_change_states_route.py``. ``require_worker`` is tested here
on a route added for the test.
"""

from __future__ import annotations

import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("prefect")

from fastapi import Depends
from fastapi.testclient import TestClient

from infrahub_sync.product_store import local_product_projection
from infrahub_sync.service.app import create_app, require_worker
from infrahub_sync.service.auth import Principal, StoreAccountResolver, apply_worker_account
from infrahub_sync.service.config_routes import ConfigurationRoutes
from infrahub_sync.service.credentials import digest, new_api_token
from infrahub_sync.service.events import ACCOUNT_REFUSED
from infrahub_sync.service.schedules import ScheduleService
from infrahub_sync.service.service import RunService
from tests.service.schedule_fixtures import FakeOrchestration

if TYPE_CHECKING:
    from fastapi import FastAPI

    from infrahub_sync.product_store import ProductProjection

_WORKER_TOKEN = "worker-token-of-the-environment-1"  # noqa: S105 - a test value, not a secret.
_T0 = datetime(2026, 11, 20, 9, 0, tzinfo=timezone.utc)
_METHODS = frozenset({"get", "post", "put", "patch", "delete"})
# The routes that read no bearer value; see the module docstring for why each is exempt.
EXEMPT_ROUTES = frozenset({("GET", "/version"), ("GET", "/status"), ("POST", "/auth/sign-in")})
WORKER_PREFIX = "/worker/"
# Every route that accepts the ``worker`` account (spec 027, FR-004 and FR-005). The first is
# the only route whose answer holds a value; the second answers with states only.
WORKER_ROUTES = frozenset(
    {
        ("POST", "/worker/runs/{run_id}/credentials"),
        ("POST", "/worker/schedules/{schedule_id}/proposed-change-states"),
    }
)


def _application(store: ProductProjection) -> tuple[FastAPI, RunService]:
    """Build the API with every optional router, as the served application has them."""
    service = RunService(store, FakeOrchestration())
    resolver = StoreAccountResolver(store, idle_seconds=600, worker_token=_WORKER_TOKEN)
    schedules = ScheduleService(
        store, FakeOrchestration(), resolver, delivered_write_targets=("branch-review", "default-branch")
    )
    configuration_routes = ConfigurationRoutes(product_projection=store, secrets=resolver.secret_values)
    return create_app(service, resolver, configuration_routes, schedules=schedules), service


def _operations() -> list[tuple[str, str]]:
    """Return every (method, path) of the API, read from its OpenAPI document at collection time."""
    with tempfile.TemporaryDirectory() as directory:
        application, _service = _application(local_product_projection(Path(directory)))
        paths = application.openapi()["paths"]
    return sorted(
        (method.upper(), path) for path, operations in paths.items() for method in operations if method in _METHODS
    )


_OPERATIONS = _operations()


def _concrete(path: str) -> str:
    """Fill every path parameter with a value that names nothing."""
    return re.sub(r"\{[^}]+\}", "no-such-value", path)


@pytest.fixture(name="api")
def _api_fixture(tmp_path: Path) -> tuple[TestClient, ProductProjection, RunService]:
    store = local_product_projection(tmp_path.resolve())
    apply_worker_account(store, _WORKER_TOKEN)
    application, service = _application(store)
    return TestClient(application), store, service


def test_the_enumeration_reaches_the_routes_of_the_included_routers() -> None:
    paths = {path for _method, path in _OPERATIONS}

    assert {"/me", "/runs", "/accounts", "/accounts/{name}", "/auth/sign-out", "/events"} <= paths
    assert any(path.startswith("/schedules") for path in paths)
    assert any(path.startswith("/configs") for path in paths)
    assert set(_OPERATIONS) >= EXEMPT_ROUTES


def test_the_worker_routes_are_exactly_the_two_routes_of_the_contract() -> None:
    assert {operation for operation in _OPERATIONS if WORKER_PREFIX in operation[1]} == WORKER_ROUTES


@pytest.mark.parametrize(("method", "path"), sorted(WORKER_ROUTES), ids=lambda value: value)
def test_a_worker_route_accepts_the_worker_token(
    api: tuple[TestClient, ProductProjection, RunService], method: str, path: str
) -> None:
    client, _store, _service = api

    response = client.request(
        method, _concrete(path), headers={"Authorization": f"Bearer {_WORKER_TOKEN}"}, json={"flow_run_id": "none"}
    )

    # The identifier names nothing, so the route answers that; it does not refuse the account.
    assert response.status_code == 404, f"{method} {path} answered {response.status_code}"
    assert _WORKER_TOKEN not in response.text


@pytest.mark.parametrize(
    ("method", "path"),
    [operation for operation in _OPERATIONS if operation not in EXEMPT_ROUTES and operation not in WORKER_ROUTES],
    ids=lambda value: value,
)
def test_the_worker_token_is_forbidden_outside_the_worker_routes(
    api: tuple[TestClient, ProductProjection, RunService], method: str, path: str
) -> None:
    client, _store, _service = api

    response = client.request(
        method,
        _concrete(path),
        headers={"Authorization": f"Bearer {_WORKER_TOKEN}", "Idempotency-Key": "worker-boundary"},
    )

    assert response.status_code == 403, f"{method} {path} answered {response.status_code}"
    assert response.json()["error"]["code"] == "forbidden"
    assert _WORKER_TOKEN not in response.text


def test_a_refusal_of_the_worker_is_recorded_without_its_token(
    api: tuple[TestClient, ProductProjection, RunService],
) -> None:
    client, store, _service = api

    client.get("/me", headers={"Authorization": f"Bearer {_WORKER_TOKEN}"})

    events = [event for event in store.audit_events() if event.event_type == ACCOUNT_REFUSED]
    assert [(event.operation, event.outcome) for event in events] == [("/me", "refused-authorization")]
    assert all(_WORKER_TOKEN not in repr(event) for event in events)


def _with_worker_route(store: ProductProjection) -> TestClient:
    """Return a client of the API with one route that uses ``require_worker``, as the worker routes will."""
    application, service = _application(store)
    resolver = StoreAccountResolver(store, idle_seconds=600, worker_token=_WORKER_TOKEN)
    authenticate_worker = require_worker(service, resolver)

    # A default, not ``Annotated``: the dependency is a local name, which postponed annotations
    # cannot resolve.
    @application.post("/worker/probe")
    def probe(principal: Principal = Depends(authenticate_worker)) -> dict[str, Any]:  # noqa: B008
        return {"worker": principal.worker}

    return TestClient(application)


def test_require_worker_accepts_the_worker_account(tmp_path: Path) -> None:
    store = local_product_projection(tmp_path.resolve())
    apply_worker_account(store, _WORKER_TOKEN)
    client = _with_worker_route(store)

    response = client.post("/worker/probe", headers={"Authorization": f"Bearer {_WORKER_TOKEN}"})

    assert (response.status_code, response.json()) == (200, {"worker": True})


@pytest.mark.parametrize("administrator", [True, False], ids=("administrator", "account"))
def test_require_worker_refuses_every_other_account(tmp_path: Path, *, administrator: bool) -> None:
    store = local_product_projection(tmp_path.resolve())
    apply_worker_account(store, _WORKER_TOKEN)
    account = store.create_account(name="alice", password_hash=None, administrator=administrator, now=_T0)
    value = new_api_token()
    store.create_account_token(account.account_id, name="ci", digest=digest(value), now=_T0)
    client = _with_worker_route(store)

    response = client.post("/worker/probe", headers={"Authorization": f"Bearer {value}"})

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "forbidden"


def test_require_worker_refuses_a_request_without_a_token(tmp_path: Path) -> None:
    client = _with_worker_route(local_product_projection(tmp_path.resolve()))

    response = client.post("/worker/probe")

    assert response.status_code == 401
    assert cast("dict[str, Any]", response.json())["error"]["code"] == "unauthenticated"
