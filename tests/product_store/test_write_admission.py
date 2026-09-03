"""Pre-submission admission: one run admits one write, and one receipt owns its append.

The rules under test are store rules, not service rules: whichever request wins the
run's write reservation is decided inside one store transaction, before either
competitor can submit anything to Prefect. The loser is told so durably, by a stored
refusal on its own receipt, so replaying its idempotency key replays the refusal
instead of racing again.

SQLite covers the transaction shape. The same cases run against a real PostgreSQL
server when ``PRODUCT_STORE_TEST_POSTGRESQL_DSN`` names one, because row-level
serialization between two competing reservations is a provider fact and SQLite's
single-writer lock cannot stand in for it.
"""

from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from hashlib import sha256
from threading import Barrier
from typing import TYPE_CHECKING, Any
from uuid import uuid4

import pytest

from infrahub_sync.product_store import (
    ExecutionFinishWriteback,
    ExecutionMergeWriteback,
    MutationReceipt,
    PrefectExecutionLink,
    ProductProjection,
    ProductRun,
)
from infrahub_sync.product_store.store import FileArtifactStore, PostgreSQLRunStore, SQLiteRunStore

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

_DSN_ENVIRONMENT_NAME = "PRODUCT_STORE_TEST_POSTGRESQL_DSN"
# The PostgreSQL parameter runs against a persistent disposable database, so every run
# ID and receipt identity in this module is namespaced to one test session.
_SESSION = uuid4().hex[:10]
_CONFLICT_CODE = "run-execution-conflict"
_WORKER_ID = "5f2d3a0e-2f2c-4a55-9d3f-9a2a1c6f4b71"
_STARTED_AT = datetime(2026, 9, 3, 9, tzinfo=timezone.utc)


def _postgresql_dsn_or_skip() -> str:
    """Return a reachable disposable DSN, or skip before any server is contacted."""
    dsn = os.environ.get(_DSN_ENVIRONMENT_NAME)
    if not dsn:
        pytest.skip(f"the admission contract's PostgreSQL parameter requires {_DSN_ENVIRONMENT_NAME}")
    psycopg = pytest.importorskip("psycopg")
    try:
        with psycopg.connect(dsn, connect_timeout=5) as probe:
            probe.execute("SELECT 1")
    except psycopg.Error:
        pytest.skip(f"{_DSN_ENVIRONMENT_NAME} is not reachable")
    return dsn


@pytest.fixture(params=("sqlite", pytest.param("postgresql", marks=pytest.mark.integration)))
def projection(request: pytest.FixtureRequest, tmp_path: Path) -> Iterator[ProductProjection]:
    """One product projection per provider profile, isolated per test."""
    artifacts = FileArtifactStore(tmp_path / "objects")
    if request.param == "sqlite":
        yield ProductProjection(SQLiteRunStore(tmp_path / "records.sqlite3"), artifacts)
        return
    dsn = _postgresql_dsn_or_skip()
    # pylint: disable-next=import-outside-toplevel
    import psycopg  # ty: ignore[unresolved-import] - TODO: optional service dependency

    from infrahub_sync.service.storage import PsycopgConnectionFactory

    def connect() -> Any:  # noqa: ANN401 - the store's own DB-API connection protocol.
        return PsycopgConnectionFactory(psycopg.connect)(dsn)

    yield ProductProjection(PostgreSQLRunStore(connect), artifacts)


def _rid(name: str) -> str:
    """Namespace one run ID to this test session."""
    return f"{name}-{_SESSION}"


def _run(run_id: str, *, operation: str = "plan") -> ProductRun:
    return ProductRun(
        run_id=run_id,
        operation=operation,  # ty: ignore[invalid-argument-type] - the literal is one of Operation.
        configuration_reference="config-001@1",
        config_id="config-001",
        registry_version=1,
        package_checksum="a" * 64,
        actor="operator@example.com",
        started_at=_STARTED_AT,
        phase="accepted",
    )


def _receipt(receipt_id: str, *, run_id: str, operation: str, client_key: str | None = None) -> MutationReceipt:
    key = client_key if client_key is not None else receipt_id
    return MutationReceipt(
        receipt_id=f"{receipt_id}.{run_id}",
        actor="operator@example.com",
        key_digest=sha256(f"{key}:{run_id}".encode()).hexdigest(),
        operation=operation,
        target_run_id=run_id,
        request_fingerprint=sha256(f"{operation}:{run_id}".encode()).hexdigest(),
        reason="admission contract",
        resource_id=run_id,
        run_id=run_id,
        prefect_key=sha256(f"prefect:{receipt_id}:{run_id}".encode()).hexdigest(),
        created_at=_STARTED_AT,
        updated_at=_STARTED_AT,
    )


def _link(flow_run_id: str, *, purpose: str) -> PrefectExecutionLink:
    return PrefectExecutionLink(
        flow_run_id=flow_run_id,
        purpose=purpose,
        attempt=1,
        submitted_at=_STARTED_AT,
    )


def _refusal_code(receipt: MutationReceipt) -> str | None:
    """Return the stored refusal's error code, or None when the receipt is not a refusal."""
    body = receipt.response_body
    if body is None:
        return None
    error = body.get("error")
    return error.get("code") if isinstance(error, dict) else None


def _admit_write(projection: ProductProjection, run_id: str, receipt_id: str, *, operation: str) -> MutationReceipt:
    """Reserve one write admission on an existing run and return the stored receipt."""
    reserved, _created = projection.reserve_mutation(
        _receipt(receipt_id, run_id=run_id, operation=operation),
        admit_write=True,
    )
    return reserved


def _reserve_read(projection: ProductProjection, run_id: str, receipt_id: str, *, operation: str) -> MutationReceipt:
    reserved, _created = projection.reserve_mutation(_receipt(receipt_id, run_id=run_id, operation=operation))
    return reserved


def test_exactly_one_of_two_concurrent_applies_wins_the_run_write_reservation(
    projection: ProductProjection,
) -> None:
    """Two applies that start together decide at the store, before either submits.

    The barrier is what discriminates: both callers are inside ``reserve_mutation``
    before either can commit, so a reservation that read the run's records without
    serializing on its authoritative row would let both win and both submit.
    """
    projection.create_run(_run(_rid("race-apply-apply")))
    barrier = Barrier(2)

    def reserve(receipt_id: str) -> MutationReceipt:
        barrier.wait(timeout=30)
        reserved, _created = projection.reserve_mutation(
            _receipt(receipt_id, run_id=_rid("race-apply-apply"), operation="apply"),
            admit_write=True,
        )
        return reserved

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = [future.result() for future in [pool.submit(reserve, "m-race-a"), pool.submit(reserve, "m-race-b")]]

    winners = [receipt for receipt in results if receipt.state == "reserved"]
    losers = [receipt for receipt in results if receipt.state == "accepted"]
    assert len(winners) == 1
    assert len(losers) == 1
    assert losers[0].response_status == 409
    assert _refusal_code(losers[0]) == _CONFLICT_CODE
    assert losers[0].flow_run_id is None


def test_an_apply_loses_to_an_unresolved_verify_receipt(projection: ProductProjection) -> None:
    """A verify already reserved and not yet answered blocks the write reservation.

    The verify receipt carries no execution yet, so a rule that only looked for a
    non-terminal execution would admit the write and race the verify's submission.
    """
    projection.create_run(_run(_rid("apply-versus-verify")))
    _reserve_read(projection, _rid("apply-versus-verify"), "m-verify", operation="verify")

    refused = _admit_write(projection, _rid("apply-versus-verify"), "m-apply", operation="apply")

    assert refused.state == "accepted"
    assert refused.response_status == 409
    assert _refusal_code(refused) == _CONFLICT_CODE


def test_an_apply_loses_to_a_nonterminal_execution(projection: ProductProjection) -> None:
    """A still-running read execution blocks the write reservation."""
    projection.create_run(_run(_rid("apply-versus-execution")))
    verify = _reserve_read(projection, _rid("apply-versus-execution"), "m-verify", operation="verify")
    projection.add_prefect_execution(
        _rid("apply-versus-execution"),
        _link("flow-verify", purpose="verify"),
        receipt_id=verify.receipt_id,
    )
    projection.complete_mutation(
        verify.receipt_id,
        response_status=202,
        response_body={"accepted": True},
        flow_run_id="flow-verify",
    )

    refused = _admit_write(projection, _rid("apply-versus-execution"), "m-apply", operation="apply")

    assert refused.state == "accepted"
    assert _refusal_code(refused) == _CONFLICT_CODE


def test_an_apply_wins_once_the_verify_is_resolved_and_terminal(projection: ProductProjection) -> None:
    """The negative cases above must not be satisfied by refusing every apply."""
    projection.create_run(_run(_rid("apply-after-verify")))
    verify = _reserve_read(projection, _rid("apply-after-verify"), "m-verify", operation="verify")
    projection.add_prefect_execution(
        _rid("apply-after-verify"),
        _link("flow-verify", purpose="verify"),
        receipt_id=verify.receipt_id,
    )
    projection.complete_mutation(
        verify.receipt_id,
        response_status=202,
        response_body={"accepted": True},
        flow_run_id="flow-verify",
    )
    projection.claim_execution(_rid("apply-after-verify"), "flow-verify", worker_id=_WORKER_ID)
    projection.commit_claimed_execution(
        _rid("apply-after-verify"),
        "flow-verify",
        worker_id=_WORKER_ID,
        terminal_at=datetime.now(timezone.utc),
        terminal_state="completed",
        terminal_outcome="succeeded",
        writeback=ExecutionMergeWriteback(results={"verification": {"outcome": "verified"}}),
    )

    admitted = _admit_write(projection, _rid("apply-after-verify"), "m-apply", operation="apply")

    assert admitted.state == "reserved"
    assert admitted.response_status is None


@pytest.mark.parametrize("operation", ["plan", "verify", "apply", "sync"])
def test_every_later_reservation_refuses_once_a_write_is_admitted(
    projection: ProductProjection, operation: str
) -> None:
    """A run that has admitted its one write never admits another stage of any kind."""
    projection.create_run(_run(_rid("admitted-run")))
    admitted = _admit_write(projection, _rid("admitted-run"), "m-apply", operation="apply")
    assert admitted.state == "reserved"

    refused, _created = projection.reserve_mutation(
        _receipt(f"m-later-{operation}", run_id=_rid("admitted-run"), operation=operation),
        admit_write=operation in {"apply", "sync"},
    )

    assert refused.state == "accepted"
    assert refused.response_status == 409
    assert _refusal_code(refused) == _CONFLICT_CODE


def test_a_terminal_write_still_refuses_a_later_reservation(projection: ProductProjection) -> None:
    """Terminal write evidence is immutable; the run is never reopened for more work."""
    projection.create_run(_run(_rid("terminal-write")))
    apply_receipt = _admit_write(projection, _rid("terminal-write"), "m-apply", operation="apply")
    projection.add_prefect_execution(
        _rid("terminal-write"),
        _link("flow-apply", purpose="apply"),
        receipt_id=apply_receipt.receipt_id,
    )
    projection.complete_mutation(
        apply_receipt.receipt_id,
        response_status=202,
        response_body={"accepted": True},
        flow_run_id="flow-apply",
    )
    projection.claim_execution(_rid("terminal-write"), "flow-apply", worker_id=_WORKER_ID)
    projection.commit_claimed_execution(
        _rid("terminal-write"),
        "flow-apply",
        worker_id=_WORKER_ID,
        terminal_at=datetime.now(timezone.utc),
        terminal_state="completed",
        terminal_outcome="succeeded",
        writeback=ExecutionFinishWriteback(
            phase="applied",
            outcome="succeeded",
            finished_at=datetime.now(timezone.utc),
            summary={},
            results={},
        ),
    )

    refused, _created = projection.reserve_mutation(
        _receipt("m-second-apply", run_id=_rid("terminal-write"), operation="apply"),
        admit_write=True,
    )

    assert refused.state == "accepted"
    assert _refusal_code(refused) == _CONFLICT_CODE


def test_only_the_admitted_receipt_may_append_the_write_execution(projection: ProductProjection) -> None:
    """A second receipt cannot borrow the admission another receipt won."""
    projection.create_run(_run(_rid("owned-append")))
    admitted = _admit_write(projection, _rid("owned-append"), "m-apply", operation="apply")
    stranger = _receipt("m-stranger", run_id=_rid("owned-append"), operation="apply")

    with pytest.raises(ValueError, match="write admission"):
        projection.add_prefect_execution(
            _rid("owned-append"),
            _link("flow-stranger", purpose="apply"),
            receipt_id=stranger.receipt_id,
        )

    appended = projection.add_prefect_execution(
        _rid("owned-append"),
        _link("flow-apply", purpose="apply"),
        receipt_id=admitted.receipt_id,
    )
    assert appended.flow_run_id == "flow-apply"


def test_an_appended_execution_purpose_must_match_the_admitted_operation(projection: ProductProjection) -> None:
    """The admission names one operation; the append cannot be a different stage."""
    projection.create_run(_run(_rid("purpose-match")))
    admitted = _admit_write(projection, _rid("purpose-match"), "m-apply", operation="apply")

    with pytest.raises(ValueError, match="write admission"):
        projection.add_prefect_execution(
            _rid("purpose-match"),
            _link("flow-sync", purpose="sync"),
            receipt_id=admitted.receipt_id,
        )


def test_a_receipt_appends_at_most_one_execution(projection: ProductProjection) -> None:
    """The receipt-to-execution relation is the one-append rule, enforced durably."""
    projection.create_run(_run(_rid("single-append")))
    admitted = _admit_write(projection, _rid("single-append"), "m-apply", operation="apply")
    projection.add_prefect_execution(
        _rid("single-append"),
        _link("flow-apply", purpose="apply"),
        receipt_id=admitted.receipt_id,
    )

    with pytest.raises(ValueError):
        projection.add_prefect_execution(
            _rid("single-append"),
            _link("flow-apply-again", purpose="apply"),
            receipt_id=admitted.receipt_id,
        )

    stored = projection.lookup_run(_rid("single-append")).value
    assert stored is not None
    assert [link.flow_run_id for link in stored.prefect_executions] == ["flow-apply"]


def test_a_read_reservation_cannot_append_after_a_write_admission(projection: ProductProjection) -> None:
    """A receipt reserved before the write cannot append its execution afterwards."""
    projection.create_run(_run(_rid("late-read-append")))
    verify = _reserve_read(projection, _rid("late-read-append"), "m-verify", operation="verify")
    projection.complete_mutation(
        verify.receipt_id,
        response_status=202,
        response_body={"accepted": True},
        flow_run_id="flow-verify",
    )
    admitted = _admit_write(projection, _rid("late-read-append"), "m-apply", operation="apply")
    assert admitted.state == "reserved"
    late = _receipt("m-late-verify", run_id=_rid("late-read-append"), operation="verify")

    with pytest.raises(ValueError, match="write admission"):
        projection.add_prefect_execution(
            _rid("late-read-append"),
            _link("flow-late-verify", purpose="verify"),
            receipt_id=late.receipt_id,
        )


def test_a_resolved_receipt_cannot_append_an_execution(projection: ProductProjection) -> None:
    """An append belongs to a submission still in flight, never to an answered one."""
    projection.create_run(_run(_rid("resolved-append")))
    admitted = _admit_write(projection, _rid("resolved-append"), "m-apply", operation="apply")
    projection.complete_mutation(
        admitted.receipt_id,
        response_status=202,
        response_body={"accepted": True},
        flow_run_id="flow-apply",
    )

    with pytest.raises(ValueError, match="unresolved"):
        projection.add_prefect_execution(
            _rid("resolved-append"),
            _link("flow-apply", purpose="apply"),
            receipt_id=admitted.receipt_id,
        )


def test_the_same_client_key_resumes_one_reservation_and_one_prefect_key(projection: ProductProjection) -> None:
    """A repeated submission before its answer reuses the reservation it already made."""
    projection.create_run(_run(_rid("same-key-resume")))
    first = _admit_write(projection, _rid("same-key-resume"), "m-apply", operation="apply")

    resumed, created = projection.reserve_mutation(
        _receipt("m-apply-second", run_id=_rid("same-key-resume"), operation="apply", client_key="m-apply"),
        admit_write=True,
    )

    assert created is False
    assert resumed.receipt_id == first.receipt_id
    assert resumed.prefect_key == first.prefect_key
    assert resumed.state == "reserved"


def test_a_different_client_key_cannot_resume_another_actors_reservation(projection: ProductProjection) -> None:
    """Resume is keyed by the client's own key; a new key is a new, losing request."""
    projection.create_run(_run(_rid("different-key")))
    _admit_write(projection, _rid("different-key"), "m-apply", operation="apply")

    refused, created = projection.reserve_mutation(
        _receipt("m-apply-other", run_id=_rid("different-key"), operation="apply", client_key="other-key"),
        admit_write=True,
    )

    assert created is True
    assert refused.state == "accepted"
    assert _refusal_code(refused) == _CONFLICT_CODE


def test_a_new_run_reservation_is_never_arbitrated_against_another_run(projection: ProductProjection) -> None:
    """Creating a sync run admits its write; a previous run's admission is irrelevant."""
    projection.create_run(_run(_rid("existing-run")))
    _admit_write(projection, _rid("existing-run"), "m-existing-apply", operation="apply")

    created_receipt, created = projection.reserve_mutation(
        _receipt("m-new-sync", run_id=_rid("new-sync-run"), operation="sync"),
        run=_run(_rid("new-sync-run"), operation="sync"),
        admit_write=True,
    )

    assert created is True
    assert created_receipt.state == "reserved"


def test_cancellation_is_outside_the_stage_arbitration(projection: ProductProjection) -> None:
    """An admitted write must still be cancellable; cancel never reserves a stage."""
    projection.create_run(_run(_rid("cancellable")))
    _admit_write(projection, _rid("cancellable"), "m-apply", operation="apply")

    cancel, created = projection.reserve_mutation(_receipt("m-cancel", run_id=_rid("cancellable"), operation="cancel"))

    assert created is True
    assert cancel.state == "reserved"


def _claimed_write_run(projection: ProductProjection, run_id: str, *, purpose: str) -> str:
    """Admit, append, and claim one write execution, returning its flow-run ID."""
    projection.create_run(_run(run_id, operation="sync" if purpose == "sync" else "plan"))
    admitted = _admit_write(projection, run_id, f"m-{run_id}", operation=purpose)
    flow_run_id = f"flow-{run_id}"
    projection.add_prefect_execution(run_id, _link(flow_run_id, purpose=purpose), receipt_id=admitted.receipt_id)
    projection.complete_mutation(
        admitted.receipt_id,
        response_status=202,
        response_body={"accepted": True},
        flow_run_id=flow_run_id,
    )
    projection.claim_execution(run_id, flow_run_id, worker_id=_WORKER_ID)
    return flow_run_id


@pytest.mark.parametrize("purpose", ["apply", "sync"])
def test_a_claimed_write_interruption_records_durable_reconciliation_evidence(
    projection: ProductProjection, purpose: str
) -> None:
    """An ambiguous write verdict and its safety bit are one durable decision."""
    run_id = _rid(f"ambiguous-{purpose}")
    flow_run_id = _claimed_write_run(projection, run_id, purpose=purpose)

    assert projection.interrupt_execution(run_id, flow_run_id) is True

    stored = projection.lookup_run(run_id).value
    assert stored is not None
    assert stored.reconciliation_required is True
    assert (stored.phase, stored.outcome) == ("interrupted", "ambiguous")


def test_an_interrupted_read_execution_records_no_reconciliation_evidence(projection: ProductProjection) -> None:
    """A plan or verify worker cannot have written; ambiguity is a write-only verdict."""
    projection.create_run(_run(_rid("ambiguous-plan")))
    reserved = _reserve_read(projection, _rid("ambiguous-plan"), "m-plan", operation="plan")
    projection.add_prefect_execution(
        _rid("ambiguous-plan"), _link("flow-plan", purpose="plan"), receipt_id=reserved.receipt_id
    )
    projection.complete_mutation(
        reserved.receipt_id,
        response_status=202,
        response_body={"accepted": True},
        flow_run_id="flow-plan",
    )
    projection.claim_execution(_rid("ambiguous-plan"), "flow-plan", worker_id=_WORKER_ID)

    assert projection.interrupt_execution(_rid("ambiguous-plan"), "flow-plan") is True

    stored = projection.lookup_run(_rid("ambiguous-plan")).value
    assert stored is not None
    assert stored.reconciliation_required is False


def test_the_worker_commits_ambiguity_and_its_evidence_together(projection: ProductProjection) -> None:
    """A live worker that may have written records the verdict, the record, and the bit."""
    flow_run_id = _claimed_write_run(projection, _rid("worker-ambiguous"), purpose="apply")

    committed = projection.commit_claimed_execution(
        _rid("worker-ambiguous"),
        flow_run_id,
        worker_id=_WORKER_ID,
        terminal_at=datetime.now(timezone.utc),
        terminal_state="interrupted",
        terminal_outcome="ambiguous",
        writeback=ExecutionFinishWriteback(
            phase="apply-interrupted",
            outcome="ambiguous",
            finished_at=datetime.now(timezone.utc),
            summary={"failed_stage": "apply", "applied_operations": ["op-1"]},
            results={"apply_failure": {"stage": "apply", "outcome": "ambiguous"}},
        ),
    )

    assert committed is True
    stored = projection.lookup_run(_rid("worker-ambiguous")).value
    assert stored is not None
    assert stored.reconciliation_required is True
    assert stored.results["apply_failure"]["outcome"] == "ambiguous"


def test_result_replacement_cannot_clear_recorded_reconciliation_evidence(projection: ProductProjection) -> None:
    """Nothing normal-looking written afterwards may retract the safety bit."""
    flow_run_id = _claimed_write_run(projection, _rid("never-cleared"), purpose="apply")
    projection.interrupt_execution(_rid("never-cleared"), flow_run_id)

    projection.record_results(_rid("never-cleared"), {"reconciliation_required": False, "outcome": "succeeded"})
    projection.merge_results(_rid("never-cleared"), {"reconciliation_required": False})
    projection.finish_run(_rid("never-cleared"), phase="applied", outcome="succeeded", summary={}, results={})

    stored = projection.lookup_run(_rid("never-cleared")).value
    assert stored is not None
    assert stored.reconciliation_required is True


def test_a_new_run_starts_without_reconciliation_evidence(projection: ProductProjection) -> None:
    """The field is authoritative and false by default, not absent."""
    projection.create_run(_run(_rid("clean-run")))

    stored = projection.lookup_run(_rid("clean-run")).value

    assert stored is not None
    assert stored.reconciliation_required is False
