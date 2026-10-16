"""A stopped apply shows what it wrote and which operation failed (spec 009 T080, User Story 4).

A `sync` run whose plan leaves out records stops after its plan; the team applies it with the
confirmed count. The destination rejects operation K of the apply. The run's
`failure.apply_progress` gives the plan's operation count, the K - 1 operations written, the
failed operation read from the plan by its ID, and whether it may have written part of its
change. `GET /runs/{run_id}/results` lists the written IDs under `apply_failure`. A second apply
of the run is refused with 409 `new-plan-required`, and the CLI prints the same message.

The run is admitted through the Sync API and its stages are driven through the production stage
boundary; the apply runs the real engine over the saved plan against a destination double.
"""

from __future__ import annotations

from contextlib import suppress
from typing import TYPE_CHECKING, Any

import pytest

pytest.importorskip("prefect")
pytest.importorskip("opsmill_prefect_extras")
pytest.importorskip("fastapi")

from typer.testing import CliRunner

from infrahub_sync.cli import app
from infrahub_sync.failure_cause import build_failure_cause
from infrahub_sync.plan.models import ApplyRecord
from infrahub_sync.plan.reader import load_plan_artifact
from infrahub_sync.service import flow as service_flow
from infrahub_sync.service.flow import service_sync_run
from tests.adapters.test_infrahub_planned_write import engine_over
from tests.plan.stopped_apply import RejectingDestination, rejection
from tests.service.execution_fixtures import stage_root
from tests.service.sync_stage_harness import SyncStageHarness, build_sync_stage_harness

if TYPE_CHECKING:
    from pathlib import Path

REJECT_AT = 4
NEW_PLAN_MESSAGE = "This plan was already applied or its apply stopped; create and review a new plan"


@pytest.fixture
def harness(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> SyncStageHarness:
    return build_sync_stage_harness(monkeypatch, tmp_path)


@pytest.fixture
def destination(harness: SyncStageHarness, monkeypatch: pytest.MonkeyPatch) -> RejectingDestination:
    """Apply with the real engine over the saved plan; the destination rejects operation K."""
    del harness  # Installs the doubles this one wraps.
    rejecting = RejectingDestination(REJECT_AT)
    double = service_flow.execute_run

    def execute_run(instance: Any, **kwargs: Any) -> Any:  # noqa: ANN401
        if kwargs.get("operation") != "apply":
            return double(instance, **kwargs)
        run_dir = stage_root(kwargs) / instance.name / kwargs["run_id"]
        manifest = load_plan_artifact(run_dir).manifest
        return engine_over(run_dir, rejecting, run_id=manifest.run_id).apply_plan(
            ownership=kwargs["ownership"],
            config_version=manifest.config_version,
            confirmed_left_out_count=kwargs["confirmed_left_out_count"],
        )

    monkeypatch.setattr(service_flow, "execute_run", execute_run)
    return rejecting


def _plan(harness: SyncStageHarness, run_id: str) -> dict[str, Any]:
    response = harness.get(f"/runs/{run_id}/plan")
    assert response.status_code == 200, response.text
    return response.json()


def _apply_body(plan: dict[str, Any]) -> dict[str, Any]:
    return {
        "expected_checksum": plan["checksum"],
        "confirm_writes": True,
        "confirmed_left_out_count": plan["summary"]["left_out"],
        "reason": "apply after review",
    }


def _stopped_apply(harness: SyncStageHarness) -> tuple[str, dict[str, Any]]:
    """A `sync` that stops after its plan, then its apply, stopped by the destination."""
    run_id = harness.run_sync()
    plan = _plan(harness, run_id)
    accepted = harness.post(f"/runs/{run_id}/apply", "first-apply", _apply_body(plan))
    assert accepted.status_code == 202, accepted.text
    harness.flow_run_id[:] = [accepted.json()["orchestration"][-1]["flow_run_id"]]
    # The run record is the subject, not the raise.
    with suppress(Exception):
        service_sync_run.fn(
            run_id,
            "apply",
            *harness.binding,
            expected_checksum=plan["checksum"],
            confirm_writes=True,
            confirmed_left_out_count=plan["summary"]["left_out"],
        )
    return run_id, plan


def test_a_stopped_apply_shows_the_operations_written_and_the_failed_operation(
    harness: SyncStageHarness, destination: RejectingDestination
) -> None:
    run_id, plan = _stopped_apply(harness)

    failure = harness.get(f"/runs/{run_id}").json()["run"]["failure"]
    failed_id = destination.attempted[-1]
    (failed,) = [operation for operation in plan["operations"] if operation["operation_id"] == failed_id]
    assert failure["stage"] == "apply"
    assert failure["failure_type"] == "destination_rejected_write"
    assert failure["apply_progress"] == {
        "operations_total": len(plan["operations"]),
        "operations_written": REJECT_AT - 1,
        "failed_operation": {
            "operation_id": failed_id,
            "action": failed["action"],
            "kind": failed["kind"],
            "identity": failed["identity"],
        },
        # The engine cannot know how far a rejected operation got (`failed_operation_wrote` null).
        "failed_operation_may_have_written": True,
    }
    results = harness.get(f"/runs/{run_id}/results").json()["results"]
    assert results["apply_failure"]["applied_operations"] == [item.operation_id for item in destination.written]
    assert results["apply_failure"]["failed_operation"] == failed_id


def test_applying_the_stopped_plan_again_needs_a_new_plan(
    harness: SyncStageHarness, destination: RejectingDestination
) -> None:
    run_id, plan = _stopped_apply(harness)
    attempted = list(destination.attempted)

    again = harness.post(f"/runs/{run_id}/apply", "second-apply", _apply_body(plan))

    assert again.status_code == 409, again.text
    error = again.json()["error"]
    assert (error["code"], error["message"]) == ("new-plan-required", NEW_PLAN_MESSAGE)
    client = harness.sync_client()
    try:
        printed = CliRunner().invoke(
            app,
            [
                "apply",
                run_id,
                "--expected-checksum",
                plan["checksum"],
                "--reason",
                "apply again",
                "--confirm-left-out",
                str(plan["summary"]["left_out"]),
            ],
            obj={"client": client},
        )
    finally:
        client.close()
    assert printed.exit_code == 1, printed.output
    assert f"apply refused: {NEW_PLAN_MESSAGE}" in printed.output
    assert destination.attempted == attempted, "Neither refusal reached the destination."
    outcomes = [event.outcome for event in harness.projection.audit_events(run_id) if event.operation == "apply"]
    assert outcomes[-2:] == ["refused-new-plan-required", "refused-new-plan-required"]


_OPERATIONS = (
    {"operation_id": "op_a", "action": "create", "kind": "LocationSite", "identity": {"name": "paris"}, "payload": {}},
    {"operation_id": "op_b", "action": "update", "kind": "DcimDevice", "identity": {"name": "rtr1"}, "payload": {}},
)


@pytest.mark.parametrize(
    ("wrote", "may_have_written"), [(None, True), (False, False)], ids=["reach-unknown", "proven-not-written"]
)
def test_the_failed_operation_may_have_written_unless_proven_otherwise(
    wrote: bool | None,  # noqa: FBT001 - a parametrized case, not a caller-facing switch
    may_have_written: bool,  # noqa: FBT001 - a parametrized case, not a caller-facing switch
) -> None:
    record = ApplyRecord(applied_operations=("op_a",), failed_operation="op_b", failed_operation_wrote=wrote)

    cause = build_failure_cause("apply", rejection(), apply_record=record, operations=_OPERATIONS)

    assert cause.apply_progress is not None
    assert cause.apply_progress.model_dump() == {
        "operations_total": 2,
        "operations_written": 1,
        "failed_operation": {
            "operation_id": "op_b",
            "action": "update",
            "kind": "DcimDevice",
            "identity": {"name": "rtr1"},
        },
        "failed_operation_may_have_written": may_have_written,
    }


def test_no_progress_without_the_apply_record_and_the_plan() -> None:
    record = ApplyRecord(applied_operations=("op_a",))

    assert build_failure_cause("apply", rejection(), apply_record=record).apply_progress is None
    assert build_failure_cause("apply", rejection(), operations=_OPERATIONS).apply_progress is None
    assert build_failure_cause("plan", rejection(), apply_record=record, operations=_OPERATIONS).apply_progress is None
