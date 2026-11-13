"""Resume an interrupted apply through the Sync API, the worker and the engine (spec 022 T022).

User story 1, acceptance scenarios 1 to 4:

1. an apply of a 10,000-operation plan, whose marker was saved at 6,000 committed operations and
   whose worker stopped after 6,200, is verified and resumed; the resume writes operations
   6,001 to 10,000 in a new execution on the same run, and the run ends applied with every
   operation written;
2. the run shows its progress marker and, per kind, the committed, uncertain and not reached
   operations;
3. a resume with another checksum is refused, and the error shows both checksums;
4. a resume records the actor, the time and the optional reason.

Each refusal of contracts/http-api.md is checked, and every request, accepted or refused, is
recorded in the run's audit history with operation `resume`. Positions are indexes in the
saved plan's stored order: position 5,999 is the 6,000th operation.
"""

# ruff: noqa: F811 - every API test takes the `service_api` fixture imported from `test_http_api`.

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, NoReturn, cast

import pytest

pytest.importorskip("prefect")
pytest.importorskip("opsmill_prefect_extras")
pytest.importorskip("fastapi")

from infrahub_sync.execution import RunResult
from infrahub_sync.plan.errors import OperationApplyFailedError, StaleDestinationIdError
from infrahub_sync.plan.models import ApplyRecord
from infrahub_sync.potenda import Potenda
from infrahub_sync.potenda.apply_progress import ApplyProgressLostError
from infrahub_sync.product_store import (
    ApplyProgress,
    ExecutionFinishWriteback,
    ExecutionMergeWriteback,
    MutationReceipt,
    PrefectExecutionLink,
    ProductProjection,
    ProductRun,
    local_product_projection,
)
from infrahub_sync.service import flow as service_flow
from infrahub_sync.service.apply_progress import StoreApplyProgressObserver
from infrahub_sync.service.flow import resume_start_after, service_sync_run
from infrahub_sync.service.models import PlanOperationResource, PlanResource, PlanSummaryResource
from infrahub_sync.service.resume_view import apply_progress_view, uncertain_window
from infrahub_sync.service.service import PLAN_ARTIFACT_ID
from infrahub_sync.service.settings import REQUIRE_APPROVAL_ENV
from tests.configuration.validation_packages import package
from tests.plan.artifact_fixtures import CONFIG_VERSION, operation_record, write_artifact
from tests.service.execution_fixtures import bind_granting_guard
from tests.service.test_http_api import (
    _PLAN_WORKER_ID,
    ADMIN_TOKEN,
    OTHER_TOKEN,
    OWNER_TOKEN,
    _create,
    _FakeOrchestration,
    _settle_open_executions,
    service_api,  # noqa: F401 - the fixture is used by name.
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from fastapi.testclient import TestClient
    from httpx import Response

    from infrahub_sync.plan.models import PlannedOperation

_API = tuple["TestClient", ProductProjection, _FakeOrchestration]
_CHECKSUM = "c" * 64
_OTHER_CHECKSUM = "d" * 64
_TOTAL = 10_000
# Positions 0 to 6,499 are `BuiltinTag`, 6,500 to 9,999 `BuiltinStatus`: no kind boundary
# between the saved marker and the operation the worker stopped at.
_FIRST_KIND_COUNT = 6_500
# The 6,000th operation: the last one the saved marker names.
_MARKER = 5_999
# The worker stopped while dispatching the 6,201st operation.
_STOPPED_AT = 6_200
_WORKER_ID = "0f6f2d7e-3b7a-4c1e-9d55-6a1e2b3c4d5e"


def _kind(index: int) -> str:
    return "BuiltinTag" if index < _FIRST_KIND_COUNT else "BuiltinStatus"


def _operation_id(index: int) -> str:
    return f"op_{index:016x}"


def _plan_operations() -> list[tuple[str, str]]:
    return [(_operation_id(index), _kind(index)) for index in range(_TOTAL)]


# --- the Sync API ------------------------------------------------------------------------


def _publish_plan(projection: ProductProjection, run_id: str, *, total: int, left_out: int = 0) -> None:
    """Publish a review document of `total` operations over two kinds, as the plan worker does."""
    by_kind = {_kind(index): 0 for index in range(total)}
    for index in range(total):
        by_kind[_kind(index)] += 1
    plan = PlanResource(
        run_id=run_id,
        checksum=_CHECKSUM,
        checksum_ok=True,
        verification_notes=(),
        summary=PlanSummaryResource(
            by_action={"create": total},
            by_kind=by_kind,
            total=total,
            delete_operations_computed=True,
            deletes_not_executed=0,
            left_out=left_out,
            left_out_with_problem=min(left_out, 1),
            left_out_dependent=max(left_out - 1, 0),
            left_out_by_kind={"BuiltinTag": left_out} if left_out else {},
            problems=min(left_out, 1),
            problems_by_type={"wrong_value_type": 1} if left_out else {},
        ),
        operations=tuple(
            PlanOperationResource(
                operation_id=_operation_id(index),
                action="create",
                kind=_kind(index),
                identity={"name": f"node-{index}"},
                tier=0,
                payload={"name": f"node-{index}"},
                relationships=(),
            )
            for index in range(total)
        ),
    )
    projection.publish_artifact(
        run_id,
        artifact_id=PLAN_ARTIFACT_ID,
        kind="saved-plan-review",
        media_type="application/json",
        data=plan.model_dump_json().encode(),
    )


def _headers(key: str, token: str = OWNER_TOKEN) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}", "Idempotency-Key": key}


def _latest_flow_run_id(projection: ProductProjection, run_id: str) -> str:
    run = projection.lookup_run(run_id).value
    assert run is not None
    return run.prefect_executions[-1].flow_run_id


def _interrupted_run(
    api: _API, *, total: int = _TOTAL, marker: int | None = _MARKER, left_out: int = 0, approve: bool = False
) -> str:
    """A planned run whose apply saved `marker` and was then interrupted by the liveness monitor."""
    client, projection, _orchestration = api
    run_id = _create(client).json()["run"]["run_id"]
    _publish_plan(projection, run_id, total=total, left_out=left_out)
    if approve:
        _finish_planned(projection, run_id)
    _settle_open_executions(projection, run_id)
    if approve:
        approved = client.post(
            f"/runs/{run_id}/approve", headers=_headers("approve-key"), json={"expected_checksum": _CHECKSUM}
        )
        assert approved.status_code == 200, approved.json()
    applied = client.post(
        f"/runs/{run_id}/apply",
        headers=_headers("apply-key"),
        json={"expected_checksum": _CHECKSUM, "confirm_writes": True, "confirm_left_out": bool(left_out)},
    )
    assert applied.status_code == 202, applied.json()
    flow_run_id = _latest_flow_run_id(projection, run_id)
    assert projection.claim_execution(run_id, flow_run_id, worker_id=_WORKER_ID)
    if marker is not None:
        assert projection.record_apply_progress(
            run_id,
            flow_run_id,
            ApplyProgress(
                position=marker,
                operation_id=_operation_id(marker),
                total=total,
                saved_at=datetime(2026, 11, 12, 10, 15, tzinfo=timezone.utc),
            ),
        )
    assert projection.interrupt_execution(run_id, flow_run_id)
    stored = projection.lookup_run(run_id).value
    assert stored is not None
    assert stored.reconciliation_required is True
    return run_id


def _finish_planned(projection: ProductProjection, run_id: str) -> None:
    """Finish the plan stage with phase `planned`, as its worker does, so the run can be approved."""
    flow_run_id = _latest_flow_run_id(projection, run_id)
    assert projection.claim_execution(run_id, flow_run_id, worker_id=_PLAN_WORKER_ID)
    assert projection.commit_claimed_execution(
        run_id,
        flow_run_id,
        worker_id=_PLAN_WORKER_ID,
        terminal_at=datetime.now(timezone.utc),
        terminal_state="completed",
        terminal_outcome="succeeded",
        writeback=ExecutionFinishWriteback(
            phase="planned",
            outcome="planned",
            finished_at=datetime.now(timezone.utc),
            summary={"sync_name": "registered-inventory"},
            results={"plan": {"outcome": "planned"}},
        ),
    )


def _verified(api: _API, run_id: str) -> None:
    """Run `verify` on the run and finish it as its worker does when every check passed."""
    client, projection, _orchestration = api
    response = client.post(f"/runs/{run_id}/verify", headers=_headers(f"verify-{run_id}"), json={})
    assert response.status_code == 202, response.json()
    _settle_open_executions(projection, run_id)


def _resume(
    client: TestClient, run_id: str, key: str = "resume-key", token: str = OWNER_TOKEN, **body: object
) -> Response:
    request = {"expected_checksum": _CHECKSUM, "confirm_writes": True, **body}
    return client.post(f"/runs/{run_id}/resume", headers=_headers(key, token), json=request)


def _error_code(response: Response) -> str:
    return response.json()["error"]["code"]


def _resume_outcomes(projection: ProductProjection, run_id: str) -> list[str]:
    return [event.outcome for event in projection.audit_events(run_id) if event.operation == "resume"]


def _resume_submissions(orchestration: _FakeOrchestration) -> list[dict[str, object]]:
    return [parameters for parameters, _key in orchestration.submissions if parameters["stage"] == "resume"]


def test_an_interrupted_run_shows_its_marker_and_each_kinds_counts(service_api: _API) -> None:
    """Scenario 2: the progress marker and, per kind, committed, uncertain and not reached."""
    client, _projection, _orchestration = service_api
    run_id = _interrupted_run(service_api)

    body = client.get(f"/runs/{run_id}", headers={"Authorization": f"Bearer {OWNER_TOKEN}"}).json()

    progress = body["apply_progress"]
    assert progress["position"] == _MARKER
    assert progress["saved_at"].startswith("2026-11-12T10:15:00")
    assert progress["resumable"] is True
    assert progress["verify_required"] is True
    # The failing operation is unknown and the last save succeeded: the next 500 are uncertain.
    assert progress["by_kind"] == [
        {"kind": "BuiltinTag", "committed": 6000, "uncertain": 500, "not_reached": 0},
        {"kind": "BuiltinStatus", "committed": 0, "uncertain": 0, "not_reached": 3500},
    ]
    assert body["resumes"] == []

    _verified(service_api, run_id)
    after = client.get(f"/runs/{run_id}", headers={"Authorization": f"Bearer {OWNER_TOKEN}"}).json()
    assert after["apply_progress"]["verify_required"] is False
    assert after["apply_progress"]["resumable"] is True


def test_a_run_that_never_started_a_write_has_no_progress_view(service_api: _API) -> None:
    client, projection, _orchestration = service_api
    run_id = _create(client).json()["run"]["run_id"]
    _publish_plan(projection, run_id, total=3)
    _settle_open_executions(projection, run_id)

    body = client.get(f"/runs/{run_id}", headers={"Authorization": f"Bearer {OWNER_TOKEN}"}).json()

    assert body["apply_progress"] is None
    assert body["resumes"] == []


@pytest.mark.parametrize("reason", ["resume after the worker restart", None], ids=["with-reason", "without-reason"])
def test_an_accepted_resume_adds_an_execution_and_records_actor_time_and_reason(
    service_api: _API, reason: str | None
) -> None:
    """Scenario 4, and FR-009: a new `resume` execution on the same run, starting after the marker."""
    client, projection, orchestration = service_api
    run_id = _interrupted_run(service_api)
    _verified(service_api, run_id)
    before = datetime.now(timezone.utc)

    response = _resume(client, run_id, reason=reason) if reason is not None else _resume(client, run_id)

    assert response.status_code == 202, response.json()
    body = response.json()
    assert body["run"]["run_id"] == run_id
    assert [entry["purpose"] for entry in body["orchestration"]] == ["plan", "apply", "verify", "resume"]
    assert body["apply_progress"]["position"] == _MARKER
    [resume] = body["resumes"]
    assert resume["actor"] == "owner"
    assert resume["reason"] == reason
    assert resume["start_after"] == _MARKER
    assert resume["outcome"] is None
    assert datetime.fromisoformat(resume["requested_at"]) >= before - timedelta(seconds=1)
    [parameters] = _resume_submissions(orchestration)
    assert parameters["run_id"] == run_id
    assert parameters["expected_checksum"] == _CHECKSUM
    assert parameters["confirm_writes"] is True
    assert "confirm_left_out" not in parameters
    assert _resume_outcomes(projection, run_id) == ["accepted"]
    events = [event for event in projection.audit_events(run_id) if event.operation == "resume"]
    assert [event.reason for event in events] == [reason]
    # The same run: no run was created by the resume.
    assert projection.lookup_run(run_id).value is not None


def test_a_resume_with_another_checksum_is_refused_and_names_both_checksums(service_api: _API) -> None:
    """Scenario 3."""
    client, projection, orchestration = service_api
    run_id = _interrupted_run(service_api)
    _verified(service_api, run_id)

    response = _resume(client, run_id, expected_checksum=_OTHER_CHECKSUM)

    assert response.status_code == 409
    error = response.json()["error"]
    assert error["code"] == "checksum-conflict"
    assert _OTHER_CHECKSUM in error["message"]
    assert _CHECKSUM in error["message"]
    assert _resume_submissions(orchestration) == []
    assert _resume_outcomes(projection, run_id) == ["refused-checksum"]


def test_a_resume_without_a_passed_verify_is_refused_with_verify_required(service_api: _API) -> None:
    client, projection, orchestration = service_api
    run_id = _interrupted_run(service_api)

    response = _resume(client, run_id)

    assert response.status_code == 409
    assert _error_code(response) == "verify-required"
    assert _resume_submissions(orchestration) == []
    assert _resume_outcomes(projection, run_id) == ["refused-verify-required"]
    # Replaying the key replays the refusal, and records the replay.
    replay = _resume(client, run_id)
    assert replay.status_code == 409
    assert replay.json() == response.json()
    assert _resume_outcomes(projection, run_id) == ["refused-verify-required", "refused-verify-required"]


def test_a_resume_of_a_run_that_was_never_interrupted_is_refused_with_run_not_resumable(service_api: _API) -> None:
    client, projection, _orchestration = service_api
    run_id = _create(client).json()["run"]["run_id"]
    _publish_plan(projection, run_id, total=3)
    _settle_open_executions(projection, run_id)

    response = _resume(client, run_id)

    assert response.status_code == 409
    assert _error_code(response) == "run-not-resumable"
    assert "no interrupted write" in response.json()["error"]["message"]
    assert _resume_outcomes(projection, run_id) == ["refused-run-not-resumable"]


def test_a_resume_of_a_cancelled_run_is_refused_with_run_not_resumable(service_api: _API) -> None:
    client, projection, orchestration = service_api
    version = client.app.state.run_binding
    run_id = "20261112T0900-0badc0de"
    projection.create_run(
        ProductRun(
            run_id=run_id,
            operation="plan",
            configuration_reference=f"{version.config_id}@{version.registry_version}",
            config_id=version.config_id,
            registry_version=version.registry_version,
            package_checksum=version.package_checksum,
            actor="owner",
            started_at=datetime.now(timezone.utc),
            phase="cancelled",
        )
    )

    response = _resume(client, run_id)

    assert response.status_code == 409
    assert _error_code(response) == "run-not-resumable"
    assert "cancelled" in response.json()["error"]["message"]
    assert _resume_submissions(orchestration) == []


def test_a_second_resume_while_the_first_is_unfinished_is_refused_with_run_execution_conflict(
    service_api: _API,
) -> None:
    client, projection, orchestration = service_api
    run_id = _interrupted_run(service_api)
    _verified(service_api, run_id)
    assert _resume(client, run_id, key="resume-1").status_code == 202

    response = _resume(client, run_id, key="resume-2")

    assert response.status_code == 409
    assert _error_code(response) == "run-execution-conflict"
    assert len(_resume_submissions(orchestration)) == 1
    assert _resume_outcomes(projection, run_id) == ["accepted", "refused-run-execution-conflict"]


def test_replaying_the_same_key_returns_the_accepted_resume_and_submits_once(service_api: _API) -> None:
    client, projection, orchestration = service_api
    run_id = _interrupted_run(service_api)
    _verified(service_api, run_id)

    first = _resume(client, run_id, reason="resume")
    second = _resume(client, run_id, reason="resume")

    assert (first.status_code, second.status_code) == (202, 202)
    assert second.json() == first.json()
    assert len(_resume_submissions(orchestration)) == 1
    assert _resume_outcomes(projection, run_id) == ["accepted", "replayed"]


def test_a_resume_without_confirm_writes_is_refused(service_api: _API) -> None:
    client, projection, orchestration = service_api
    run_id = _interrupted_run(service_api)
    _verified(service_api, run_id)

    response = _resume(client, run_id, confirm_writes=False)

    assert response.status_code == 409
    assert _error_code(response) == "confirmation-required"
    assert _resume_submissions(orchestration) == []
    assert _resume_outcomes(projection, run_id) == ["refused-confirmation"]


def test_a_resume_of_a_plan_that_leaves_out_records_needs_the_confirmation(service_api: _API) -> None:
    client, projection, orchestration = service_api
    run_id = _interrupted_run(service_api, total=20, marker=9, left_out=4)
    _verified(service_api, run_id)

    refused = _resume(client, run_id, key="resume-unconfirmed")
    accepted = _resume(client, run_id, key="resume-confirmed", confirm_left_out=True)

    assert refused.status_code == 409
    assert _error_code(refused) == "left-out-confirmation-required"
    assert refused.json()["error"]["left_out"] == 4
    assert accepted.status_code == 202, accepted.json()
    [parameters] = _resume_submissions(orchestration)
    assert parameters["confirm_left_out"] is True
    assert _resume_outcomes(projection, run_id) == ["refused-left-out-not-confirmed", "accepted"]


def test_a_site_that_requires_approval_refuses_a_resume_of_a_run_with_no_approval(
    service_api: _API, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, projection, orchestration = service_api
    run_id = _interrupted_run(service_api)
    _verified(service_api, run_id)
    monkeypatch.setenv(REQUIRE_APPROVAL_ENV, "true")

    response = _resume(client, run_id)

    assert response.status_code == 409
    assert _error_code(response) == "approval-required"
    assert _resume_submissions(orchestration) == []
    assert _resume_outcomes(projection, run_id) == ["refused-approval-required"]


def test_a_site_that_requires_approval_refuses_a_resume_of_another_checksum_than_the_approved_one(
    service_api: _API, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, projection, _orchestration = service_api
    run_id = _interrupted_run(service_api, approve=True)
    _verified(service_api, run_id)
    monkeypatch.setenv(REQUIRE_APPROVAL_ENV, "true")

    # The approval check comes before the checksum check, as for `apply`.
    refused = _resume(client, run_id, key="resume-other", expected_checksum=_OTHER_CHECKSUM)
    accepted = _resume(client, run_id, key="resume-approved")

    assert refused.status_code == 409
    assert _error_code(refused) == "approval-checksum-mismatch"
    assert accepted.status_code == 202, accepted.json()
    assert _resume_outcomes(projection, run_id) == ["refused-approval-required", "accepted"]


def test_only_the_runs_actor_or_an_administrator_may_resume_it(service_api: _API) -> None:
    client, projection, orchestration = service_api
    run_id = _interrupted_run(service_api)
    _verified(service_api, run_id)

    forbidden = _resume(client, run_id, key="other-key", token=OTHER_TOKEN)
    accepted = _resume(client, run_id, key="admin-key", token=ADMIN_TOKEN)

    assert forbidden.status_code == 403
    assert _error_code(forbidden) == "forbidden"
    assert accepted.status_code == 202, accepted.json()
    assert accepted.json()["resumes"][0]["actor"] == "admin"
    assert len(_resume_submissions(orchestration)) == 1
    assert _resume_outcomes(projection, run_id) == ["refused-authorization", "accepted"]


# --- the uncertain window (critique E3) -----------------------------------------------------


@pytest.mark.parametrize(
    ("position", "failed_index", "last_save_failed", "expected"),
    [
        # The failing operation is known: through it.
        (5_999, 6_200, False, range(6_000, 6_201)),
        # Unknown, and the last save succeeded: the next 500.
        (5_999, None, False, range(6_000, 6_500)),
        # Unknown, and the last save failed: every remaining operation.
        (5_999, None, True, range(6_000, 10_000)),
        # Nothing saved: from the first operation.
        (None, None, False, range(500)),
        # Near the end of the plan the window stops at the last operation.
        (9_800, None, False, range(9_801, 10_000)),
    ],
    ids=["failing-operation-known", "last-save-succeeded", "last-save-failed", "nothing-saved", "near-the-end"],
)
def test_the_uncertain_window(
    position: int | None,
    failed_index: int | None,
    last_save_failed: bool,  # noqa: FBT001 - a parametrized case, not a flag
    expected: range,
) -> None:
    assert uncertain_window(position, _TOTAL, failed_index=failed_index, last_save_failed=last_save_failed) == expected


# --- the worker and the engine ----------------------------------------------------------------


def _receipt(run_id: str, name: str, operation: str) -> MutationReceipt:
    now = datetime.now(timezone.utc)
    return MutationReceipt(
        receipt_id=f"m-{name}",
        actor="owner",
        key_digest=sha256(f"{name}:{run_id}".encode()).hexdigest(),
        operation=operation,
        target_run_id=run_id,
        request_fingerprint=sha256(f"{operation}:{name}".encode()).hexdigest(),
        reason=None,
        resource_id=run_id,
        run_id=run_id,
        prefect_key=sha256(f"prefect:{name}".encode()).hexdigest(),
        created_at=now,
        updated_at=now,
    )


def _start(projection: ProductProjection, run_id: str, receipt: MutationReceipt, purpose: str) -> str:
    """Append, answer and claim the receipt's execution, as the Sync API and the worker do."""
    flow_run_id = f"flow-{receipt.receipt_id}"
    projection.add_prefect_execution(
        run_id,
        PrefectExecutionLink(flow_run_id=flow_run_id, purpose=purpose, attempt=1),
        receipt_id=receipt.receipt_id,
        allocate_attempt=True,
    )
    projection.complete_mutation(
        receipt.receipt_id, response_status=202, response_body={"accepted": True}, flow_run_id=flow_run_id
    )
    assert projection.claim_execution(run_id, flow_run_id, worker_id=_WORKER_ID)
    return flow_run_id


def _store_run(tmp_path: Path, run_id: str) -> ProductProjection:
    projection = local_product_projection(tmp_path / "store")
    version = projection.create_configuration(package())
    projection.create_run(
        ProductRun(
            run_id=run_id,
            operation="plan",
            configuration_reference=f"{version.config_id}@{version.registry_version}",
            config_id=version.config_id,
            registry_version=version.registry_version,
            package_checksum=version.package_checksum,
            actor="owner",
            started_at=datetime.now(timezone.utc),
            phase="accepted",
            summary={"sync_name": "inventory"},
        )
    )
    return projection


def _admit_apply(projection: ProductProjection, run_id: str) -> str:
    reserved, _created = projection.reserve_mutation(_receipt(run_id, "apply", "apply"), admit_write=True)
    assert reserved.state == "reserved"
    return _start(projection, run_id, reserved, "apply")


def _pass_verify(projection: ProductProjection, run_id: str) -> None:
    reserved, _created = projection.reserve_mutation(_receipt(run_id, "verify", "verify"))
    assert reserved.state == "reserved"
    flow_run_id = _start(projection, run_id, reserved, "verify")
    assert projection.commit_claimed_execution(
        run_id,
        flow_run_id,
        worker_id=_WORKER_ID,
        terminal_at=datetime.now(timezone.utc),
        terminal_state="completed",
        terminal_outcome="succeeded",
        writeback=ExecutionMergeWriteback(results={"verification": {"outcome": "verified"}}),
    )


def _admit_resume(projection: ProductProjection, run_id: str, name: str = "resume") -> str:
    reserved, _created = projection.reserve_resume(run_id, _receipt(run_id, name, "resume"))
    assert reserved.state == "reserved", reserved.response_body
    return _start(projection, run_id, reserved, "resume")


class _KillableDestination:
    """A destination that writes each operation, and whose worker is stopped at one position.

    `written` holds every dispatch, so a repeated write shows twice. At `stop_at` the liveness
    monitor ends the execution first, then the worker stops: the late marker save is refused.
    """

    def __init__(self, *, stop_at: int | None = None, on_stop: Callable[[], None] | None = None) -> None:
        self.written: list[str] = []
        self._stop_at = stop_at
        self._on_stop = on_stop

    def new_peer_resolver(self) -> object:  # noqa: PLR6301
        return object()

    def apply_planned_operation(self, *, operation: PlannedOperation, peers: object) -> str:
        del peers
        # The plan holds no delete, so the number of dispatches so far is the operation's position.
        if len(self.written) == self._stop_at:
            assert self._on_stop is not None
            self._on_stop()
            raise KeyboardInterrupt
        self.written.append(operation.operation_id)
        return "node"


class _Ownership:
    def before_operation(self) -> None:
        pass

    def after_final_operation(self) -> None:
        pass


def _write_plan(run_directory: Path, run_id: str) -> list[str]:
    records = [operation_record(kind=_kind(index), identity={"name": f"node-{index}"}) for index in range(_TOTAL)]
    write_artifact(run_directory, records, run_id=run_id, source_snapshot=[])
    return [str(record["operation_id"]) for record in records]


def _engine(run_directory: Path, run_id: str, destination: object) -> Potenda:
    return Potenda(
        source=SimpleNamespace(top_level=[]),  # ty: ignore[invalid-argument-type]
        destination=destination,  # ty: ignore[invalid-argument-type]
        config=None,  # ty: ignore[invalid-argument-type]
        top_level=["BuiltinTag"],
        run_dir=run_directory,
        run_id=run_id,
    )


def test_an_interrupted_apply_resumes_after_its_marker_and_the_run_ends_applied(tmp_path: Path) -> None:  # noqa: PLR0914 - one scenario, end to end
    """Scenario 1, against the store and the engine with a fake destination.

    The apply saves its marker every 500 commits, so the last saved position is 5,999. The
    worker stops at 6,200: operations 6,000 to 6,199 are written but not saved. The resume
    starts after 5,999, so it writes 6,000 to 9,999 once more or for the first time, and the
    destination ends with every operation of the plan.
    """
    run_id = "20261112T1000-aa11bb22"
    run_directory = tmp_path / "plan" / run_id
    run_directory.mkdir(parents=True)
    plan_ids = _write_plan(run_directory, run_id)
    projection = _store_run(tmp_path, run_id)
    apply_flow_run_id = _admit_apply(projection, run_id)

    def stop_worker() -> None:
        assert projection.interrupt_execution(run_id, apply_flow_run_id)

    first = _KillableDestination(stop_at=_STOPPED_AT, on_stop=stop_worker)
    with pytest.raises(KeyboardInterrupt):
        _engine(run_directory, run_id, first).apply_plan(
            config_version=CONFIG_VERSION,
            ownership=_Ownership(),
            progress=StoreApplyProgressObserver(projection, run_id, apply_flow_run_id, total=_TOTAL, clock=lambda: 0.0),
        )
    assert len(first.written) == _STOPPED_AT
    interrupted = projection.lookup_run(run_id).value
    assert interrupted is not None
    assert interrupted.reconciliation_required is True
    assert interrupted.prefect_executions[-1].apply_progress is not None
    assert interrupted.prefect_executions[-1].apply_progress.position == _MARKER
    operations = [(operation_id, _kind(index)) for index, operation_id in enumerate(plan_ids)]
    view = apply_progress_view(interrupted, operations)
    assert view is not None
    assert [(entry.kind, entry.committed, entry.uncertain, entry.not_reached) for entry in view.by_kind] == [
        ("BuiltinTag", 6000, 500, 0),
        ("BuiltinStatus", 0, 0, 3500),
    ]

    _pass_verify(projection, run_id)
    resume_flow_run_id = _admit_resume(projection, run_id)
    start_after = resume_start_after(projection, run_id)
    assert start_after == _MARKER
    second = _KillableDestination()
    engine = _engine(run_directory, run_id, second)
    record = engine.apply_plan(
        config_version=CONFIG_VERSION,
        ownership=_Ownership(),
        progress=StoreApplyProgressObserver(
            projection, run_id, resume_flow_run_id, total=_TOTAL, start_after=start_after, clock=lambda: 0.0
        ),
        start_after=start_after,
    )
    assert projection.commit_claimed_execution(
        run_id,
        resume_flow_run_id,
        worker_id=_WORKER_ID,
        terminal_at=datetime.now(timezone.utc),
        terminal_state="completed",
        terminal_outcome="succeeded",
        writeback=ExecutionFinishWriteback(
            phase="applied", outcome="applied", finished_at=datetime.now(timezone.utc), summary={}, results={}
        ),
    )

    # The resume wrote operations 6,001 to 10,000 (positions 6,000 to 9,999) and nothing before.
    assert second.written == plan_ids[_MARKER + 1 :]
    assert record.applied_operations == tuple(plan_ids[_MARKER + 1 :])
    # Its counts cover only the operations it dispatched (research R6).
    assert engine.last_applied_plan_action_counts == {"create": _TOTAL - _MARKER - 1, "update": 0, "delete": 0}
    # Every operation of the plan reached the destination; 6,000 to 6,199 were written twice.
    assert set(first.written) | set(second.written) == set(plan_ids)
    applied = projection.lookup_run(run_id).value
    assert applied is not None
    assert (applied.phase, applied.reconciliation_required) == ("applied", False)
    assert [link.purpose for link in applied.prefect_executions] == ["apply", "verify", "resume"]
    resume_marker = applied.prefect_executions[-1].apply_progress
    assert resume_marker is not None
    assert (resume_marker.start_after, resume_marker.position) == (_MARKER, _TOTAL - 1)
    final_view = apply_progress_view(applied, operations)
    assert final_view is not None
    assert final_view.resumable is False
    assert [(entry.committed, entry.uncertain, entry.not_reached) for entry in final_view.by_kind] == [
        (6500, 0, 0),
        (3500, 0, 0),
    ]


def test_a_destination_not_found_on_an_id_keyed_update_stops_the_resume_and_names_the_operation(
    tmp_path: Path,
) -> None:
    """The node an update names was removed meanwhile: the resume stops at that operation."""
    run_id = "20261112T1100-cc33dd44"
    run_directory = tmp_path / run_id
    run_directory.mkdir()
    records = [
        operation_record(identity={"name": "alpha"}),
        operation_record(action="update", identity={"name": "bravo"}),
        operation_record(identity={"name": "charlie"}),
    ]
    write_artifact(run_directory, records, run_id=run_id, source_snapshot=[])
    bravo = str(records[1]["operation_id"])

    class _Removed:
        def new_peer_resolver(self) -> object:  # noqa: PLR6301
            return object()

        def apply_planned_operation(self, *, operation: PlannedOperation, peers: object) -> NoReturn:  # noqa: PLR6301
            del peers
            msg = "node not found"
            assert operation.operation_id == bravo
            raise StaleDestinationIdError(msg)

    with pytest.raises(OperationApplyFailedError) as raised:
        _engine(run_directory, run_id, _Removed()).apply_plan(
            config_version=CONFIG_VERSION, ownership=_Ownership(), start_after=0
        )

    assert bravo in str(raised.value)
    assert raised.value.apply_record.failed_operation == bravo
    assert raised.value.apply_record.failed_operation_wrote is False
    assert raised.value.apply_record.applied_operations == ()


# --- the `resume` stage of the service worker -----------------------------------------------


def _instance(configuration: Any, *, directory: str, resolve_source_credentials: bool = True) -> SimpleNamespace:  # noqa: ANN401
    del directory
    # A resume never reads the source, so it never resolves a source credential (FR-010).
    assert resolve_source_credentials is False
    return SimpleNamespace(name=configuration.configuration.name)


@pytest.fixture
def resume_worker(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> tuple[ProductProjection, str, str]:
    """A verified interrupted run whose admitted resume this worker has claimed."""
    run_id = "20261112T1200-ee55ff66"
    projection = _store_run(tmp_path, run_id)
    apply_flow_run_id = _admit_apply(projection, run_id)
    assert projection.record_apply_progress(
        run_id,
        apply_flow_run_id,
        ApplyProgress(
            position=_MARKER, operation_id=_operation_id(_MARKER), total=_TOTAL, saved_at=datetime.now(timezone.utc)
        ),
    )
    assert projection.interrupt_execution(run_id, apply_flow_run_id)
    _pass_verify(projection, run_id)
    resume_flow_run_id = _admit_resume(projection, run_id)
    monkeypatch.setattr(service_flow, "_runtime", lambda: (str(tmp_path), projection))
    monkeypatch.setattr(service_flow, "_claim_current_execution", lambda *_args: (resume_flow_run_id, _WORKER_ID))
    bind_granting_guard(monkeypatch, service_flow)
    monkeypatch.setattr(service_flow, "_run_logger", lambda: (logging.getLogger("test-resume"), False))
    monkeypatch.setattr(service_flow, "resolve_runtime_instance", _instance)
    monkeypatch.setattr(service_flow, "collect_secret_values", lambda _instance=None: ())
    monkeypatch.setattr(service_flow, "build_runtime_model_plan", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(service_flow, "rehydrate_plan_checkpoint", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(service_flow, "publish_final_checkpoint", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        service_flow, "_verify_registered_apply", lambda **_kwargs: SimpleNamespace(operations_count=_TOTAL)
    )
    monkeypatch.setattr(service_flow, "_require_planned_schema", lambda **_kwargs: None)
    monkeypatch.setattr(service_flow, "_baseline_writeback", lambda _manifest: None)

    def no_plan_stage(*_args: object, **_kwargs: object) -> NoReturn:
        pytest.fail("a resume ran the plan stage")

    monkeypatch.setattr(service_flow, "_plan", no_plan_stage)
    return projection, run_id, resume_flow_run_id


def _run_resume(projection: ProductProjection, run_id: str) -> object:
    binding = projection.lookup_run(run_id).value
    assert binding is not None
    assert binding.configuration_binding is not None
    return service_sync_run.fn(
        run_id, "resume", *binding.configuration_binding, expected_checksum=_CHECKSUM, confirm_writes=True
    )


def test_the_resume_stage_applies_from_the_runs_position_and_settles_the_run(
    resume_worker: tuple[ProductProjection, str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """FR-010, FR-015, FR-016: the saved plan from the marker, its own marker, and the run applied."""
    projection, run_id, resume_flow_run_id = resume_worker
    calls: list[dict[str, Any]] = []

    def apply(instance: object, **kwargs: Any) -> RunResult:  # noqa: ANN401
        del instance
        calls.append(kwargs)
        return RunResult(
            sync_name="inventory",
            operation="apply",
            run_id=run_id,
            status="applied",
            changed=True,
            summary={"create": _TOTAL - _MARKER - 1, "update": 0, "delete": 0},
            artifact_path=str(Path(__file__).parent / run_id),
        )

    monkeypatch.setattr(service_flow, "execute_run", apply)

    result = _run_resume(projection, run_id)

    [call] = calls
    assert call["operation"] == "apply"
    assert call["expected_checksum"] == _CHECKSUM
    assert call["_start_after"] == _MARKER
    observer = call["_apply_progress"]
    assert isinstance(observer, StoreApplyProgressObserver)
    assert observer._start_after == _MARKER
    assert isinstance(result, dict)
    stage = cast("dict[str, Any]", result)
    assert (stage["operation"], stage["outcome"], stage["start_after"]) == ("resume", "applied", _MARKER)
    stored = projection.lookup_run(run_id).value
    assert stored is not None
    assert (stored.phase, stored.outcome, stored.reconciliation_required) == ("applied", "applied", False)
    link = stored.prefect_executions[-1]
    assert (link.flow_run_id, link.terminal_state, link.terminal_outcome) == (
        resume_flow_run_id,
        "completed",
        "succeeded",
    )
    assert stored.summary["create"] == _TOTAL - _MARKER - 1
    assert [event.event_type for event in projection.audit_events(run_id) if event.operation == "resume"] == [
        "infrahub_sync.run.resumed"
    ]


def test_progress_lost_during_a_resume_takes_the_adr_0010_exit_and_keeps_the_run_resumable(
    resume_worker: tuple[ProductProjection, str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """`ApplyProgressLostError` after a dispatch ends the resume interrupted, never succeeded."""
    projection, run_id, _resume_flow_run_id = resume_worker
    partial = ApplyRecord(applied_operations=(_operation_id(_MARKER + 1),), operations_total=_TOTAL)

    def lose_progress(_instance: object, **kwargs: Any) -> NoReturn:  # noqa: ANN401
        kwargs["ownership"].before_operation()
        msg = "the progress of the apply could not be saved"
        raise ApplyProgressLostError(msg, apply_record=partial)

    monkeypatch.setattr(service_flow, "execute_run", lose_progress)

    # The flow raises the sanitized copy of a failure it records without a known cause.
    with pytest.raises(RuntimeError, match="ApplyProgressLostError"):
        _run_resume(projection, run_id)

    stored = projection.lookup_run(run_id).value
    assert stored is not None
    assert (stored.phase, stored.outcome, stored.reconciliation_required) == ("resume-interrupted", "ambiguous", True)
    assert stored.results["resume_failure"]["error_type"] == "ApplyProgressLostError"
    assert stored.prefect_executions[-1].terminal_state == "interrupted"
    # The resume saved no marker of its own, so its last save is not known to have succeeded:
    # every operation after the run's position is uncertain (critique E3).
    view = apply_progress_view(stored, _plan_operations())
    assert view is not None
    assert (view.position, view.resumable, view.verify_required) == (_MARKER, True, True)
    assert [(entry.kind, entry.committed, entry.uncertain, entry.not_reached) for entry in view.by_kind] == [
        ("BuiltinTag", 6000, 500, 0),
        ("BuiltinStatus", 0, 3500, 0),
    ]


def test_a_failed_resume_names_the_operation_and_the_window_ends_with_it(
    resume_worker: tuple[ProductProjection, str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A destination "not found" stops the resume; the record names the operation it stopped at."""
    projection, run_id, _resume_flow_run_id = resume_worker
    failed = _operation_id(6_100)
    partial = ApplyRecord(
        applied_operations=tuple(_operation_id(index) for index in range(_MARKER + 1, 6_100)),
        failed_operation=failed,
        # What the engine records for a destination "not found": this operation wrote nothing.
        failed_operation_wrote=False,
        operations_total=_TOTAL,
        failed_operation_detail={"action": "update", "kind": "BuiltinTag", "identity": {"name": "node-6100"}},
    )

    def not_found(_instance: object, **kwargs: Any) -> NoReturn:  # noqa: ANN401
        kwargs["ownership"].before_operation()
        msg = f"Applying operation {failed!r} failed"
        raise OperationApplyFailedError(msg, apply_record=partial, next_action=StaleDestinationIdError.next_action)

    monkeypatch.setattr(service_flow, "execute_run", not_found)

    with pytest.raises(RuntimeError, match="OperationApplyFailedError"):
        _run_resume(projection, run_id)

    stored = projection.lookup_run(run_id).value
    assert stored is not None
    assert stored.results["resume_failure"]["failed_operation"] == failed
    # The failing operation wrote nothing, so the resume failed rather than ended ambiguous; the
    # run's earlier write is still uncertain, so it still requires reconciliation and a new
    # `verify` before the next resume.
    assert (stored.phase, stored.outcome, stored.reconciliation_required) == ("resume-failed", "failed", True)
    assert stored.prefect_executions[-1].terminal_state == "failed"
    view = apply_progress_view(stored, _plan_operations())
    assert view is not None
    assert (view.resumable, view.verify_required) == (True, True)
    # Uncertain: after the run's position, through the failing operation (6,000 to 6,100).
    assert [(entry.kind, entry.committed, entry.uncertain, entry.not_reached) for entry in view.by_kind] == [
        ("BuiltinTag", 6000, 101, 399),
        ("BuiltinStatus", 0, 0, 3500),
    ]
