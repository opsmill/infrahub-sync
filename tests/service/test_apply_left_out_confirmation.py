"""`POST /runs/{run_id}/apply` needs the confirmed left-out count (spec 009 T057).

The checks of `contracts/sync-api.md` run in order, all before any write: confirmation,
checksum, the left-out count, a run that already admitted a write, and the plan format. A
refusal submits nothing to the orchestrator, so no worker starts and the destination receives
no write. The fixture plan of `tests/plan/problem_plan.py` is published as the plan stage's
worker publishes it.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

import pytest

pytest.importorskip("prefect")
pytest.importorskip("opsmill_prefect_extras")
pytest.importorskip("fastapi")

from fastapi.testclient import TestClient

from infrahub_sync.plan.reader import load_plan_artifact
from infrahub_sync.plan.review import SavedPlan
from infrahub_sync.product_store import ProductProjection, local_product_projection
from infrahub_sync.service import flow as service_flow
from infrahub_sync.service.app import create_app
from infrahub_sync.service.auth import PRINCIPALS_ENV, EnvironmentPrincipalResolver
from infrahub_sync.service.service import PLAN_ARTIFACT_ID, RunService
from tests.adapters.test_infrahub_planned_write import engine_over
from tests.plan.problem_plan import RUN_ID as FIXTURE_RUN_ID
from tests.plan.problem_plan import PlannedFixture, plan_fixture
from tests.plan.test_plan_format_3 import CountingDestination, RecordingOwnership
from tests.service.test_http_api import (
    _create,
    _FakeOrchestration,
    _plan_document,
    _registered_package,
    _settle_open_executions,
)

if TYPE_CHECKING:
    from pathlib import Path

OWNER_TOKEN = "owner-token-canary-0001"  # noqa: S105 - the token `_create` sends.


@pytest.fixture(scope="module")
def fixture_plan(tmp_path_factory: pytest.TempPathFactory) -> PlannedFixture:
    """The problem fixture planned once; its plan leaves out records."""
    return plan_fixture(tmp_path_factory.mktemp("problem-plan"))


def _saved(fixture: PlannedFixture) -> SavedPlan:
    loaded = load_plan_artifact(fixture.run_dir)
    return SavedPlan(
        manifest=loaded.manifest,
        operations=loaded.operations,
        checksum_ok=True,
        verification_notes=[],
        report=loaded.report,
    )


class _Api:
    def __init__(self, projection: ProductProjection, client: TestClient, orchestration: _FakeOrchestration) -> None:
        self.projection = projection
        self.client = client
        self.orchestration = orchestration

    def planned_run(self, publish: Any) -> str:  # noqa: ANN401 - a callable that publishes the plan
        created = _create(self.client)
        assert created.status_code == 202, created.text
        run_id = created.json()["run"]["run_id"]
        publish(run_id)
        _settle_open_executions(self.projection, run_id)
        return run_id

    def apply(self, run_id: str, key: str, **body: Any) -> Any:  # noqa: ANN401
        payload = {"reason": "apply the reviewed plan", "confirm_writes": True, **body}
        return self.client.post(
            f"/runs/{run_id}/apply",
            headers={"Authorization": f"Bearer {OWNER_TOKEN}", "Idempotency-Key": key},
            json=payload,
        )

    def apply_submissions(self) -> list[dict[str, object]]:
        return [parameters for parameters, _key in self.orchestration.submissions if parameters["stage"] == "apply"]

    def outcomes(self, run_id: str) -> list[str]:
        return [event.outcome for event in self.projection.audit_events(run_id) if event.operation == "apply"]


@pytest.fixture
def api(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> _Api:
    monkeypatch.setenv(PRINCIPALS_ENV, json.dumps({"owner": {"token": OWNER_TOKEN}}))
    resolver = EnvironmentPrincipalResolver.from_environment()
    projection = local_product_projection(tmp_path.resolve())
    orchestration = _FakeOrchestration()
    version = projection.create_configuration(_registered_package())
    client = TestClient(create_app(RunService(projection, orchestration, secrets=resolver.secret_values), resolver))
    client.app.state.run_binding = version
    return _Api(projection, client, orchestration)


def _publish_fixture(api: _Api, fixture: PlannedFixture) -> Any:  # noqa: ANN401
    return lambda run_id: service_flow._publish_plan(api.projection, run_id, _saved(fixture), ())


def _publish_document(api: _Api, **update: Any) -> Any:  # noqa: ANN401
    def publish(run_id: str) -> None:
        document = _plan_document(run_id).model_copy(update=update)
        api.projection.publish_artifact(
            run_id,
            artifact_id=PLAN_ARTIFACT_ID,
            kind="saved-plan-review",
            media_type="application/json",
            data=document.model_dump_json().encode(),
        )

    return publish


def _left_out(fixture: PlannedFixture) -> int:
    assert fixture.manifest.report is not None
    assert fixture.manifest.report.left_out > 0
    return fixture.manifest.report.left_out


@pytest.mark.parametrize("delta", [None, -1], ids=["absent", "wrong"])
def test_a_missing_or_wrong_count_is_refused_with_the_expected_count(
    api: _Api, fixture_plan: PlannedFixture, delta: int | None
) -> None:
    left_out = _left_out(fixture_plan)
    run_id = api.planned_run(_publish_fixture(api, fixture_plan))
    body: dict[str, Any] = {"expected_checksum": fixture_plan.manifest.plan_checksum}
    if delta is not None:
        body["confirmed_left_out_count"] = left_out + delta

    response = api.apply(run_id, "apply-without-count", **body)

    assert response.status_code == 409, response.text
    error = response.json()["error"]
    assert error["code"] == "left-out-confirmation-required"
    assert error["expected_left_out_count"] == left_out
    assert api.outcomes(run_id) == ["refused-left-out-count"]
    assert api.apply_submissions() == [], "Nothing reached a worker, so nothing was written."


def test_the_right_count_is_accepted_and_the_apply_writes_only_the_plan_operations(
    api: _Api, fixture_plan: PlannedFixture
) -> None:
    left_out = _left_out(fixture_plan)
    run_id = api.planned_run(_publish_fixture(api, fixture_plan))

    response = api.apply(
        run_id,
        "apply-with-count",
        expected_checksum=fixture_plan.manifest.plan_checksum,
        confirmed_left_out_count=left_out,
    )

    assert response.status_code == 202, response.text
    assert api.outcomes(run_id)[-1] == "accepted"
    (parameters,) = api.apply_submissions()
    assert parameters["confirmed_left_out_count"] == left_out
    # The worker hands the submitted count to the engine; the engine writes the plan's
    # operations and none for a left-out record.
    destination = CountingDestination()
    engine_over(fixture_plan.run_dir, destination, run_id=FIXTURE_RUN_ID).apply_plan(
        ownership=RecordingOwnership(),
        config_version=fixture_plan.manifest.config_version,
        confirmed_left_out_count=parameters["confirmed_left_out_count"],  # ty: ignore[invalid-argument-type]
    )
    assert destination.applied == [
        operation.operation_id for operation in fixture_plan.operations if operation.action != "delete"
    ]


@pytest.mark.parametrize("count", [None, 0], ids=["absent", "zero"])
def test_a_plan_that_leaves_out_nothing_needs_no_count(api: _Api, count: int | None) -> None:
    run_id = api.planned_run(_publish_document(api))
    body: dict[str, Any] = {"expected_checksum": "a" * 64}
    if count is not None:
        body["confirmed_left_out_count"] = count

    response = api.apply(run_id, "apply-nothing-left-out", **body)

    assert response.status_code == 202, response.text
    assert len(api.apply_submissions()) == 1


def test_a_negative_count_is_rejected_as_a_malformed_request(api: _Api) -> None:
    run_id = api.planned_run(_publish_document(api))

    response = api.apply(run_id, "apply-negative", expected_checksum="a" * 64, confirmed_left_out_count=-1)

    assert response.status_code == 422
    assert api.apply_submissions() == []


@pytest.mark.parametrize("plan_format", [3, None], ids=["format-3", "earlier-release-document"])
def test_a_plan_saved_before_format_4_is_refused(api: _Api, plan_format: int | None) -> None:
    run_id = api.planned_run(_publish_document(api, format_version=plan_format))

    response = api.apply(run_id, "apply-format-3", expected_checksum="a" * 64)

    assert response.status_code == 409, response.text
    error = response.json()["error"]
    assert error["code"] == "plan-format-not-applicable"
    assert error["message"] == "plan again before applying"
    assert "expected_left_out_count" not in error
    assert api.apply_submissions() == []


def test_the_checks_run_in_the_contract_order(api: _Api) -> None:
    """Confirmation, then checksum, then the count, then the plan format.

    Check 4, a run that already admitted a write, is tested on its own below.
    """
    left_out_format_3 = _publish_document(
        api,
        format_version=3,
        summary=_plan_document("x").summary.model_copy(
            update={
                "left_out": 2,
                "left_out_dependent": 2,
                "left_out_by_kind": {"Device": 2},
            }
        ),
    )
    run_id = api.planned_run(left_out_format_3)

    unconfirmed = api.apply(run_id, "order-1", expected_checksum="b" * 64, confirm_writes=False)
    wrong_checksum = api.apply(run_id, "order-2", expected_checksum="b" * 64)
    no_count = api.apply(run_id, "order-3", expected_checksum="a" * 64)
    old_format = api.apply(run_id, "order-4", expected_checksum="a" * 64, confirmed_left_out_count=2)

    codes = [response.json()["error"]["code"] for response in (unconfirmed, wrong_checksum, no_count, old_format)]
    assert codes == [
        "confirmation-required",
        "checksum-conflict",
        "left-out-confirmation-required",
        "plan-format-not-applicable",
    ]
    assert api.outcomes(run_id) == [
        "refused-confirmation",
        "refused-checksum",
        "refused-left-out-count",
        "refused-plan-format",
    ]
    assert api.apply_submissions() == []


def test_the_count_is_checked_before_a_run_that_already_admitted_a_write(
    api: _Api, fixture_plan: PlannedFixture
) -> None:
    left_out = _left_out(fixture_plan)
    run_id = api.planned_run(_publish_fixture(api, fixture_plan))
    checksum = fixture_plan.manifest.plan_checksum
    first = api.apply(run_id, "first-apply", expected_checksum=checksum, confirmed_left_out_count=left_out)
    assert first.status_code == 202, first.text

    wrong = api.apply(run_id, "second-apply-wrong", expected_checksum=checksum)
    right = api.apply(run_id, "second-apply-right", expected_checksum=checksum, confirmed_left_out_count=left_out)

    assert wrong.json()["error"]["code"] == "left-out-confirmation-required"
    assert right.status_code == 409
    assert right.json()["error"]["code"] == "new-plan-required"
    assert len(api.apply_submissions()) == 1, "Only the first apply reached a worker."


def test_a_run_that_admitted_a_write_is_refused_before_the_plan_format(api: _Api) -> None:
    """Check 4 (`new-plan-required`) comes after the count and before the plan format."""
    version = api.client.app.state.run_binding
    admitted = api.client.post(
        "/runs",
        headers={"Authorization": f"Bearer {OWNER_TOKEN}", "Idempotency-Key": "sync-admits-the-write"},
        json={
            "operation": "sync",
            "config_id": version.config_id,
            "registry_version": version.registry_version,
            "confirm_writes": True,
            "reason": "synchronize the inventory",
        },
    )
    assert admitted.status_code == 202, admitted.text
    run_id = admitted.json()["run"]["run_id"]
    _publish_document(
        api,
        format_version=3,
        summary=_plan_document("x").summary.model_copy(
            update={"left_out": 2, "left_out_dependent": 2, "left_out_by_kind": {"Device": 2}}
        ),
    )(run_id)
    _settle_open_executions(api.projection, run_id)

    no_count = api.apply(run_id, "after-sync-1", expected_checksum="a" * 64)
    counted = api.apply(run_id, "after-sync-2", expected_checksum="a" * 64, confirmed_left_out_count=2)

    assert no_count.json()["error"]["code"] == "left-out-confirmation-required"
    error = counted.json()["error"]
    assert (counted.status_code, error["code"]) == (409, "new-plan-required")
    assert error["message"] == "This plan was already applied or its apply stopped; create and review a new plan"
    assert api.outcomes(run_id)[-2:] == ["refused-left-out-count", "refused-new-plan-required"]
    assert api.apply_submissions() == []
