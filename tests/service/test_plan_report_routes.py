"""The plan report routes and the plan summary counts of the Sync API (spec 009 T040).

`GET /runs/{run_id}/plan/problems`, `/plan/left-out` and `/plan/kept` page the report of one
saved plan in its stable order (contracts/sync-api.md). The fixture plan of
`tests/plan/problem_plan.py` is published the way the plan stage's worker publishes it.
"""

from __future__ import annotations

import json
import sqlite3
from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any

import pytest

pytest.importorskip("prefect")
pytest.importorskip("opsmill_prefect_extras")
pytest.importorskip("fastapi")

from fastapi.testclient import TestClient

from infrahub_sync.plan.models import PlanManifest
from infrahub_sync.plan.reader import load_plan_artifact
from infrahub_sync.plan.review import SavedPlan
from infrahub_sync.product_store import ProductProjection, ProductRun, local_product_projection
from infrahub_sync.service import flow as service_flow
from infrahub_sync.service.app import create_app
from infrahub_sync.service.auth import PRINCIPALS_ENV, EnvironmentPrincipalResolver
from infrahub_sync.service.plan_report import PLAN_REPORT_ARTIFACT_ID
from infrahub_sync.service.service import PLAN_ARTIFACT_ID, RunService
from tests.plan.problem_plan import plan_fixture
from tests.service.test_http_api import _FakeOrchestration, _publish_plan, _registered_package

if TYPE_CHECKING:
    from pathlib import Path

    from infrahub_sync.plan.models import PlanReport

OWNER_TOKEN = "owner-token-canary-0101"  # noqa: S105 - deliberate non-secret boundary canary.
HEADERS = {"Authorization": f"Bearer {OWNER_TOKEN}"}
RUN_ID = "run-plan-report"
ROUTES = ("problems", "left-out", "kept")
PAGE_FIELDS = {"run_id", "plan_checksum", "entries", "offset", "limit", "total", "next_offset"}


@pytest.fixture(scope="module")
def fixture_plan(tmp_path_factory: pytest.TempPathFactory) -> SavedPlan:
    """The fixture of `problem_plan`, planned once and read back as the worker reads it."""
    run_dir = tmp_path_factory.mktemp("problem-plan")
    plan_fixture(run_dir)
    loaded = load_plan_artifact(run_dir)
    return SavedPlan(
        manifest=loaded.manifest,
        operations=loaded.operations,
        checksum_ok=True,
        verification_notes=[],
        report=loaded.report,
    )


def _projection_and_client(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> tuple[ProductProjection, TestClient]:
    monkeypatch.setenv(PRINCIPALS_ENV, json.dumps({"owner": {"token": OWNER_TOKEN}}))
    resolver = EnvironmentPrincipalResolver.from_environment()
    projection = local_product_projection(tmp_path.resolve())
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
            started_at=datetime(2026, 10, 15, 12, tzinfo=timezone.utc),
            phase="accepted",
        )
    )
    service = RunService(projection, _FakeOrchestration(), secrets=resolver.secret_values)
    return projection, TestClient(create_app(service, resolver))


@pytest.fixture
def published(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, fixture_plan: SavedPlan
) -> tuple[ProductProjection, TestClient, SavedPlan]:
    projection, client = _projection_and_client(monkeypatch, tmp_path)
    service_flow._publish_plan(projection, RUN_ID, fixture_plan, ())
    return projection, client, fixture_plan


def _report_lines(report: PlanReport, entry: str) -> list[dict[str, Any]]:
    """The report's lines of one entry name, in the stable order, as the routes return them."""
    lines = [line for line in report.entries() if line["entry"] == entry]
    return [{key: value for key, value in line.items() if key != "entry"} for line in lines]


def _all_pages(client: TestClient, route: str, *, limit: int, **params: str) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    offset: int | None = 0
    while offset is not None:
        response = client.get(
            f"/runs/{RUN_ID}/plan/{route}", headers=HEADERS, params={"offset": offset, "limit": limit, **params}
        )
        assert response.status_code == 200, response.text
        body = response.json()
        entries.extend(body["entries"])
        offset = body["next_offset"]
    return entries


@pytest.mark.parametrize(("route", "entry"), [("problems", "problem"), ("left-out", "left_out"), ("kept", "kept")])
def test_a_report_route_returns_the_page_shape_and_every_entry_in_order(
    published: tuple[ProductProjection, TestClient, SavedPlan], route: str, entry: str
) -> None:
    _projection, client, saved = published
    expected = _report_lines(saved.report, entry)

    response = client.get(f"/runs/{RUN_ID}/plan/{route}", headers=HEADERS)

    assert response.status_code == 200
    body = response.json()
    assert set(body) == PAGE_FIELDS
    assert body["run_id"] == RUN_ID
    assert body["plan_checksum"] == saved.manifest.plan_checksum
    assert body["offset"] == 0
    assert body["limit"] == 500
    assert body["total"] == len(expected)
    assert body["next_offset"] is None
    assert body["entries"] == expected
    assert all("entry" not in item for item in body["entries"])


@pytest.mark.parametrize(("route", "entry"), [("problems", "problem"), ("left-out", "left_out"), ("kept", "kept")])
def test_pages_hold_each_entry_once_in_the_stable_order(
    published: tuple[ProductProjection, TestClient, SavedPlan], route: str, entry: str
) -> None:
    _projection, client, saved = published

    assert _all_pages(client, route, limit=1) == _report_lines(saved.report, entry)
    assert _all_pages(client, route, limit=3) == _report_lines(saved.report, entry)


def test_the_fixture_plan_lists_ten_problems_of_nine_types(
    published: tuple[ProductProjection, TestClient, SavedPlan],
) -> None:
    _projection, client, _saved = published

    body = client.get(f"/runs/{RUN_ID}/plan/problems", headers=HEADERS).json()

    assert body["total"] == 10
    assert len({item["problem_type"] for item in body["entries"]}) == 9


def test_a_page_reports_the_offset_of_the_next_page(
    published: tuple[ProductProjection, TestClient, SavedPlan],
) -> None:
    _projection, client, _saved = published

    first = client.get(f"/runs/{RUN_ID}/plan/problems", headers=HEADERS, params={"limit": 3}).json()
    last = client.get(f"/runs/{RUN_ID}/plan/problems", headers=HEADERS, params={"offset": 9, "limit": 3}).json()

    assert (first["offset"], first["limit"], first["total"], first["next_offset"]) == (0, 3, 10, 3)
    assert len(first["entries"]) == 3
    assert (last["offset"], last["total"], last["next_offset"]) == (9, 10, None)
    assert len(last["entries"]) == 1


@pytest.mark.parametrize("route", ROUTES)
@pytest.mark.parametrize("params", [{"limit": 0}, {"limit": 501}, {"offset": -1}])
def test_a_limit_outside_one_to_five_hundred_is_refused(
    published: tuple[ProductProjection, TestClient, SavedPlan], route: str, params: dict[str, int]
) -> None:
    _projection, client, _saved = published

    response = client.get(f"/runs/{RUN_ID}/plan/{route}", headers=HEADERS, params=params)

    assert response.status_code == 422
    assert set(response.json()) == {"error"}


def test_problem_type_and_kind_filter_the_problems(
    published: tuple[ProductProjection, TestClient, SavedPlan],
) -> None:
    _projection, client, saved = published
    problems = _report_lines(saved.report, "problem")

    by_type = client.get(
        f"/runs/{RUN_ID}/plan/problems", headers=HEADERS, params={"problem_type": "peer_not_found_in_source"}
    ).json()
    by_kind = client.get(f"/runs/{RUN_ID}/plan/problems", headers=HEADERS, params={"kind": "DcimDevice"}).json()

    assert by_type["entries"] == [item for item in problems if item["problem_type"] == "peer_not_found_in_source"]
    assert by_type["total"] == 2
    assert by_kind["entries"] == [item for item in problems if item["record"]["kind"] == "DcimDevice"]
    assert by_kind["total"] >= 1


@pytest.mark.parametrize(("route", "entry"), [("left-out", "left_out"), ("kept", "kept")])
def test_kind_filters_the_left_out_and_kept_records(
    published: tuple[ProductProjection, TestClient, SavedPlan], route: str, entry: str
) -> None:
    _projection, client, saved = published
    lines = _report_lines(saved.report, entry)
    kind = lines[0]["record"]["kind"]

    body = client.get(f"/runs/{RUN_ID}/plan/{route}", headers=HEADERS, params={"kind": kind}).json()

    assert body["entries"] == [item for item in lines if item["record"]["kind"] == kind]
    assert body["total"] == len(body["entries"])


def test_an_unknown_problem_type_is_refused(published: tuple[ProductProjection, TestClient, SavedPlan]) -> None:
    _projection, client, _saved = published

    response = client.get(f"/runs/{RUN_ID}/plan/problems", headers=HEADERS, params={"problem_type": "typo"})

    assert response.status_code == 422
    assert set(response.json()) == {"error"}


@pytest.mark.parametrize("route", ROUTES)
def test_a_different_expected_checksum_is_refused_as_plan_changed(
    published: tuple[ProductProjection, TestClient, SavedPlan], route: str
) -> None:
    _projection, client, saved = published

    changed = client.get(f"/runs/{RUN_ID}/plan/{route}", headers=HEADERS, params={"expected_checksum": "b" * 64})
    same = client.get(
        f"/runs/{RUN_ID}/plan/{route}", headers=HEADERS, params={"expected_checksum": saved.manifest.plan_checksum}
    )

    assert changed.status_code == 409
    assert changed.json()["error"]["code"] == "plan-changed"
    assert same.status_code == 200


@pytest.mark.parametrize("format_version", [2, 3])
@pytest.mark.parametrize("route", ROUTES)
def test_a_format_two_or_three_plan_returns_empty_lists(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, format_version: int, route: str
) -> None:
    projection, client = _projection_and_client(monkeypatch, tmp_path)
    saved = SavedPlan(
        manifest=PlanManifest(
            format_version=format_version,
            run_id=RUN_ID,
            created_at="2026-10-15T12:00:00+00:00",
            config_version="edge-sync@1",
            source_snapshot=[],
            operations_count=0,
            delete_operations_computed=True,
            plan_checksum="c" * 64,
        ),
        operations=[],
        checksum_ok=True,
        verification_notes=[],
    )
    service_flow._publish_plan(projection, RUN_ID, saved, ())

    body = client.get(f"/runs/{RUN_ID}/plan/{route}", headers=HEADERS).json()
    summary = client.get(f"/runs/{RUN_ID}/plan", headers=HEADERS).json()["summary"]

    assert (body["entries"], body["total"], body["next_offset"]) == ([], 0, None)
    assert body["plan_checksum"] == "c" * 64
    assert (summary["left_out"], summary["problems"], summary["kept"]) == (0, 0, 0)


@pytest.mark.parametrize("route", ROUTES)
def test_a_plan_published_before_the_report_returns_empty_lists(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, route: str
) -> None:
    """A review document published by an earlier release has no report artifact beside it."""
    projection, client = _projection_and_client(monkeypatch, tmp_path)
    _publish_plan(projection, RUN_ID)

    response = client.get(f"/runs/{RUN_ID}/plan/{route}", headers=HEADERS)

    assert response.status_code == 200
    assert (response.json()["entries"], response.json()["total"]) == ([], 0)


@pytest.mark.parametrize("route", ROUTES)
def test_an_unknown_run_is_not_found(published: tuple[ProductProjection, TestClient, SavedPlan], route: str) -> None:
    _projection, client, _saved = published

    response = client.get(f"/runs/missing/plan/{route}", headers=HEADERS)

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "run-not-found"


@pytest.mark.parametrize("artifact_id", [PLAN_ARTIFACT_ID, PLAN_REPORT_ARTIFACT_ID])
@pytest.mark.parametrize("route", ROUTES)
def test_an_expired_plan_or_report_is_gone(
    published: tuple[ProductProjection, TestClient, SavedPlan], tmp_path: Path, artifact_id: str, route: str
) -> None:
    _projection, client, _saved = published
    expired_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    with sqlite3.connect(tmp_path / "product-records.sqlite3") as connection:
        connection.execute(
            "UPDATE artifact_refs SET expires_at = ? WHERE run_id = ? AND artifact_id = ?",
            (expired_at.isoformat(), RUN_ID, artifact_id),
        )

    response = client.get(f"/runs/{RUN_ID}/plan/{route}", headers=HEADERS)

    assert response.status_code == 410
    assert response.json()["error"]["code"] == "artifact-expired"


@pytest.mark.parametrize("artifact_id", [PLAN_ARTIFACT_ID, PLAN_REPORT_ARTIFACT_ID])
@pytest.mark.parametrize("route", ROUTES)
def test_an_unreadable_plan_or_report_is_unavailable(
    published: tuple[ProductProjection, TestClient, SavedPlan], tmp_path: Path, artifact_id: str, route: str
) -> None:
    projection, client, _saved = published
    run = projection.lookup_run(RUN_ID).value
    assert run is not None
    reference = next(item for item in run.artifact_refs if item.artifact_id == artifact_id)
    (tmp_path / "artifacts" / reference.object_key).unlink()

    response = client.get(f"/runs/{RUN_ID}/plan/{route}", headers=HEADERS)

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "plan-unavailable"


@pytest.mark.parametrize("route", ROUTES)
@pytest.mark.parametrize("authorization", [None, "Bearer not-a-known-token"])
def test_the_routes_require_the_bearer_token_of_the_run_routes(
    published: tuple[ProductProjection, TestClient, SavedPlan], route: str, authorization: str | None
) -> None:
    _projection, client, _saved = published
    headers = {} if authorization is None else {"Authorization": authorization}

    response = client.get(f"/runs/{RUN_ID}/plan/{route}", headers=headers)

    assert response.status_code == 401
    assert OWNER_TOKEN not in response.text


def test_the_plan_summary_carries_the_report_counts(
    published: tuple[ProductProjection, TestClient, SavedPlan],
) -> None:
    _projection, client, saved = published
    report = saved.report

    summary = client.get(f"/runs/{RUN_ID}/plan", headers=HEADERS).json()["summary"]

    assert summary["left_out"] == len(report.left_out) > 0
    assert summary["left_out_with_problem"] == sum(1 for item in report.left_out if item.cause == "has_problem")
    assert summary["left_out_with_problem"] + summary["left_out_dependent"] == summary["left_out"]
    assert summary["left_out_by_kind"] == dict(Counter(item.record.kind for item in report.left_out))
    assert sum(summary["left_out_by_kind"].values()) == summary["left_out"]
    assert summary["problems"] == 10
    assert summary["problems_by_type"] == dict(Counter(item.problem_type for item in report.problems))
    assert summary["kept"] == len(report.kept) > 0
    # Review #3 and #8: the kinds whose deletes are withheld, and whether Dropdown values were checked.
    assert summary["deletes_withheld_by_kind"] == report.records_without_identity_by_kind() != {}
    assert summary["dropdown_values_checked"] is True
