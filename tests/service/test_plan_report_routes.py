"""The problem, left-out and kept lists of the Sync API (spec 019 T015, FR-007, contracts/sync-api.md).

`GET /runs/{run_id}/plan/problems`, `/plan/left-out` and `/plan/kept` page the report of one saved
plan in its stable order, with the same response shape for the three routes:

- `limit` is 1 to 500 (500 by default) and `next_offset` is null on the last page
- `problem_type` (problems only) and `kind` filter the entries; an unknown `problem_type` is 422
- `expected_checksum` that differs from the stored plan is 409 `plan-changed`
- a format 2 or 3 plan returns empty lists; an unknown run is 404 `run-not-found`

The plan of the shared problem fixture (`tests/plan/problem_plan.py`) is read back with
`read_saved_plan` and published with the worker's `_publish_plan`, as the plan stage does.

Spec 019 T027 to T030 implement the routes. Until then each test is an expected failure: the
fixture plan needs the base-class read loop (`NotImplementedError`), and a route that does not
exist answers 404 (`AssertionError`).
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

import pytest

from infrahub_sync.plan.errors import DestinationIdentityCollisionError

pytest.importorskip("prefect")
pytest.importorskip("opsmill_prefect_extras")
pytest.importorskip("fastapi")

from fastapi.testclient import TestClient

from infrahub_sync.plan.review import read_saved_plan
from infrahub_sync.product_store import ProductProjection, ProductRun, local_product_projection
from infrahub_sync.service import flow as service_flow
from infrahub_sync.service.app import create_app
from infrahub_sync.service.auth import PRINCIPALS_ENV, EnvironmentPrincipalResolver
from infrahub_sync.service.service import RunService
from tests.plan.conftest import load_plan_problem_fixture
from tests.plan.problem_plan import RUN_ID as PLAN_RUN_ID
from tests.plan.problem_plan import PlannedFixture, plan_fixture
from tests.service.test_http_api import _FakeOrchestration, _publish_plan, _registered_package

if TYPE_CHECKING:
    from pathlib import Path

OWNER_TOKEN = "owner-token-canary-0190"  # noqa: S105 - deliberate non-secret boundary canary.
HEADERS = {"Authorization": f"Bearer {OWNER_TOKEN}"}
RUN_ID = "run-plan-report"
PAGE_FIELDS = {"run_id", "plan_checksum", "entries", "offset", "limit", "total", "next_offset"}
ROUTES = [("problems", "problem"), ("left-out", "left_out"), ("kept", "kept")]

NOT_IMPLEMENTED = pytest.mark.xfail(
    strict=True,
    raises=DestinationIdentityCollisionError,
    reason=(
        "spec 019 T028 not implemented: Potenda.write_plan does not run the problem pass yet, so it "
        "still refuses identity groups (DestinationIdentityCollisionError)"
    ),
)
NO_ROUTE = pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason="spec 019 US1 not implemented: the plan report routes do not exist (T030)",
)


def _service(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> tuple[ProductProjection, TestClient]:
    """A product store with one accepted plan run, and a client of the Sync API over it."""
    monkeypatch.setenv(PRINCIPALS_ENV, json.dumps({"owner": {"token": OWNER_TOKEN}}))
    resolver = EnvironmentPrincipalResolver.from_environment()
    projection = local_product_projection((tmp_path / "store").resolve())
    version = projection.create_configuration(_registered_package())
    projection.create_run(
        ProductRun(
            run_id=RUN_ID,
            operation="plan",
            configuration_reference=f"{version.config_id}@{version.registry_version}",
            config_id=version.config_id,
            registry_version=version.registry_version,
            package_checksum=version.package_checksum,
            actor="owner",
            started_at=datetime(2026, 11, 11, 12, tzinfo=timezone.utc),
            phase="accepted",
        )
    )
    service = RunService(projection, _FakeOrchestration(), secrets=resolver.secret_values)
    return projection, TestClient(create_app(service, resolver))


def _published_fixture(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> tuple[TestClient, PlannedFixture]:
    """Plan the full problem fixture and publish it for `RUN_ID`, as the plan stage's worker does."""
    monkeypatch.setenv("INFRAHUB_SYNC_CACHE_DIR", str(tmp_path / "cache"))
    fixture = load_plan_problem_fixture()
    planned = plan_fixture(fixture)
    saved = read_saved_plan(sync_name=fixture.config_data["name"], run_id=PLAN_RUN_ID)
    projection, client = _service(monkeypatch, tmp_path)
    service_flow._publish_plan(projection, RUN_ID, saved, ())
    return client, planned


def _published_format_3(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> TestClient:
    """A run whose published plan carries no report, as a plan saved before format 4."""
    projection, client = _service(monkeypatch, tmp_path)
    _publish_plan(projection, RUN_ID)
    return client


def _expected(planned: PlannedFixture, entry: str) -> list[dict[str, Any]]:
    """The report lines of one entry, in the report's order, as the routes return them."""
    return [{key: value for key, value in line.items() if key != "entry"} for line in planned.entries(entry)]


def _get(client: TestClient, route: str, run_id: str = RUN_ID, **params: Any) -> Any:  # noqa: ANN401
    return client.get(f"/runs/{run_id}/plan/{route}", headers=HEADERS, params=params)


def _all_pages(client: TestClient, route: str, *, limit: int, **params: Any) -> list[dict[str, Any]]:  # noqa: ANN401
    entries: list[dict[str, Any]] = []
    offset: int | None = 0
    while offset is not None:
        response = _get(client, route, offset=offset, limit=limit, **params)
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["offset"] == offset
        entries.extend(body["entries"])
        offset = body["next_offset"]
    return entries


@NOT_IMPLEMENTED
@pytest.mark.parametrize(("route", "entry"), ROUTES)
def test_a_route_returns_every_entry_in_the_report_order(
    route: str, entry: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """One page of the default limit holds every entry of the route, in the report's order."""
    client, planned = _published_fixture(monkeypatch, tmp_path)
    expected = _expected(planned, entry)

    response = _get(client, route)

    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body) == PAGE_FIELDS
    assert body["run_id"] == RUN_ID
    assert body["plan_checksum"] == planned.manifest["plan_checksum"]
    assert (body["offset"], body["limit"], body["total"], body["next_offset"]) == (0, 500, len(expected), None)
    assert body["entries"] == expected
    assert expected


@NOT_IMPLEMENTED
@pytest.mark.parametrize(("route", "entry"), ROUTES)
def test_pages_hold_each_entry_once_in_order(
    route: str, entry: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Pages of one entry each, followed by `next_offset`, give the same list as one page."""
    client, planned = _published_fixture(monkeypatch, tmp_path)

    assert _all_pages(client, route, limit=1) == _expected(planned, entry)


@NOT_IMPLEMENTED
def test_the_lists_filter_by_problem_type_and_kind(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """`problem_type` and `kind` keep only the matching entries, in order, with the filtered total."""
    client, planned = _published_fixture(monkeypatch, tmp_path)

    by_type = _get(client, "problems", problem_type="peer_not_found_in_source").json()
    assert by_type["entries"] == [
        line for line in _expected(planned, "problem") if line["problem_type"] == "peer_not_found_in_source"
    ]
    assert by_type["total"] == 2
    by_kind = _get(client, "problems", kind="LocationSite").json()
    assert by_kind["entries"] == [
        line for line in _expected(planned, "problem") if line["record"]["kind"] == "LocationSite"
    ]
    left_out = _get(client, "left-out", kind="InterfacePhysical").json()
    assert left_out["entries"] == [
        line for line in _expected(planned, "left_out") if line["record"]["kind"] == "InterfacePhysical"
    ]
    assert left_out["total"] == 6
    kept = _get(client, "kept", kind="IpamIPAddress").json()
    assert kept["total"] == 2
    assert _get(client, "kept", kind="DcimDevice").json()["entries"] == []


@NOT_IMPLEMENTED
def test_an_expected_checksum_that_differs_is_plan_changed(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """409 `plan-changed` when `expected_checksum` is not the stored plan's; 200 when it is."""
    client, planned = _published_fixture(monkeypatch, tmp_path)

    changed = _get(client, "problems", expected_checksum="f" * 64)
    same = _get(client, "problems", expected_checksum=planned.manifest["plan_checksum"])

    assert changed.status_code == 409
    assert changed.json()["error"]["code"] == "plan-changed"
    assert same.status_code == 200


@NO_ROUTE
def test_an_unknown_problem_type_is_refused(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """A `problem_type` outside the nine names is 422."""
    client = _published_format_3(monkeypatch, tmp_path)

    assert _get(client, "problems", problem_type="not_a_problem_type").status_code == 422


@NO_ROUTE
@pytest.mark.parametrize("limit", [0, 501])
def test_a_limit_outside_1_to_500_is_refused(limit: int, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    client = _published_format_3(monkeypatch, tmp_path)

    assert _get(client, "problems", limit=limit).status_code == 422


@NO_ROUTE
@pytest.mark.parametrize(("route", "entry"), ROUTES)
def test_a_plan_without_a_report_returns_empty_lists(
    route: str, entry: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A plan saved before format 4 has no report: each list is empty."""
    del entry
    client = _published_format_3(monkeypatch, tmp_path)

    response = _get(client, route)

    assert response.status_code == 200, response.text
    body = response.json()
    assert (body["entries"], body["total"], body["next_offset"]) == ([], 0, None)


@NO_ROUTE
def test_an_unknown_run_is_run_not_found(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    client = _published_format_3(monkeypatch, tmp_path)

    response = _get(client, "problems", run_id="run-that-does-not-exist")

    assert response.status_code == 404
    assert response.json().get("error", {}).get("code") == "run-not-found"
