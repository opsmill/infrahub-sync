"""`POST /runs/{run_id}/approve`: record approval of a run's saved plan without applying it.

The cases follow the order of evaluation in the HTTP contract of spec 019
(``dev/specs/019-plan-approval/contracts/http-api.md``).
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

import pytest

pytest.importorskip("prefect")
pytest.importorskip("fastapi")

from fastapi.testclient import TestClient

from infrahub_sync.configuration import ConfigurationPackage
from infrahub_sync.product_store import PrefectExecutionLink, ProductProjection, ProductRun, local_product_projection
from infrahub_sync.service.app import create_app
from infrahub_sync.service.auth import PRINCIPALS_ENV, EnvironmentPrincipalResolver
from infrahub_sync.service.models import PlanOperationResource, PlanResource, PlanSummaryResource
from infrahub_sync.service.orchestration import CancellationResult, Observation, PoolStatus, Submission
from infrahub_sync.service.service import PLAN_ARTIFACT_ID, RunService
from tests.service.execution_fixtures import append_execution

if TYPE_CHECKING:
    from pathlib import Path

    from httpx import Response

    from infrahub_sync.product_store import ConfigurationVersion

OWNER_TOKEN = "owner-token-approval-0001"  # noqa: S105 - deliberate non-secret boundary canary.
OTHER_TOKEN = "other-token-approval-0002"  # noqa: S105 - deliberate non-secret boundary canary.
ADMIN_TOKEN = "admin-token-approval-0003"  # noqa: S105 - deliberate non-secret boundary canary.
CHECKSUM = "a" * 64
OTHER_CHECKSUM = "b" * 64
RUN_ID = "run-approval-001"


class _RecordingOrchestration:
    """Records every submission so a test can show approval submits nothing."""

    def __init__(self) -> None:
        self.submissions: list[dict[str, object]] = []

    async def submit(self, parameters: dict[str, object], *, idempotency_key: str) -> Submission:
        del idempotency_key
        self.submissions.append(parameters)
        return Submission(flow_run_id="flow-unexpected", state="pending")

    async def observe(self, flow_run_id: str) -> Observation:  # noqa: PLR6301
        del flow_run_id
        return Observation(available=False, state=None, reason="prefect-execution-unavailable")

    async def pool_status(self, work_pool_name: str, now: datetime) -> PoolStatus:  # noqa: PLR6301
        del work_pool_name, now
        return PoolStatus(detail_available=False, queue_depth=None, observed_at=None)

    async def cancel(self, flow_run_id: str) -> CancellationResult:  # noqa: PLR6301
        del flow_run_id
        return CancellationResult(acknowledged=False, reason="not-used")


def _registered_package() -> ConfigurationPackage:
    return ConfigurationPackage.model_validate(
        {
            "format_version": 1,
            "configuration": {
                "name": "registered-inventory",
                "source": {
                    "name": "netbox",
                    "settings": {"url": "https://netbox.example", "token": {"$credential": "token"}},
                },
                "destination": {
                    "name": "infrahub",
                    "settings": {"url": "https://infrahub.example", "token": {"$credential": "token"}},
                },
                "order": [],
                "schema_mapping": [],
                "diffsync_flags": [],
                "incremental": None,
            },
            "credentials": {"token": {"provider": "env", "identifier": "INFRAHUB_SYNC_CREDENTIAL_TOKEN"}},
        }
    )


_Api = tuple[TestClient, ProductProjection, _RecordingOrchestration, "ConfigurationVersion"]


@pytest.fixture(name="api")
def _api_fixture(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> _Api:
    monkeypatch.setenv(
        PRINCIPALS_ENV,
        json.dumps(
            {
                "owner": {"token": OWNER_TOKEN},
                "other": {"token": OTHER_TOKEN},
                "admin": {"token": ADMIN_TOKEN, "administrator": True},
            }
        ),
    )
    resolver = EnvironmentPrincipalResolver.from_environment()
    projection = local_product_projection(tmp_path.resolve())
    orchestration = _RecordingOrchestration()
    version = projection.create_configuration(_registered_package())
    service = RunService(projection, orchestration, secrets=resolver.secret_values)
    return TestClient(create_app(service, resolver)), projection, orchestration, version


def _stored_run(
    projection: ProductProjection,
    version: ConfigurationVersion,
    *,
    phase: str = "planned",
    checksum_ok: bool = True,
    publish: bool = True,
) -> None:
    projection.create_run(
        ProductRun(
            run_id=RUN_ID,
            operation="plan",
            configuration_reference=f"{version.config_id}@{version.registry_version}",
            config_id=version.config_id,
            registry_version=version.registry_version,
            package_checksum=version.package_checksum,
            actor="owner",
            started_at=datetime(2026, 11, 10, 12, tzinfo=timezone.utc),
            phase=phase,
        )
    )
    if not publish:
        return
    plan = PlanResource(
        run_id=RUN_ID,
        checksum=CHECKSUM,
        checksum_ok=checksum_ok,
        verification_notes=(),
        summary=PlanSummaryResource(
            by_action={"create": 1, "update": 2, "delete": 3},
            by_kind={"Device": 6},
            total=6,
            delete_operations_computed=True,
            deletes_not_executed=3,
        ),
        operations=(
            PlanOperationResource(
                operation_id="op-001", action="create", kind="Device", identity={"name": "edge-01"}, tier=0
            ),
        ),
    )
    projection.publish_artifact(
        RUN_ID,
        artifact_id=PLAN_ARTIFACT_ID,
        kind="saved-plan-review",
        media_type="application/json",
        data=plan.model_dump_json().encode(),
    )


def _approve(
    client: TestClient,
    *,
    key: str = "approve-key-1",
    token: str = OWNER_TOKEN,
    body: dict[str, Any] | None = None,
) -> Response:
    payload = {"expected_checksum": CHECKSUM, "reason": "reviewed by the network team"} if body is None else body
    return client.post(
        f"/runs/{RUN_ID}/approve",
        headers={"Authorization": f"Bearer {token}", "Idempotency-Key": key},
        json=payload,
    )


def _outcomes(projection: ProductProjection) -> list[str]:
    return [event.outcome for event in projection.audit_events(RUN_ID) if event.operation == "approve"]


def test_approve_records_the_approval_and_returns_the_run_with_the_plan_summary(api: _Api) -> None:
    client, projection, orchestration, version = api
    _stored_run(projection, version)

    response = _approve(client)

    assert response.status_code == 200, response.text
    body = response.json()
    approval = body["run"]["approval"]
    assert approval["approved_by"] == "owner"
    assert approval["plan_checksum"] == CHECKSUM
    assert approval["reason"] == "reviewed by the network team"
    assert approval["approved_at"]
    assert body["run"]["phase"] == "planned"
    assert body["plan_summary"] == {"creates": 1, "updates": 2, "deletes_recorded": 3}
    assert orchestration.submissions == []
    stored = projection.lookup_run(RUN_ID).value
    assert stored is not None
    assert stored.approval is not None
    assert stored.approval.approved_by == "owner"
    assert _outcomes(projection) == ["accepted"]


def test_get_run_and_the_run_list_carry_the_approval_or_null(api: _Api) -> None:
    client, projection, _orchestration, version = api
    _stored_run(projection, version)
    headers = {"Authorization": f"Bearer {OWNER_TOKEN}"}

    before = client.get(f"/runs/{RUN_ID}", headers=headers).json()["run"]
    approved = _approve(client).json()["run"]["approval"]
    after = client.get(f"/runs/{RUN_ID}", headers=headers).json()["run"]
    listed = client.get("/runs", headers=headers).json()["items"]

    assert before["approval"] is None
    assert after["approval"] == approved
    assert [item["approval"] for item in listed] == [approved]


def test_an_administrator_may_approve_another_actors_run(api: _Api) -> None:
    client, projection, _orchestration, version = api
    _stored_run(projection, version)

    response = _approve(client, token=ADMIN_TOKEN)

    assert response.status_code == 200, response.text
    assert response.json()["run"]["approval"]["approved_by"] == "admin"


def test_another_non_admin_actor_is_refused(api: _Api) -> None:
    client, projection, _orchestration, version = api
    _stored_run(projection, version)

    response = _approve(client, token=OTHER_TOKEN)

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "forbidden"
    stored = projection.lookup_run(RUN_ID).value
    assert stored is not None
    assert stored.approval is None
    assert _outcomes(projection) == ["refused-authorization"]


@pytest.mark.parametrize("phase", ["failed", "applied"])
def test_a_run_outside_phase_planned_is_not_approvable_and_the_message_names_the_phase(api: _Api, phase: str) -> None:
    client, projection, _orchestration, version = api
    _stored_run(projection, version, phase=phase)

    response = _approve(client)

    assert response.status_code == 409
    error = response.json()["error"]
    assert error["code"] == "run-not-approvable"
    assert phase in error["message"]
    assert _outcomes(projection) == ["refused-not-approvable"]


def test_a_run_still_planning_is_not_approvable_before_its_plan_is_looked_up(api: _Api) -> None:
    client, projection, _orchestration, version = api
    _stored_run(projection, version, phase="accepted", publish=False)
    append_execution(
        projection,
        RUN_ID,
        PrefectExecutionLink(
            flow_run_id="flow-still-planning", purpose="plan", attempt=1, submitted_at=datetime.now(timezone.utc)
        ),
    )

    response = _approve(client)

    assert response.status_code == 409
    error = response.json()["error"]
    assert error["code"] == "run-not-approvable"
    assert "plan" in error["message"]


def test_a_planned_run_with_an_execution_in_progress_names_the_active_stage(api: _Api) -> None:
    client, projection, _orchestration, version = api
    _stored_run(projection, version)
    append_execution(
        projection,
        RUN_ID,
        PrefectExecutionLink(
            flow_run_id="flow-verifying", purpose="verify", attempt=1, submitted_at=datetime.now(timezone.utc)
        ),
    )

    response = _approve(client)

    assert response.status_code == 409
    error = response.json()["error"]
    assert error["code"] == "run-not-approvable"
    assert "a verify execution is in progress" in error["message"]
    assert _outcomes(projection) == ["refused-not-approvable"]


def test_a_plan_whose_checksum_was_not_verified_is_refused(api: _Api) -> None:
    client, projection, _orchestration, version = api
    _stored_run(projection, version, checksum_ok=False)

    response = _approve(client)

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "plan-checksum-unverified"
    assert _outcomes(projection) == ["refused-plan-unverified"]


@pytest.mark.parametrize("artifact", [None, b"not a plan"], ids=["missing", "invalid"])
def test_a_plan_that_cannot_be_read_is_refused_and_the_refusal_is_audited(api: _Api, artifact: bytes | None) -> None:
    """FR-004 says which case applies; FR-008 records every approval request, refused ones included."""
    client, projection, _orchestration, version = api
    _stored_run(projection, version, publish=False)
    if artifact is not None:
        projection.publish_artifact(
            RUN_ID, artifact_id=PLAN_ARTIFACT_ID, kind="saved-plan-review", media_type="application/json", data=artifact
        )

    response = _approve(client)

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "plan-unavailable"
    stored = projection.lookup_run(RUN_ID).value
    assert stored is not None
    assert stored.approval is None
    assert _outcomes(projection) == ["refused-plan-unavailable"]


def test_a_different_expected_checksum_is_refused_showing_both_checksums(api: _Api) -> None:
    client, projection, _orchestration, version = api
    _stored_run(projection, version)

    response = _approve(client, body={"expected_checksum": OTHER_CHECKSUM})

    assert response.status_code == 409
    error = response.json()["error"]
    assert error["code"] == "checksum-mismatch"
    assert CHECKSUM in error["message"]
    assert OTHER_CHECKSUM in error["message"]
    stored = projection.lookup_run(RUN_ID).value
    assert stored is not None
    assert stored.approval is None
    assert _outcomes(projection) == ["refused-checksum"]


def test_a_second_approval_is_refused_with_the_existing_approval_in_details(api: _Api) -> None:
    client, projection, _orchestration, version = api
    _stored_run(projection, version)
    first = _approve(client).json()["run"]["approval"]

    response = _approve(client, key="approve-key-2", token=ADMIN_TOKEN, body={"expected_checksum": CHECKSUM})

    assert response.status_code == 409
    error = response.json()["error"]
    assert error["code"] == "already-approved"
    assert error["details"] == {"approval": first}
    stored = projection.lookup_run(RUN_ID).value
    assert stored is not None
    assert stored.approval is not None
    assert stored.approval.approved_by == "owner"
    assert _outcomes(projection) == ["accepted", "refused-already-approved"]


def test_a_retry_with_the_same_key_replays_the_stored_body(api: _Api) -> None:
    client, projection, orchestration, version = api
    _stored_run(projection, version)
    first = _approve(client)

    replayed = _approve(client)

    assert replayed.status_code == 200
    assert replayed.json() == first.json()
    assert orchestration.submissions == []
    assert _outcomes(projection) == ["accepted", "replayed"]


def test_the_same_key_with_a_different_body_is_refused(api: _Api) -> None:
    client, projection, _orchestration, version = api
    _stored_run(projection, version)
    _approve(client)

    response = _approve(client, body={"expected_checksum": CHECKSUM, "reason": "another reason"})

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "idempotency-conflict"


@pytest.mark.parametrize(
    ("headers", "body"),
    [
        ({"Authorization": f"Bearer {OWNER_TOKEN}"}, {"expected_checksum": CHECKSUM}),
        (
            {"Authorization": f"Bearer {OWNER_TOKEN}", "Idempotency-Key": "k"},
            {"expected_checksum": CHECKSUM, "confirm_writes": True},
        ),
    ],
    ids=["missing-idempotency-key", "unknown-field"],
)
def test_a_malformed_request_is_refused_with_422(api: _Api, headers: dict[str, str], body: dict[str, Any]) -> None:
    client, projection, _orchestration, version = api
    _stored_run(projection, version)

    response = client.post(f"/runs/{RUN_ID}/approve", headers=headers, json=body)

    assert response.status_code == 422
    stored = projection.lookup_run(RUN_ID).value
    assert stored is not None
    assert stored.approval is None
