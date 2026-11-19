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
    SCHEDULE_START_OPERATION,
    SCHEDULE_STOPPED_EVENT_TYPE,
    AuditEvent,
    EventDetails,
    EventFilters,
    PrefectExecutionLink,
    ProductProjection,
    ProductRun,
    RelatedResource,
)
from infrahub_sync.product_store.store import FileArtifactStore, PostgreSQLRunStore, SQLiteRunStore
from infrahub_sync.service import events as service_events
from infrahub_sync.service.events import run_event, schedule_decision_type, schedule_event
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


def _requested_cancellation(projection: ProductProjection) -> str:
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
    return receipt.receipt_id


def _acknowledge(
    projection: ProductProjection,
    event: AuditEvent,
    *,
    seconds: int = 1,
    refusal_events: dict[int, AuditEvent] | None = None,
) -> bool:
    return projection.acknowledge_execution_cancellation(
        "run-001",
        "flow-001",
        acknowledged_at=_NOW + timedelta(seconds=seconds),
        response_status=202,
        response_body={"run": {"run_id": "run-001"}, "orchestration": []},
        event=event,
        refusal_events=refusal_events or {},
    )


def _refusal_events(*, unavailable_id: str = "a-cancel-unavailable") -> dict[int, AuditEvent]:
    return {
        409: _event("a-cancel-refused", "infrahub_sync.run.refused"),
        503: _event(unavailable_id, "infrahub_sync.run.unavailable"),
    }


def test_failed_event_insert_leaves_the_cancel_request_unanswered(projection: ProductProjection) -> None:
    _requested_cancellation(projection)
    taken = _taken_event_id(projection)

    with pytest.raises(sqlite3.IntegrityError):
        _acknowledge(projection, _event(taken, "infrahub_sync.run.accepted"))

    receipt = _receipt(operation="cancel", target_run_id="run-001")
    stored = projection.lookup_mutation(receipt.actor, receipt.key_digest).value
    assert stored is not None
    assert stored.response_status is None
    run = projection.lookup_run("run-001").value
    assert run is not None
    assert run.prefect_executions[0].cancellation_acknowledged_at is None


def test_cancel_acknowledgement_and_its_event_are_saved_together(projection: ProductProjection) -> None:
    _requested_cancellation(projection)

    assert _acknowledge(projection, _event("a-cancel-accepted", "infrahub_sync.run.accepted"))

    assert _typed_ids(projection) == ["a-cancel-accepted"]
    found = projection.lookup_mutation_for_execution("run-001", "flow-001", "cancel").value
    assert found is not None
    assert found.operation == "cancel"
    assert projection.lookup_mutation_for_execution("run-001", "flow-001", "plan").value is None


def test_cancel_acknowledged_too_late_saves_no_accepted_event(projection: ProductProjection) -> None:
    _requested_cancellation(projection)

    assert not _acknowledge(projection, _event("a-cancel-accepted", "infrahub_sync.run.accepted"), seconds=31)

    assert _typed_ids(projection) == []


def test_cancel_acknowledged_too_late_saves_its_refusal_event_with_the_refusal(projection: ProductProjection) -> None:
    """FR-014: the terminal state, the 503 receipt answer and the refusal event commit together."""
    _requested_cancellation(projection)

    assert not _acknowledge(
        projection,
        _event("a-cancel-accepted", "infrahub_sync.run.accepted"),
        seconds=31,
        refusal_events=_refusal_events(),
    )

    assert _typed_ids(projection) == ["a-cancel-unavailable"]
    receipt = _receipt(operation="cancel", target_run_id="run-001")
    stored = projection.lookup_mutation(receipt.actor, receipt.key_digest).value
    assert stored is not None
    assert stored.response_status == 503


def test_failed_refusal_event_insert_leaves_the_late_cancellation_unsaved(projection: ProductProjection) -> None:
    _requested_cancellation(projection)
    taken = _taken_event_id(projection)

    with pytest.raises(sqlite3.IntegrityError):
        _acknowledge(
            projection,
            _event("a-cancel-accepted", "infrahub_sync.run.accepted"),
            seconds=31,
            refusal_events=_refusal_events(unavailable_id=taken),
        )

    receipt = _receipt(operation="cancel", target_run_id="run-001")
    stored = projection.lookup_mutation(receipt.actor, receipt.key_digest).value
    assert stored is not None
    assert stored.response_status is None
    run = projection.lookup_run("run-001").value
    assert run is not None
    assert run.prefect_executions[0].terminal_at is None


# --- several events with one decision, and schedule events (spec 021, research R9) -----------

_SCHEDULE_ID = f"s-{'a' * 32}"
_CONFIG_ID = "20260101T0000-aaaaaaaa"


def _missed_start(event_id: str, day: int) -> AuditEvent:
    return schedule_event(
        event_type=service_events.SCHEDULE_START_MISSED,
        actor="operator@example.com",
        operation=service_events.SCHEDULE_START,
        reason=None,
        outcome="missed-skipped",
        schedule_id=_SCHEDULE_ID,
        config_id=_CONFIG_ID,
        registry_version=3,
        details=EventDetails(start_at=_NOW + timedelta(days=day)),
        created_at=_NOW + timedelta(seconds=day),
    ).model_copy(update={"event_id": event_id})


def test_mutation_receipt_and_a_sequence_of_events_are_saved_together(projection: ProductProjection) -> None:
    receipt_id = _reserved_plan(projection)

    projection.complete_mutation(
        receipt_id,
        response_status=202,
        response_body={"run": {"run_id": "run-001"}},
        flow_run_id="flow-001",
        event=_event("a-accepted", "infrahub_sync.run.accepted"),
        events=(_missed_start("a-missed-1", 1), _missed_start("a-missed-2", 2)),
    )

    assert _typed_ids(projection) == ["a-accepted", "a-missed-1", "a-missed-2"]


def test_one_failed_event_of_a_sequence_leaves_the_mutation_receipt_unanswered(projection: ProductProjection) -> None:
    receipt_id = _reserved_plan(projection)
    taken = _taken_event_id(projection)

    with pytest.raises(sqlite3.IntegrityError):
        projection.complete_mutation(
            receipt_id,
            response_status=202,
            response_body={"run": {"run_id": "run-001"}},
            flow_run_id="flow-001",
            events=(_missed_start("a-missed-1", 1), _missed_start(taken, 2)),
        )

    receipt = projection.lookup_mutation(_receipt().actor, _receipt().key_digest).value
    assert receipt is not None
    assert receipt.state == "reserved"
    assert _typed_ids(projection) == [taken]


def _commit_plan_with_events(projection: ProductProjection, *events: AuditEvent, secrets: tuple[str, ...] = ()) -> bool:
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
        events=events,
        secrets=secrets,
    )


def test_stage_result_and_a_sequence_of_events_are_saved_together(projection: ProductProjection) -> None:
    _claimed_execution(projection)

    assert _commit_plan_with_events(
        projection,
        _event("a-planned", "infrahub_sync.run.planned"),
        _missed_start("a-missed-1", 1),
        _missed_start("a-missed-2", 2),
    )

    assert _typed_ids(projection) == ["a-planned", "a-missed-1", "a-missed-2"]
    schedule = (RelatedResource(kind="schedule", id=_SCHEDULE_ID),)
    of_schedule = projection.list_events(EventFilters(related=schedule), limit=50, offset=0).items
    assert [event.event_id for event in of_schedule] == ["a-missed-1", "a-missed-2"]
    assert of_schedule[0].details == EventDetails(start_at=_NOW + timedelta(days=1))


def test_one_failed_event_of_a_sequence_leaves_the_stage_result_unsaved(projection: ProductProjection) -> None:
    _claimed_execution(projection)
    taken = _taken_event_id(projection)

    with pytest.raises(sqlite3.IntegrityError):
        _commit_plan_with_events(projection, _missed_start("a-missed-1", 1), _missed_start(taken, 2))

    run = projection.lookup_run("run-001").value
    assert run is not None
    assert (run.phase, run.outcome) == ("planning", None)
    assert run.prefect_executions[0].terminal_at is None
    assert _typed_ids(projection) == [taken]


def test_events_of_a_sequence_are_redacted(projection: ProductProjection) -> None:
    _claimed_execution(projection)
    event = _missed_start("a-missed-1", 1).model_copy(update={"reason": f"token {_SECRET}"})

    assert _commit_plan_with_events(projection, event, secrets=(_SECRET,))

    (stored,) = projection.list_events(EventFilters(), limit=50, offset=0).items
    assert stored.reason is not None
    assert _SECRET not in stored.reason


def test_a_schedule_event_names_the_schedule_the_configuration_and_the_version() -> None:
    event = _missed_start("a-missed-1", 1)

    assert event.related == (
        RelatedResource(kind="account", id="operator@example.com"),
        RelatedResource(kind="config", id=_CONFIG_ID),
        RelatedResource(kind="config_version", id=f"{_CONFIG_ID}@3"),
        RelatedResource(kind="schedule", id=_SCHEDULE_ID),
    )
    assert (event.event_type, event.operation) == ("infrahub_sync.schedule.start_missed", "schedule-start")
    assert (event.config_id, event.registry_version, event.run_id, event.reason) == (_CONFIG_ID, 3, None, None)
    primary = event.primary_resource
    assert primary is not None
    assert (primary.kind, primary.id) == ("schedule", _SCHEDULE_ID)


def test_a_schedule_event_of_a_run_has_the_run_as_its_primary_resource() -> None:
    event = schedule_event(
        event_type=service_events.SCHEDULE_START_SKIPPED,
        actor="operator@example.com",
        operation=service_events.SCHEDULE_START,
        reason=None,
        outcome="skipped-run-in-progress",
        schedule_id=_SCHEDULE_ID,
        config_id=_CONFIG_ID,
        registry_version=3,
        run_id="run-in-progress",
    )

    primary = event.primary_resource
    assert primary is not None
    assert (primary.kind, primary.id) == ("run", "run-in-progress")
    assert RelatedResource(kind="schedule", id=_SCHEDULE_ID) in event.related
    assert event.created_at.utcoffset() == timedelta(0)


def test_a_schedule_event_of_a_request_refused_before_a_schedule_exists_names_no_schedule() -> None:
    event = schedule_event(
        event_type=service_events.SCHEDULE_REFUSED,
        actor="operator@example.com",
        operation="schedule-create",
        reason="nightly sync",
        outcome="refused-schedule-name-in-use",
        schedule_id=None,
        config_id=_CONFIG_ID,
        registry_version=3,
    )

    assert all(resource.kind != "schedule" for resource in event.related)
    primary = event.primary_resource
    assert primary is not None
    assert (primary.kind, primary.id) == ("config", _CONFIG_ID)


@pytest.mark.parametrize(
    ("operation", "outcome", "expected"),
    [
        ("schedule-create", "accepted", "infrahub_sync.schedule.created"),
        ("schedule-change", "accepted", "infrahub_sync.schedule.changed"),
        ("schedule-pause", "accepted", "infrahub_sync.schedule.paused"),
        ("schedule-resume", "accepted", "infrahub_sync.schedule.resumed"),
        ("schedule-delete", "accepted", "infrahub_sync.schedule.deleted"),
        ("schedule-take-over", "accepted", "infrahub_sync.schedule.taken_over"),
        ("schedule-create", "refused-schedule-name-in-use", "infrahub_sync.schedule.refused"),
        ("schedule-pause", "replayed", "infrahub_sync.schedule.replayed"),
        ("schedule-pause", "refused-authentication", None),
    ],
)
def test_the_event_type_of_a_schedule_request(operation: str, outcome: str, expected: str | None) -> None:
    assert schedule_decision_type(operation, outcome) == expected


@pytest.mark.parametrize(("operation", "outcome"), [("sync", "accepted"), ("schedule-create", "unavailable")])
def test_a_schedule_request_with_no_event_type_is_refused(operation: str, outcome: str) -> None:
    with pytest.raises(ValueError, match="no schedule event type"):
        schedule_decision_type(operation, outcome)


def test_the_schedule_event_types_are_those_of_the_contract() -> None:
    names = {name: value for name, value in vars(service_events).items() if name.startswith("SCHEDULE_")}
    types = {value for value in names.values() if value.startswith("infrahub_sync.")}

    assert types == {
        f"infrahub_sync.schedule.{action}"
        for action in (
            "created",
            "changed",
            "paused",
            "resumed",
            "deleted",
            "taken_over",
            "refused",
            "replayed",
            "start_skipped",
            "start_queued",
            "start_missed",
            "start_failed",
            "stopped",
            "proposed_change_opened",
            "proposed_change_replaced",
            "proposed_change_kept",
            "proposed_change_failed",
        )
    }
    assert service_events.SCHEDULE_STOPPED == SCHEDULE_STOPPED_EVENT_TYPE
    assert service_events.SCHEDULE_START == SCHEDULE_START_OPERATION == "schedule-start"


def _scheduled(run: ProductRun) -> ProductRun:
    return ProductRun.model_validate(
        {
            **run.model_dump(),
            "trigger_type": "scheduled",
            "schedule_id": _SCHEDULE_ID,
            "scheduled_start_at": _NOW,
            "config_id": _CONFIG_ID,
            "registry_version": 3,
            "package_checksum": "a" * 64,
            "destination_branch": "main",
        }
    )


def test_an_event_of_a_scheduled_run_names_its_schedule() -> None:
    event = run_event(
        "run-001",
        _scheduled(_run()),
        event_type="infrahub_sync.run.synced",
        actor="operator@example.com",
        operation="sync",
        reason=None,
        outcome="succeeded",
    )

    assert event.related == (
        RelatedResource(kind="account", id="operator@example.com"),
        RelatedResource(kind="config", id=_CONFIG_ID),
        RelatedResource(kind="config_version", id=f"{_CONFIG_ID}@3"),
        RelatedResource(kind="schedule", id=_SCHEDULE_ID),
        RelatedResource(kind="branch", id="main"),
    )
    primary = event.primary_resource
    assert primary is not None
    assert primary.kind == "run"


def test_an_event_of_a_run_that_a_person_started_names_no_schedule() -> None:
    event = run_event(
        "run-001",
        _run(),
        event_type="infrahub_sync.run.planned",
        actor="operator@example.com",
        operation="plan",
        reason=None,
        outcome="succeeded",
    )

    assert all(resource.kind != "schedule" for resource in event.related)
