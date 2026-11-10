"""`POST /runs/{run_id}/cancel` withdraws a finished plan; a cancelled run cannot be approved or applied.

The cases follow User Story 4 of spec 019 and the cancel, approve and apply sections of its HTTP
contract (``dev/specs/019-plan-approval/contracts/http-api.md``).
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from hashlib import sha256
from itertools import count
from typing import TYPE_CHECKING, Any

import pytest

pytest.importorskip("prefect")
pytest.importorskip("fastapi")

from fastapi.testclient import TestClient

from infrahub_sync.configuration import ConfigurationPackage
from infrahub_sync.product_store import (
    AuditEvent,
    ExecutionMergeWriteback,
    LookupResult,
    MutationReceipt,
    PrefectExecutionLink,
    ProductProjection,
    ProductRun,
    local_product_projection,
)
from infrahub_sync.service.app import create_app
from infrahub_sync.service.auth import PRINCIPALS_ENV, EnvironmentPrincipalResolver
from infrahub_sync.service.events import RUN_CANCELLED
from infrahub_sync.service.models import PlanOperationResource, PlanResource, PlanSummaryResource
from infrahub_sync.service.orchestration import CancellationResult, Observation, PoolStatus, RunLogRead, Submission
from infrahub_sync.service.service import PLAN_ARTIFACT_ID, RunService
from infrahub_sync.service.settings import REQUIRE_APPROVAL_ENV

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from pathlib import Path

    from httpx import Response

    from infrahub_sync.product_store import ConfigurationVersion
    from infrahub_sync.product_store.models import RunApproval

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

    async def read_logs(self, flow_run_ids: Sequence[str]) -> RunLogRead:  # noqa: PLR6301
        del flow_run_ids
        return RunLogRead(expired=False)


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


def _reserve_receipt(projection: ProductProjection, purpose: str) -> MutationReceipt:
    """Reserve one stage's receipt the way the service does, before it submits anything."""
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
    return reserved


def _start_execution(projection: ProductProjection, purpose: str) -> str:
    """Submit one execution the way the service does: reserve, append, answer the receipt."""
    reserved = _reserve_receipt(projection, purpose)
    flow_run_id = f"flow-{purpose}-{reserved.receipt_id}"
    projection.add_prefect_execution(
        RUN_ID,
        PrefectExecutionLink(
            flow_run_id=flow_run_id, purpose=purpose, attempt=1, submitted_at=datetime.now(timezone.utc)
        ),
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
    """The outcomes of the request decisions for ``operation``, without the run's end event."""
    return [
        event.outcome
        for event in projection.audit_events(RUN_ID)
        if event.operation == operation and event.event_type != RUN_CANCELLED
    ]


def _cancelled_events(projection: ProductProjection) -> list[AuditEvent]:
    return [event for event in projection.audit_events(RUN_ID) if event.event_type == RUN_CANCELLED]


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
    decision = next(event for event in projection.audit_events(RUN_ID) if event.outcome == "accepted-withdrawn")
    assert decision.event_type == "infrahub_sync.run.accepted"
    # No Prefect acknowledgement follows a withdrawal, so the Sync API records the run's end event.
    [ended] = _cancelled_events(projection)
    assert (ended.operation, ended.outcome, ended.actor) == ("cancel", "cancelled", "owner")
    assert ended.created_at == stored.finished_at


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


def test_the_cancel_route_documents_the_withdrawal_status(api: _Api) -> None:
    """A withdrawal answers ``200`` and a cancelled execution ``202``; the OpenAPI document lists both."""
    client, _projection, _orchestration, _version = api

    responses = client.get("/openapi.json").json()["paths"]["/runs/{run_id}/cancel"]["post"]["responses"]

    assert {"200", "202"} <= responses.keys()


# Store refusals: the run changed after the request read it, so the store's answer, decided under
# the run's row lock, is the response and decides the audit outcome.


def _read_before_the_change(
    monkeypatch: pytest.MonkeyPatch, projection: ProductProjection, snapshot: ProductRun
) -> None:
    """Answer the next run lookup with ``snapshot``: the run as the request read it, before another request changed it."""
    lookup_run = projection.lookup_run
    pending = [snapshot]

    def stale_then_current(run_id: str) -> LookupResult[ProductRun]:
        return LookupResult(value=pending.pop()) if pending else lookup_run(run_id)

    monkeypatch.setattr(projection, "lookup_run", stale_then_current)


def _as_planned(projection: ProductProjection) -> ProductRun:
    """The stored run as a request read it while it was still in phase ``planned`` and unapproved."""
    stored = projection.lookup_run(RUN_ID).value
    assert stored is not None
    return stored.model_copy(update={"phase": "planned", "approval": None})


@pytest.mark.parametrize(
    ("stored_phase", "approved_first", "code", "outcome"),
    [
        ("cancelled", False, "run-cancelled", "refused-cancelled"),
        ("applied", False, "run-not-approvable", "refused-not-approvable"),
        ("planned", True, "already-approved", "refused-already-approved"),
    ],
    ids=["withdrawn", "applied", "approved"],
)
def test_an_approval_the_store_refuses_is_audited_with_the_stored_refusal(  # noqa: PLR0913
    api: _Api,
    monkeypatch: pytest.MonkeyPatch,
    stored_phase: str,
    code: str,
    outcome: str,
    *,
    approved_first: bool,
) -> None:
    client, projection, _orchestration, version = api
    _stored_run(projection, version, phase=stored_phase)
    snapshot = _as_planned(projection)
    if approved_first:
        assert _approve(client, key="approve-first").status_code == 200
    _read_before_the_change(monkeypatch, projection, snapshot)

    response = _approve(client, key="approve-late")

    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == code
    receipt = _receipt_for(projection, "approve-late")
    assert receipt is not None
    assert receipt.response_status == 409
    assert _outcomes(projection, "approve")[-1] == outcome


def test_an_approval_is_refused_when_an_execution_started_after_the_request_read_the_run(
    api: _Api, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, projection, _orchestration, version = api
    _stored_run(projection, version)
    snapshot = _as_planned(projection)
    _start_execution(projection, "verify")
    _read_before_the_change(monkeypatch, projection, snapshot)

    response = _approve(client)

    assert response.status_code == 409, response.text
    error = response.json()["error"]
    assert error["code"] == "run-not-approvable"
    assert "verify execution is in progress" in error["message"]
    stored = projection.lookup_run(RUN_ID).value
    assert stored is not None
    assert stored.approval is None
    assert _outcomes(projection, "approve") == ["refused-not-approvable"]


@pytest.mark.parametrize(
    ("stored_phase", "code", "outcome"),
    [
        ("cancelled", "run-already-cancelled", "refused-cancelled"),
        ("applied", "execution-terminal", "refused-terminal"),
    ],
    ids=["already-withdrawn", "applied"],
)
def test_a_withdrawal_the_store_refuses_is_audited_with_the_stored_refusal(
    api: _Api, monkeypatch: pytest.MonkeyPatch, stored_phase: str, code: str, outcome: str
) -> None:
    client, projection, orchestration, version = api
    _stored_run(projection, version, phase=stored_phase)
    _read_before_the_change(monkeypatch, projection, _as_planned(projection))

    response = _cancel(client)

    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == code
    stored = projection.lookup_run(RUN_ID).value
    assert stored is not None
    assert stored.phase == stored_phase
    assert orchestration.cancelled == []
    assert _outcomes(projection, "cancel") == [outcome]


def test_a_withdrawal_refused_because_another_stage_is_reserved_is_audited_as_a_conflict(api: _Api) -> None:
    """A verify reserved but not yet submitted holds the run, so the store refuses the withdrawal."""
    client, projection, orchestration, version = api
    _stored_run(projection, version)
    _reserve_receipt(projection, "verify")

    response = _cancel(client)

    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == "run-execution-conflict"
    stored = projection.lookup_run(RUN_ID).value
    assert stored is not None
    assert stored.phase == "planned"
    assert orchestration.cancelled == []
    assert _outcomes(projection, "cancel") == ["refused-run-execution-conflict"]


# Two requests with one key: the second finds the receipt already answered and replays that answer.


def test_an_approval_whose_receipt_a_concurrent_retry_answered_first_replays_that_answer(
    api: _Api, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, projection, _orchestration, version = api
    _stored_run(projection, version)
    record_approval = projection.record_approval

    def answered_first(  # noqa: PLR0913 - mirrors the store method
        run_id: str,
        approval: RunApproval,
        receipt: MutationReceipt,
        response: Mapping[str, Any],
        *,
        events: Sequence[AuditEvent] = (),
        secrets: Sequence[str] = (),
    ) -> bool:
        record_approval(run_id, approval, receipt, response, events=events, secrets=secrets)  # the concurrent retry
        return record_approval(run_id, approval, receipt, response, events=events, secrets=secrets)

    monkeypatch.setattr(projection, "record_approval", answered_first)

    response = _approve(client)

    assert response.status_code == 200, response.text
    assert response.json()["run"]["approval"]["approved_by"] == "owner"
    # The concurrent retry stored the approval and its event; this request replays it.
    assert _outcomes(projection, "approve") == ["accepted", "replayed"]


def test_a_withdrawal_whose_receipt_a_concurrent_retry_answered_first_replays_that_answer(
    api: _Api, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, projection, _orchestration, version = api
    _stored_run(projection, version)
    cancel_planned_run = projection.cancel_planned_run

    def answered_first(  # noqa: PLR0913 - mirrors the store method
        run_id: str,
        receipt: MutationReceipt,
        response: Mapping[str, Any],
        *,
        cancelled_at: datetime,
        events: Sequence[AuditEvent] = (),
        secrets: Sequence[str] = (),
    ) -> bool:
        cancel_planned_run(run_id, receipt, response, cancelled_at=cancelled_at, events=events, secrets=secrets)
        return cancel_planned_run(run_id, receipt, response, cancelled_at=cancelled_at, events=events, secrets=secrets)

    monkeypatch.setattr(projection, "cancel_planned_run", answered_first)

    response = _cancel(client)

    assert response.status_code == 200, response.text
    assert response.json()["run"]["phase"] == "cancelled"
    # The concurrent retry withdrew the run and stored its events once; this request replays it.
    assert _outcomes(projection, "cancel") == ["accepted-withdrawn", "replayed"]
    assert len(_cancelled_events(projection)) == 1
