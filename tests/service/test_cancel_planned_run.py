"""`POST /runs/{run_id}/cancel` withdraws a finished plan; a cancelled run cannot be approved or applied.

The cases follow User Story 4 of spec 019 and the cancel, approve and apply sections of its HTTP
contract (``dev/specs/019-plan-approval/contracts/http-api.md``).
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from hashlib import sha256
from itertools import count
from typing import TYPE_CHECKING

import pytest

pytest.importorskip("prefect")
pytest.importorskip("fastapi")

from fastapi.testclient import TestClient

from infrahub_sync.configuration import ConfigurationPackage
from infrahub_sync.product_store import (
    ExecutionMergeWriteback,
    MutationReceipt,
    PrefectExecutionLink,
    ProductProjection,
    ProductRun,
    local_product_projection,
)
from infrahub_sync.service.app import create_app
from infrahub_sync.service.auth import PRINCIPALS_ENV, EnvironmentPrincipalResolver
from infrahub_sync.service.models import PlanOperationResource, PlanResource, PlanSummaryResource
from infrahub_sync.service.orchestration import CancellationResult, Observation, PoolStatus, Submission
from infrahub_sync.service.service import PLAN_ARTIFACT_ID, RunService
from infrahub_sync.service.settings import REQUIRE_APPROVAL_ENV

if TYPE_CHECKING:
    from pathlib import Path

    from httpx import Response

    from infrahub_sync.product_store import ConfigurationVersion

OWNER_TOKEN = "owner-token-withdraw-0001"  # noqa: S105 - deliberate non-secret boundary canary.
OTHER_TOKEN = "other-token-withdraw-0002"  # noqa: S105 - deliberate non-secret boundary canary.
ADMIN_TOKEN = "admin-token-withdraw-0003"  # noqa: S105 - deliberate non-secret boundary canary.
CHECKSUM = "a" * 64
RUN_ID = "run-withdraw-001"
_WORKER_ID = "0c6c1d8e-3f0a-4f1e-9b8d-6a0f2e4b7c91"
_ORDINAL = count(1)


class _RecordingOrchestration:
    """Records submissions and cancellations, so a test can show a withdrawal sends Prefect nothing."""

    def __init__(self) -> None:
        self.submissions: list[dict[str, object]] = []
        self.cancelled: list[str] = []

    async def submit(self, parameters: dict[str, object], *, idempotency_key: str) -> Submission:
        del idempotency_key
        self.submissions.append(parameters)
        return Submission(flow_run_id=f"flow-submitted-{len(self.submissions)}", state="pending")

    async def observe(self, flow_run_id: str) -> Observation:  # noqa: PLR6301
        del flow_run_id
        return Observation(available=True, state="running")

    async def pool_status(self, work_pool_name: str, now: datetime) -> PoolStatus:  # noqa: PLR6301
        del work_pool_name, now
        return PoolStatus(detail_available=False, queue_depth=None, observed_at=None)

    async def cancel(self, flow_run_id: str) -> CancellationResult:
        self.cancelled.append(flow_run_id)
        return CancellationResult(acknowledged=True)


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
    monkeypatch.delenv(REQUIRE_APPROVAL_ENV, raising=False)
    resolver = EnvironmentPrincipalResolver.from_environment()
    projection = local_product_projection(tmp_path.resolve())
    orchestration = _RecordingOrchestration()
    version = projection.create_configuration(_registered_package())
    service = RunService(projection, orchestration, secrets=resolver.secret_values)
    return TestClient(create_app(service, resolver)), projection, orchestration, version


def _start_execution(projection: ProductProjection, purpose: str) -> str:
    """Submit one execution the way the service does: reserve, append, answer the receipt."""
    ordinal = next(_ORDINAL)
    now = datetime.now(timezone.utc)
    reserved, _created = projection.reserve_mutation(
        MutationReceipt(
            receipt_id=f"m-fixture-{ordinal}",
            actor="owner",
            key_digest=sha256(f"fixture-key-{ordinal}".encode()).hexdigest(),
            operation=purpose,
            target_run_id=RUN_ID,
            request_fingerprint=sha256(f"{purpose}:{ordinal}".encode()).hexdigest(),
            reason="withdraw fixture",
            resource_id=RUN_ID,
            run_id=RUN_ID,
            prefect_key=sha256(f"prefect:fixture-{ordinal}".encode()).hexdigest(),
            created_at=now,
            updated_at=now,
        )
    )
    flow_run_id = f"flow-{purpose}-{ordinal}"
    projection.add_prefect_execution(
        RUN_ID,
        PrefectExecutionLink(flow_run_id=flow_run_id, purpose=purpose, attempt=1, submitted_at=now),
        receipt_id=reserved.receipt_id,
    )
    projection.complete_mutation(
        reserved.receipt_id, response_status=202, response_body={"accepted": True}, flow_run_id=flow_run_id
    )
    return flow_run_id


def _finish_execution(projection: ProductProjection, purpose: str) -> None:
    """Run one execution to a successful end, as its worker does."""
    flow_run_id = _start_execution(projection, purpose)
    projection.claim_execution(RUN_ID, flow_run_id, worker_id=_WORKER_ID)
    projection.commit_claimed_execution(
        RUN_ID,
        flow_run_id,
        worker_id=_WORKER_ID,
        terminal_at=datetime.now(timezone.utc),
        terminal_state="completed",
        terminal_outcome="succeeded",
        writeback=ExecutionMergeWriteback(results={purpose: {"outcome": "succeeded"}}),
    )


def _stored_run(projection: ProductProjection, version: ConfigurationVersion, *, phase: str = "planned") -> None:
    """Store a run whose plan stage finished and whose saved plan is published."""
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
    _finish_execution(projection, "plan")
    plan = PlanResource(
        run_id=RUN_ID,
        checksum=CHECKSUM,
        checksum_ok=True,
        verification_notes=(),
        summary=PlanSummaryResource(
            by_action={"create": 1},
            by_kind={"Device": 1},
            total=1,
            delete_operations_computed=True,
            deletes_not_executed=0,
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


def _post(client: TestClient, stage: str, body: dict[str, object], *, key: str, token: str = OWNER_TOKEN) -> Response:
    return client.post(
        f"/runs/{RUN_ID}/{stage}",
        headers={"Authorization": f"Bearer {token}", "Idempotency-Key": key},
        json=body,
    )


def _cancel(client: TestClient, *, key: str = "cancel-key-1", token: str = OWNER_TOKEN) -> Response:
    return _post(client, "cancel", {"reason": "the plan has a mistake"}, key=key, token=token)


def _approve(client: TestClient, *, key: str = "approve-key-1") -> Response:
    return _post(client, "approve", {"expected_checksum": CHECKSUM, "reason": "reviewed"}, key=key)


def _apply(client: TestClient, *, key: str = "apply-key-1") -> Response:
    return _post(
        client, "apply", {"expected_checksum": CHECKSUM, "reason": "approved", "confirm_writes": True}, key=key
    )


def _outcomes(projection: ProductProjection, operation: str) -> list[str]:
    return [event.outcome for event in projection.audit_events(RUN_ID) if event.operation == operation]


def _receipt_for(projection: ProductProjection, key: str) -> MutationReceipt | None:
    return projection.lookup_mutation("owner", sha256(key.encode()).hexdigest()).value


# T035: the cancel endpoint.


@pytest.mark.parametrize("approved", [False, True], ids=["unapproved", "approved"])
def test_cancelling_a_planned_run_withdraws_it_without_calling_prefect(api: _Api, *, approved: bool) -> None:
    client, projection, orchestration, version = api
    _stored_run(projection, version)
    approval = _approve(client).json()["run"]["approval"] if approved else None

    response = _cancel(client)

    assert response.status_code == 200, response.text
    run = response.json()["run"]
    assert run["phase"] == "cancelled"
    assert run["approval"] == approval
    stored = projection.lookup_run(RUN_ID).value
    assert stored is not None
    assert (stored.phase, stored.outcome) == ("cancelled", "cancelled")
    assert (stored.approval is not None) is approved
    assert orchestration.submissions == []
    assert orchestration.cancelled == []
    assert _outcomes(projection, "cancel") == ["accepted-withdrawn"]


def test_cancelling_a_run_with_an_active_execution_cancels_that_execution_as_before(api: _Api) -> None:
    client, projection, orchestration, version = api
    _stored_run(projection, version)
    verifying = _start_execution(projection, "verify")

    response = _cancel(client)

    assert response.status_code == 202, response.text
    assert orchestration.cancelled == [verifying]
    assert _outcomes(projection, "cancel") == ["accepted"]


def test_cancelling_an_already_cancelled_run_is_refused_and_says_so(api: _Api) -> None:
    client, projection, _orchestration, version = api
    _stored_run(projection, version)
    assert _cancel(client, key="cancel-first").status_code == 200

    response = _cancel(client, key="cancel-second")

    assert response.status_code == 409
    error = response.json()["error"]
    assert error["code"] == "run-already-cancelled"
    assert "already cancelled" in error["message"]
    assert _receipt_for(projection, "cancel-second") is None
    assert _outcomes(projection, "cancel") == ["accepted-withdrawn", "refused-cancelled"]


@pytest.mark.parametrize("phase", ["applied", "failed"])
def test_cancelling_an_applied_or_plan_failed_run_is_refused_as_before(api: _Api, phase: str) -> None:
    client, projection, orchestration, version = api
    _stored_run(projection, version, phase=phase)

    response = _cancel(client)

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "execution-terminal"
    stored = projection.lookup_run(RUN_ID).value
    assert stored is not None
    assert stored.phase == phase
    assert orchestration.cancelled == []
    assert _outcomes(projection, "cancel") == ["refused-terminal"]


def test_a_withdrawal_retried_with_the_same_key_replays_the_stored_response(api: _Api) -> None:
    client, projection, _orchestration, version = api
    _stored_run(projection, version)

    first = _cancel(client)
    replayed = _cancel(client)

    assert (first.status_code, replayed.status_code) == (200, 200)
    assert replayed.json() == first.json()
    assert _outcomes(projection, "cancel") == ["accepted-withdrawn", "replayed"]


def test_another_non_admin_actor_cannot_withdraw_the_run(api: _Api) -> None:
    client, projection, _orchestration, version = api
    _stored_run(projection, version)

    response = _cancel(client, token=OTHER_TOKEN)

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "forbidden"
    stored = projection.lookup_run(RUN_ID).value
    assert stored is not None
    assert stored.phase == "planned"


def test_an_administrator_may_withdraw_another_actors_run(api: _Api) -> None:
    client, projection, _orchestration, version = api
    _stored_run(projection, version)

    response = _cancel(client, token=ADMIN_TOKEN)

    assert response.status_code == 200, response.text
    assert response.json()["run"]["phase"] == "cancelled"


# T036: a cancelled run cannot be approved or applied.


def test_approving_a_withdrawn_run_is_refused_before_a_receipt_is_reserved(api: _Api) -> None:
    client, projection, _orchestration, version = api
    _stored_run(projection, version)
    assert _cancel(client).status_code == 200

    response = _approve(client, key="approve-after-cancel")

    assert response.status_code == 409
    error = response.json()["error"]
    assert error["code"] == "run-cancelled"
    assert "cancelled" in error["message"]
    assert _receipt_for(projection, "approve-after-cancel") is None
    assert _outcomes(projection, "approve") == ["refused-cancelled"]


@pytest.mark.parametrize("setting", [None, "true"], ids=["setting-off", "setting-on"])
def test_an_approved_then_withdrawn_run_cannot_be_applied(
    api: _Api, monkeypatch: pytest.MonkeyPatch, setting: str | None
) -> None:
    client, projection, orchestration, version = api
    _stored_run(projection, version)
    assert _approve(client).status_code == 200
    assert _cancel(client).status_code == 200
    if setting is not None:
        monkeypatch.setenv(REQUIRE_APPROVAL_ENV, setting)

    response = _apply(client, key="apply-after-cancel")

    assert response.status_code == 409
    error = response.json()["error"]
    assert error["code"] == "run-cancelled"
    assert "cancelled" in error["message"]
    assert _receipt_for(projection, "apply-after-cancel") is None
    assert orchestration.submissions == []
    assert _outcomes(projection, "apply") == ["refused-cancelled"]


def test_a_run_whose_verify_execution_was_cancelled_cannot_be_applied(api: _Api) -> None:
    """Phase ``cancelled`` reached when Prefect cancelled a verify is refused the same way."""
    client, projection, orchestration, version = api
    _stored_run(projection, version, phase="cancelled")
    _finish_execution(projection, "verify")

    applied = _apply(client, key="apply-after-cancelled-verify")
    approved = _approve(client, key="approve-after-cancelled-verify")

    assert (applied.status_code, applied.json()["error"]["code"]) == (409, "run-cancelled")
    assert (approved.status_code, approved.json()["error"]["code"]) == (409, "run-cancelled")
    assert _receipt_for(projection, "apply-after-cancelled-verify") is None
    assert orchestration.submissions == []
