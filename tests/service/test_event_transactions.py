"""An event is saved in the same transaction as the decision or result it records (FR-014).

Each store write that records a decision takes an optional event. When the event cannot be
saved, here because its ``event_id`` is already taken, the decision is not saved either; with
a valid event, both are saved.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING

import pytest

from infrahub_sync import product_store
from infrahub_sync.product_store import (
    AuditEvent,
    EventFilters,
    PrefectExecutionLink,
    ProductProjection,
    RelatedResource,
)
from infrahub_sync.product_store.store import FileArtifactStore, PostgreSQLRunStore, SQLiteRunStore
from tests.product_store.test_contract import _append_execution, _connect, _receipt, _run

if TYPE_CHECKING:
    from pathlib import Path

_NOW = datetime(2026, 11, 6, 12, tzinfo=timezone.utc)
_WORKER_ID = "8c1da53d-0e6b-4d3d-a0f1-97b6a9ccebf0"
_SECRET = "s3cr3t-token-value"  # noqa: S105 - a fake secret the redaction must remove.


@pytest.fixture(params=("local", "postgresql-emulated"))
def projection(request: pytest.FixtureRequest, tmp_path: Path) -> ProductProjection:
    if request.param == "local":
        return ProductProjection(SQLiteRunStore(tmp_path / "local.sqlite3"), FileArtifactStore(tmp_path / "objects"))
    return ProductProjection(
        PostgreSQLRunStore(_connect(tmp_path / "emulated.sqlite3")), FileArtifactStore(tmp_path / "objects")
    )


def _event(event_id: str, event_type: str, *, reason: str | None = None) -> AuditEvent:
    return AuditEvent(
        event_id=event_id,
        run_id="run-001",
        actor="operator@example.com",
        operation="plan",
        reason=reason,
        outcome="accepted",
        created_at=_NOW,
        event_type=event_type,
        related=(RelatedResource(kind="account", id="operator@example.com"),),
    )


def _taken_event_id(projection: ProductProjection) -> str:
    """Record an event, so that a second event with its ID cannot be inserted."""
    projection.record_audit(_event("a-taken", "infrahub_sync.run.refused"))
    return "a-taken"


def _reserved_plan(projection: ProductProjection) -> str:
    receipt = _receipt()
    projection.reserve_mutation(receipt, run=_run())
    return receipt.receipt_id


def _claimed_execution(projection: ProductProjection) -> None:
    projection.create_run(_run())
    _append_execution(
        projection,
        "run-001",
        PrefectExecutionLink(flow_run_id="flow-001", purpose="plan", attempt=1, submitted_at=_NOW),
    )
    assert projection.claim_execution("run-001", "flow-001", worker_id=_WORKER_ID, claimed_at=_NOW)


def _commit_plan(projection: ProductProjection, event: AuditEvent) -> bool:
    return projection.commit_claimed_execution(
        "run-001",
        "flow-001",
        worker_id=_WORKER_ID,
        terminal_at=_NOW + timedelta(seconds=5),
        terminal_state="completed",
        terminal_outcome="succeeded",
        writeback=product_store.ExecutionFinishWriteback(
            phase="planned", outcome="planned", finished_at=_NOW + timedelta(seconds=5), summary={}, results={}
        ),
        event=event,
    )


def _acknowledged_cancellation(projection: ProductProjection) -> None:
    receipt = _receipt(operation="cancel", target_run_id="run-001")
    projection.create_run(_run())
    _append_execution(
        projection,
        "run-001",
        PrefectExecutionLink(flow_run_id="flow-001", purpose="plan", attempt=1, submitted_at=_NOW),
    )
    projection.reserve_mutation(receipt)
    assert projection.claim_mutation(receipt.receipt_id)
    assert projection.request_execution_cancellation(
        "run-001",
        "flow-001",
        requested_at=_NOW,
        recovery_deadline_at=_NOW + timedelta(seconds=30),
        recovery_seconds=30,
        expected_latest_position=0,
        receipt_id=receipt.receipt_id,
    )
    assert projection.acknowledge_execution_cancellation(
        "run-001",
        "flow-001",
        acknowledged_at=_NOW + timedelta(seconds=1),
        response_status=202,
        response_body={"run": {"run_id": "run-001"}, "orchestration": []},
    )
    projection.observe_prefect_execution("run-001", "flow-001", state="cancelled")


def _typed_ids(projection: ProductProjection) -> list[str]:
    return [event.event_id for event in projection.list_events(EventFilters(), limit=50, offset=0).items]


def test_failed_event_insert_leaves_the_mutation_receipt_unanswered(projection: ProductProjection) -> None:
    receipt_id = _reserved_plan(projection)
    taken = _taken_event_id(projection)

    with pytest.raises(sqlite3.IntegrityError):
        projection.complete_mutation(
            receipt_id,
            response_status=202,
            response_body={"run": {"run_id": "run-001"}},
            flow_run_id="flow-001",
            event=_event(taken, "infrahub_sync.run.accepted"),
        )

    receipt = projection.lookup_mutation(_receipt().actor, _receipt().key_digest).value
    assert receipt is not None
    assert (receipt.state, receipt.response_status) == ("reserved", None)
    assert _typed_ids(projection) == [taken]


def test_mutation_receipt_and_its_event_are_saved_together(projection: ProductProjection) -> None:
    receipt_id = _reserved_plan(projection)

    completed = projection.complete_mutation(
        receipt_id,
        response_status=202,
        response_body={"run": {"run_id": "run-001"}},
        flow_run_id="flow-001",
        event=_event("a-accepted", "infrahub_sync.run.accepted"),
    )

    assert (completed.state, completed.response_status) == ("accepted", 202)
    assert _typed_ids(projection) == ["a-accepted"]


def test_failed_event_insert_leaves_the_stage_result_unsaved(projection: ProductProjection) -> None:
    _claimed_execution(projection)
    taken = _taken_event_id(projection)

    with pytest.raises(sqlite3.IntegrityError):
        _commit_plan(projection, _event(taken, "infrahub_sync.run.planned"))

    run = projection.lookup_run("run-001").value
    assert run is not None
    assert (run.phase, run.outcome, run.finished_at) == ("planning", None, None)
    assert run.prefect_executions[0].terminal_at is None


def test_stage_result_and_its_event_are_saved_together(projection: ProductProjection) -> None:
    _claimed_execution(projection)

    assert _commit_plan(projection, _event("a-planned", "infrahub_sync.run.planned"))

    run = projection.lookup_run("run-001").value
    assert run is not None
    assert (run.phase, run.outcome) == ("planned", "planned")
    assert run.prefect_executions[0].terminal_state == "completed"
    assert _typed_ids(projection) == ["a-planned"]


def test_stage_result_refused_by_the_claim_saves_no_event(projection: ProductProjection) -> None:
    projection.create_run(_run())
    _append_execution(
        projection,
        "run-001",
        PrefectExecutionLink(flow_run_id="flow-001", purpose="plan", attempt=1, submitted_at=_NOW),
    )

    assert not _commit_plan(projection, _event("a-planned", "infrahub_sync.run.planned"))

    assert _typed_ids(projection) == []


def test_failed_event_insert_leaves_the_cancellation_unsaved(projection: ProductProjection) -> None:
    _acknowledged_cancellation(projection)
    taken = _taken_event_id(projection)

    with pytest.raises(sqlite3.IntegrityError):
        projection.cancel_execution(
            "run-001",
            "flow-001",
            terminal_at=_NOW + timedelta(seconds=30),
            event=_event(taken, "infrahub_sync.run.cancelled"),
        )

    run = projection.lookup_run("run-001").value
    assert run is not None
    assert run.phase != "cancelled"
    assert run.outcome is None
    assert run.prefect_executions[0].terminal_state is None


def test_cancellation_and_its_event_are_saved_together(projection: ProductProjection) -> None:
    _acknowledged_cancellation(projection)

    assert projection.cancel_execution(
        "run-001",
        "flow-001",
        terminal_at=_NOW + timedelta(seconds=30),
        event=_event("a-cancelled", "infrahub_sync.run.cancelled"),
    )

    run = projection.lookup_run("run-001").value
    assert run is not None
    assert (run.phase, run.outcome) == ("cancelled", "cancelled")
    assert _typed_ids(projection) == ["a-cancelled"]


def test_event_written_with_a_decision_is_redacted(projection: ProductProjection) -> None:
    receipt_id = _reserved_plan(projection)

    projection.complete_mutation(
        receipt_id,
        response_status=202,
        response_body={"run": {"run_id": "run-001"}},
        flow_run_id="flow-001",
        event=_event("a-accepted", "infrahub_sync.run.accepted", reason=f"token {_SECRET}"),
        secrets=(_SECRET,),
    )

    (stored,) = projection.list_events(EventFilters(), limit=50, offset=0).items
    assert stored.reason is not None
    assert _SECRET not in stored.reason
