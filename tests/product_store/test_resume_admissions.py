"""Admissions on an interrupted run: `verify`, then `resume` (spec 022).

Research R5 and critique E1: a run that admitted its one write refuses every later stage, so
the `verify` that a resume requires would be refused on an interrupted run. The store admits
one exception: a `verify` on a resumable run, which is a run whose write was interrupted
(`reconciliation_required`), that is not cancelled, and that has no unfinished execution
(data-model.md "State rules").

The same cases run against a real PostgreSQL server when `PRODUCT_STORE_TEST_POSTGRESQL_DSN`
names one; see `tests/product_store/postgresql_isolation`.

A `resume` is admitted through `reserve_resume` (research R5): the run must be resumable, its
latest `verify` must come after its latest write and have succeeded, and no other resume may be
unfinished. A resume that succeeds clears `reconciliation_required` (research R8); one that is
interrupted keeps the run resumable and requires a new `verify`. A `verify` that is cancelled or
fails leaves the run resumable.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from threading import Barrier
from typing import TYPE_CHECKING, Any
from uuid import uuid4

import pytest

from infrahub_sync.product_store import (
    ApplyProgress,
    ExecutionFinishWriteback,
    ExecutionMergeWriteback,
    MutationReceipt,
    PrefectExecutionLink,
    ProductProjection,
    ProductRun,
    run_apply_progress,
)
from infrahub_sync.product_store.models import RunApproval
from infrahub_sync.product_store.store import FileArtifactStore, PostgreSQLRunStore, SQLiteRunStore
from tests.product_store.postgresql_isolation import postgresql_schema_fixture

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

_SESSION = uuid4().hex[:10]
_CONFLICT_CODE = "run-execution-conflict"
_WORKER_ID = "3d6c1f2a-8b4e-4c7d-9e0f-1a2b3c4d5e6f"
_STARTED_AT = datetime(2026, 11, 12, 9, tzinfo=timezone.utc)

_postgresql_schema = postgresql_schema_fixture("the resume admissions' PostgreSQL parameter")


@pytest.fixture(params=("sqlite", pytest.param("postgresql", marks=pytest.mark.integration)))
def projection(request: pytest.FixtureRequest, tmp_path: Path) -> Iterator[ProductProjection]:
    """One product projection per provider profile, isolated per test."""
    artifacts = FileArtifactStore(tmp_path / "objects")
    if request.param == "sqlite":
        yield ProductProjection(SQLiteRunStore(tmp_path / "records.sqlite3"), artifacts)
        return
    schema = request.getfixturevalue("_postgresql_schema")
    # pylint: disable-next=import-outside-toplevel
    import psycopg  # ty: ignore[unresolved-import] - TODO: optional service dependency

    from infrahub_sync.service.storage import PsycopgConnectionFactory

    def connect() -> Any:  # noqa: ANN401 - the store's own DB-API connection protocol.
        return PsycopgConnectionFactory(psycopg.connect)(schema.dsn)

    yield ProductProjection(PostgreSQLRunStore(connect), artifacts)


def _rid(name: str) -> str:
    return f"{name}-{_SESSION}"


def _receipt(name: str, *, run_id: str, operation: str) -> MutationReceipt:
    return MutationReceipt(
        receipt_id=f"{name}.{run_id}",
        actor="operator@example.com",
        key_digest=sha256(f"{name}:{run_id}".encode()).hexdigest(),
        operation=operation,
        target_run_id=run_id,
        request_fingerprint=sha256(f"{operation}:{name}:{run_id}".encode()).hexdigest(),
        reason="resume admission",
        resource_id=run_id,
        run_id=run_id,
        prefect_key=sha256(f"prefect:{name}:{run_id}".encode()).hexdigest(),
        created_at=_STARTED_AT,
        updated_at=_STARTED_AT,
    )


def _link(flow_run_id: str, *, purpose: str) -> PrefectExecutionLink:
    return PrefectExecutionLink(
        flow_run_id=flow_run_id, purpose=purpose, attempt=1, submitted_at=datetime.now(timezone.utc)
    )


def _refusal_code(receipt: MutationReceipt) -> str | None:
    body = receipt.response_body
    if body is None:
        return None
    error = body.get("error")
    return error.get("code") if isinstance(error, dict) else None


def _reserve(projection: ProductProjection, run_id: str, name: str, *, operation: str) -> MutationReceipt:
    reserved, _created = projection.reserve_mutation(
        _receipt(name, run_id=run_id, operation=operation), admit_write=operation in {"apply", "sync"}
    )
    return reserved


def _submit(projection: ProductProjection, run_id: str, receipt: MutationReceipt, *, purpose: str) -> str:
    """Append and claim the receipt's execution, as the service and the worker do."""
    flow_run_id = f"flow-{receipt.receipt_id}"
    # The store allocates the attempt, so a second verify or resume of one run gets its own.
    projection.add_prefect_execution(
        run_id, _link(flow_run_id, purpose=purpose), receipt_id=receipt.receipt_id, allocate_attempt=True
    )
    projection.complete_mutation(
        receipt.receipt_id, response_status=202, response_body={"accepted": True}, flow_run_id=flow_run_id
    )
    assert projection.claim_execution(run_id, flow_run_id, worker_id=_WORKER_ID)
    return flow_run_id


def _interrupted_run(projection: ProductProjection, name: str) -> str:
    """A run whose claimed apply was interrupted: it requires reconciliation and is resumable."""
    run_id = _rid(name)
    projection.create_run(
        ProductRun(
            run_id=run_id,
            operation="plan",
            configuration_reference="config-001@1",
            config_id="config-001",
            registry_version=1,
            package_checksum="a" * 64,
            actor="operator@example.com",
            started_at=_STARTED_AT,
            phase="accepted",
        )
    )
    apply_receipt = _reserve(projection, run_id, "m-apply", operation="apply")
    assert apply_receipt.state == "reserved"
    flow_run_id = _submit(projection, run_id, apply_receipt, purpose="apply")
    assert projection.interrupt_execution(run_id, flow_run_id) is True
    stored = projection.lookup_run(run_id).value
    assert stored is not None
    assert stored.reconciliation_required is True
    return run_id


def _assert_refused(receipt: MutationReceipt) -> None:
    assert receipt.state == "accepted"
    assert receipt.response_status == 409
    assert _refusal_code(receipt) == _CONFLICT_CODE


def test_an_interrupted_run_with_no_unfinished_execution_is_resumable(projection: ProductProjection) -> None:
    run_id = _interrupted_run(projection, "resumable")

    assert projection.run_is_resumable(run_id) is True


def test_verify_is_admitted_on_a_resumable_run_and_can_submit_its_execution(projection: ProductProjection) -> None:
    run_id = _interrupted_run(projection, "verify-admitted")

    verify = _reserve(projection, run_id, "m-verify", operation="verify")

    assert verify.state == "reserved"
    assert verify.response_status is None
    flow_run_id = _submit(projection, run_id, verify, purpose="verify")
    stored = projection.lookup_run(run_id).value
    assert stored is not None
    assert [link.purpose for link in stored.prefect_executions] == ["apply", "verify"]
    assert stored.prefect_executions[-1].flow_run_id == flow_run_id


def test_a_second_verify_is_refused_while_the_first_is_reserved(projection: ProductProjection) -> None:
    run_id = _interrupted_run(projection, "verify-reserved-twice")
    assert _reserve(projection, run_id, "m-verify-1", operation="verify").state == "reserved"

    _assert_refused(_reserve(projection, run_id, "m-verify-2", operation="verify"))


def test_a_second_verify_is_refused_while_the_first_execution_is_unfinished(projection: ProductProjection) -> None:
    run_id = _interrupted_run(projection, "verify-running")
    _submit(projection, run_id, _reserve(projection, run_id, "m-verify-1", operation="verify"), purpose="verify")

    assert projection.run_is_resumable(run_id) is False
    _assert_refused(_reserve(projection, run_id, "m-verify-2", operation="verify"))


@pytest.mark.parametrize("operation", ["plan", "apply", "sync"])
def test_every_other_stage_is_still_refused_on_a_resumable_run(projection: ProductProjection, operation: str) -> None:
    run_id = _interrupted_run(projection, f"other-stage-{operation}")

    _assert_refused(_reserve(projection, run_id, f"m-later-{operation}", operation=operation))


def test_approve_is_accepted_on_a_resumable_run(projection: ProductProjection) -> None:
    """Spec 022 FR-019: a run whose write stopped part-way can be approved, so a site that requires approval can resume it."""
    run_id = _interrupted_run(projection, "approve")
    approve, _created = projection.reserve_mutation(_receipt("m-approve", run_id=run_id, operation="approve"))
    approval = RunApproval.model_validate(
        {
            "approved_by": "reviewer@example.com",
            "approved_at": datetime(2026, 11, 12, 10, tzinfo=timezone(timedelta(hours=1))),
            "plan_checksum": "b" * 64,
            "reason": "CHG-42 reviewed",
        }
    )

    stored = projection.record_approval(run_id, approval, approve, {"run": {"run_id": run_id}})

    assert stored is True
    answered = projection.lookup_mutation(approve.actor, approve.key_digest).value
    assert answered is not None
    assert answered.response_status == 200
    run = projection.lookup_run(run_id).value
    assert run is not None
    assert run.approval is not None
    assert run.approval.plan_checksum == "b" * 64


def test_verify_is_still_refused_on_an_applied_run(projection: ProductProjection) -> None:
    run_id = _rid("applied")
    projection.create_run(
        ProductRun(
            run_id=run_id,
            operation="plan",
            configuration_reference="config-001@1",
            config_id="config-001",
            registry_version=1,
            package_checksum="a" * 64,
            actor="operator@example.com",
            started_at=_STARTED_AT,
            phase="accepted",
        )
    )
    flow_run_id = _submit(
        projection, run_id, _reserve(projection, run_id, "m-apply", operation="apply"), purpose="apply"
    )
    finished_at = datetime.now(timezone.utc)
    assert projection.commit_claimed_execution(
        run_id,
        flow_run_id,
        worker_id=_WORKER_ID,
        terminal_at=finished_at,
        terminal_state="completed",
        terminal_outcome="succeeded",
        writeback=ExecutionFinishWriteback(
            phase="applied", outcome="succeeded", finished_at=finished_at, summary={}, results={}
        ),
    )

    assert projection.run_is_resumable(run_id) is False
    _assert_refused(_reserve(projection, run_id, "m-verify", operation="verify"))


# ---------------------------------------------------------------------------------------------
# `resume` admissions (T013, T014)
# ---------------------------------------------------------------------------------------------

_NOT_RESUMABLE_CODE = "run-not-resumable"
_VERIFY_REQUIRED_CODE = "verify-required"


def _finish_claimed(
    projection: ProductProjection,
    run_id: str,
    flow_run_id: str,
    *,
    verdict: tuple[str, str],
) -> None:
    """Commit a worker verdict with a merge writeback, which never changes the run's phase."""
    terminal_state, terminal_outcome = verdict
    assert projection.commit_claimed_execution(
        run_id,
        flow_run_id,
        worker_id=_WORKER_ID,
        terminal_at=datetime.now(timezone.utc),
        terminal_state=terminal_state,  # ty: ignore[invalid-argument-type] - the three legal verdicts.
        terminal_outcome=terminal_outcome,  # ty: ignore[invalid-argument-type] - the three legal verdicts.
        writeback=ExecutionMergeWriteback(results={"stage": "test"}),
    )


def _verify(projection: ProductProjection, run_id: str, name: str, *, passed: bool = True) -> str:
    """Run one `verify` on the run to its end: passed, or failed."""
    receipt = _reserve(projection, run_id, name, operation="verify")
    assert receipt.state == "reserved"
    flow_run_id = _submit(projection, run_id, receipt, purpose="verify")
    _finish_claimed(
        projection, run_id, flow_run_id, verdict=("completed", "succeeded") if passed else ("failed", "failed")
    )
    return flow_run_id


def _reserve_resume(projection: ProductProjection, run_id: str, name: str) -> MutationReceipt:
    reserved, _created = projection.reserve_resume(run_id, _receipt(name, run_id=run_id, operation="resume"))
    return reserved


def _verified_run(projection: ProductProjection, name: str) -> str:
    """An interrupted run with a passed `verify` after its write: a resume is admitted."""
    run_id = _interrupted_run(projection, name)
    _verify(projection, run_id, "m-verify")
    return run_id


def _assert_resume_refused(receipt: MutationReceipt, code: str) -> None:
    assert receipt.state == "accepted"
    assert receipt.response_status == 409
    assert _refusal_code(receipt) == code
    assert receipt.flow_run_id is None


def _run(projection: ProductProjection, run_id: str) -> ProductRun:
    stored = projection.lookup_run(run_id).value
    assert stored is not None
    return stored


def test_a_resume_is_admitted_after_a_passed_verify_and_can_submit_its_execution(
    projection: ProductProjection,
) -> None:
    run_id = _verified_run(projection, "resume-admitted")

    resume = _reserve_resume(projection, run_id, "m-resume")

    assert resume.state == "reserved"
    assert resume.response_status is None
    _submit(projection, run_id, resume, purpose="resume")
    assert [link.purpose for link in _run(projection, run_id).prefect_executions] == ["apply", "verify", "resume"]


def test_a_resume_keeps_the_original_write_admission(projection: ProductProjection) -> None:
    """The run's `write_admissions` row stays: a new `apply` on the run is still refused."""
    run_id = _verified_run(projection, "resume-keeps-admission")
    assert _reserve_resume(projection, run_id, "m-resume").state == "reserved"

    _assert_refused(_reserve(projection, run_id, "m-apply-again", operation="apply"))


def test_a_resume_without_a_verify_is_refused_with_verify_required(projection: ProductProjection) -> None:
    run_id = _interrupted_run(projection, "resume-no-verify")

    _assert_resume_refused(_reserve_resume(projection, run_id, "m-resume"), _VERIFY_REQUIRED_CODE)


def test_a_resume_after_a_failed_verify_is_refused_with_verify_required(projection: ProductProjection) -> None:
    run_id = _interrupted_run(projection, "resume-failed-verify")
    _verify(projection, run_id, "m-verify", passed=False)

    assert projection.run_is_resumable(run_id) is True
    assert _run(projection, run_id).reconciliation_required is True
    _assert_resume_refused(_reserve_resume(projection, run_id, "m-resume"), _VERIFY_REQUIRED_CODE)


def test_a_resume_of_a_run_without_an_interrupted_write_is_refused(projection: ProductProjection) -> None:
    run_id = _rid("resume-no-write")
    projection.create_run(
        ProductRun(
            run_id=run_id,
            operation="plan",
            configuration_reference="config-001@1",
            config_id="config-001",
            registry_version=1,
            package_checksum="a" * 64,
            actor="operator@example.com",
            started_at=_STARTED_AT,
            phase="planned",
        )
    )

    _assert_resume_refused(_reserve_resume(projection, run_id, "m-resume"), _NOT_RESUMABLE_CODE)


def test_a_resume_while_a_verify_is_unfinished_is_refused_as_not_resumable(projection: ProductProjection) -> None:
    run_id = _interrupted_run(projection, "resume-verify-running")
    _submit(projection, run_id, _reserve(projection, run_id, "m-verify", operation="verify"), purpose="verify")

    _assert_resume_refused(_reserve_resume(projection, run_id, "m-resume"), _NOT_RESUMABLE_CODE)


def test_a_resume_while_a_verify_is_reserved_is_refused_as_not_resumable(projection: ProductProjection) -> None:
    """A `verify` reserved and not yet submitted holds the run, so it cannot race the resume."""
    run_id = _verified_run(projection, "resume-verify-reserved")
    assert _reserve(projection, run_id, "m-verify-2", operation="verify").state == "reserved"

    _assert_resume_refused(_reserve_resume(projection, run_id, "m-resume"), _NOT_RESUMABLE_CODE)


def test_a_second_resume_while_the_first_is_reserved_is_refused_as_a_conflict(projection: ProductProjection) -> None:
    run_id = _verified_run(projection, "resume-reserved-twice")
    assert _reserve_resume(projection, run_id, "m-resume-1").state == "reserved"

    _assert_resume_refused(_reserve_resume(projection, run_id, "m-resume-2"), _CONFLICT_CODE)


def test_a_second_resume_while_the_first_is_running_is_refused_as_a_conflict(projection: ProductProjection) -> None:
    run_id = _verified_run(projection, "resume-running")
    _submit(projection, run_id, _reserve_resume(projection, run_id, "m-resume-1"), purpose="resume")

    _assert_resume_refused(_reserve_resume(projection, run_id, "m-resume-2"), _CONFLICT_CODE)


def test_replaying_a_refused_resume_key_replays_the_refusal(projection: ProductProjection) -> None:
    run_id = _interrupted_run(projection, "resume-replay")
    receipt = _receipt("m-resume", run_id=run_id, operation="resume")
    first, first_created = projection.reserve_resume(run_id, receipt)

    replayed, replay_created = projection.reserve_resume(run_id, receipt)

    assert (first_created, replay_created) == (True, False)
    assert replayed.response_body == first.response_body
    assert _refusal_code(replayed) == _VERIFY_REQUIRED_CODE


def test_a_resume_execution_needs_the_receipt_that_holds_its_resume_admission(projection: ProductProjection) -> None:
    run_id = _verified_run(projection, "resume-append-owner")
    verify = _reserve(projection, run_id, "m-verify-2", operation="verify")

    with pytest.raises(ValueError, match="resume admission"):
        projection.add_prefect_execution(
            run_id, _link("flow-not-a-resume", purpose="resume"), receipt_id=verify.receipt_id, allocate_attempt=True
        )


def test_exactly_one_of_two_concurrent_resumes_is_admitted(projection: ProductProjection) -> None:
    """Both callers are inside `reserve_resume` before either commits; the run's row lock decides."""
    run_id = _verified_run(projection, "resume-race")
    barrier = Barrier(2)

    def reserve(name: str) -> MutationReceipt:
        barrier.wait(timeout=30)
        return _reserve_resume(projection, run_id, name)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = [future.result() for future in [pool.submit(reserve, "m-race-a"), pool.submit(reserve, "m-race-b")]]

    winners = [receipt for receipt in results if receipt.state == "reserved"]
    losers = [receipt for receipt in results if receipt.state == "accepted"]
    assert len(winners) == 1
    assert len(losers) == 1
    _assert_resume_refused(losers[0], _CONFLICT_CODE)


def test_a_resume_that_succeeds_clears_reconciliation_and_sets_the_run_applied(projection: ProductProjection) -> None:
    run_id = _verified_run(projection, "resume-succeeds")
    flow_run_id = _submit(projection, run_id, _reserve_resume(projection, run_id, "m-resume"), purpose="resume")

    # A merge writeback never touches the phase, so `applied` below is the store's own rule.
    _finish_claimed(projection, run_id, flow_run_id, verdict=("completed", "succeeded"))

    stored = _run(projection, run_id)
    assert stored.reconciliation_required is False
    assert stored.phase == "applied"
    assert projection.run_is_resumable(run_id) is False
    _assert_resume_refused(_reserve_resume(projection, run_id, "m-resume-again"), _NOT_RESUMABLE_CODE)


def test_a_resume_that_fails_keeps_the_run_requiring_reconciliation(projection: ProductProjection) -> None:
    run_id = _verified_run(projection, "resume-fails")
    flow_run_id = _submit(projection, run_id, _reserve_resume(projection, run_id, "m-resume"), purpose="resume")

    _finish_claimed(projection, run_id, flow_run_id, verdict=("failed", "failed"))

    assert _run(projection, run_id).reconciliation_required is True
    assert projection.run_is_resumable(run_id) is True


def test_an_interrupted_resume_keeps_the_run_resumable_and_requires_a_new_verify(
    projection: ProductProjection,
) -> None:
    run_id = _verified_run(projection, "resume-interrupted")
    flow_run_id = _submit(projection, run_id, _reserve_resume(projection, run_id, "m-resume-1"), purpose="resume")

    assert projection.interrupt_execution(run_id, flow_run_id) is True

    stored = _run(projection, run_id)
    assert stored.reconciliation_required is True
    assert stored.phase == "interrupted"
    assert projection.run_is_resumable(run_id) is True
    _assert_resume_refused(_reserve_resume(projection, run_id, "m-resume-2"), _VERIFY_REQUIRED_CODE)
    _verify(projection, run_id, "m-verify-2")
    assert _reserve_resume(projection, run_id, "m-resume-3").state == "reserved"


def test_a_cancelled_verify_keeps_the_run_resumable_and_requires_a_new_verify(projection: ProductProjection) -> None:
    """The orchestrator's rule: a cancelled `verify` never makes a resumable run `cancelled`."""
    run_id = _verified_run(projection, "verify-cancelled")
    verify = _reserve(projection, run_id, "m-verify-2", operation="verify")
    flow_run_id = _submit(projection, run_id, verify, purpose="verify")
    cancel, _created = projection.reserve_mutation(_receipt("m-cancel", run_id=run_id, operation="cancel"))
    assert projection.claim_mutation(cancel.receipt_id)
    now = datetime.now(timezone.utc)
    # Executions are stored in order, so the verify's position in the run is its index.
    position = len(_run(projection, run_id).prefect_executions) - 1

    assert projection.request_execution_cancellation(
        run_id,
        flow_run_id,
        requested_at=now,
        recovery_deadline_at=now + timedelta(seconds=30),
        recovery_seconds=30,
        expected_latest_position=position,
        receipt_id=cancel.receipt_id,
    )
    assert projection.acknowledge_execution_cancellation(
        run_id,
        flow_run_id,
        acknowledged_at=now + timedelta(seconds=1),
        response_status=202,
        response_body={"run": {"run_id": run_id}, "orchestration": []},
    )
    projection.observe_prefect_execution(run_id, flow_run_id, state="cancelled")
    assert projection.cancel_execution(run_id, flow_run_id, terminal_at=now + timedelta(seconds=2))

    stored = _run(projection, run_id)
    assert stored.prefect_executions[-1].terminal_state == "cancelled"
    assert stored.phase == "interrupted"
    assert stored.reconciliation_required is True
    assert projection.run_is_resumable(run_id) is True
    _assert_resume_refused(_reserve_resume(projection, run_id, "m-resume"), _VERIFY_REQUIRED_CODE)


def _marker(position: int, *, saved_at: datetime, start_after: int | None = None) -> ApplyProgress:
    return ApplyProgress(
        position=position,
        operation_id="op_0123456789abcdef",
        total=10_000,
        start_after=start_after,
        saved_at=saved_at,
    )


def test_the_run_position_is_the_highest_saved_position_over_all_write_executions(
    projection: ProductProjection,
) -> None:
    """A resume that saved nothing, or less, never lowers the run's position (critique E2)."""
    run_id = _rid("position")
    projection.create_run(
        ProductRun(
            run_id=run_id,
            operation="plan",
            configuration_reference="config-001@1",
            config_id="config-001",
            registry_version=1,
            package_checksum="a" * 64,
            actor="operator@example.com",
            started_at=_STARTED_AT,
            phase="accepted",
        )
    )
    apply_flow = _submit(
        projection, run_id, _reserve(projection, run_id, "m-apply", operation="apply"), purpose="apply"
    )
    assert projection.record_apply_progress(run_id, apply_flow, _marker(6_000, saved_at=_STARTED_AT)) == "saved"
    assert projection.interrupt_execution(run_id, apply_flow) is True
    _verify(projection, run_id, "m-verify")
    resume_flow = _submit(projection, run_id, _reserve_resume(projection, run_id, "m-resume"), purpose="resume")

    assert run_apply_progress(_run(projection, run_id)) == _marker(6_000, saved_at=_STARTED_AT)

    later = _STARTED_AT + timedelta(minutes=5)
    assert (
        projection.record_apply_progress(run_id, resume_flow, _marker(6_500, saved_at=later, start_after=6_000))
        == "saved"
    )
    progress = run_apply_progress(_run(projection, run_id))
    assert progress is not None
    assert (progress.position, progress.saved_at) == (6_500, later)


def test_a_run_without_a_saved_marker_has_no_position(projection: ProductProjection) -> None:
    run_id = _interrupted_run(projection, "no-position")

    assert run_apply_progress(_run(projection, run_id)) is None
