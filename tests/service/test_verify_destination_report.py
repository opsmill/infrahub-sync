"""The `verify` report of an interrupted run (spec 022 User Story 2, FR-005 to FR-008).

For a run that requires reconciliation, `verify` reads from the destination the nodes of the
uncertain operations and reports each as `matches`, `differs` or `missing`. The window is the
one the run's progress view computes (critique E3). The report never fails `verify`, never
holds a value, and is never produced when a pre-write check failed.

The window cases call `_destination_report` with stored runs built in memory and a recording
reader. The stage cases drive the real `verify` stage through the harness of
`test_verify_destination.py`, whose destination double gains a recording read.
"""

from __future__ import annotations

import dataclasses
import json
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

import httpx
import pytest

pytest.importorskip("prefect")
pytest.importorskip("opsmill_prefect_extras")

from infrahub_sync.plan.destination_check import (
    VERIFY_REPORT_LIMIT,
    DestinationNode,
    DestinationReadError,
    PlannedNodeRead,
)
from infrahub_sync.plan.errors import failed_checks
from infrahub_sync.plan.identity import canonical_identity, operation_id
from infrahub_sync.plan.models import PlannedOperation
from infrahub_sync.product_store import ApplyProgress, PrefectExecutionLink, ProductRun
from infrahub_sync.service import flow as service_flow
from infrahub_sync.service.resume_view import UNCERTAIN_AFTER_SUCCESSFUL_SAVE
from tests.plan.artifact_fixtures import TAMPERABLE_VALUE
from tests.service.test_verify_destination import ALL_CHECKS, _break_lineage, _execute, _harness

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from pathlib import Path

    from tests.service.test_verify_destination import _Harness

KIND = "BuiltinTag"
SAVED_AT = datetime(2026, 11, 12, 10, 15, tzinfo=timezone.utc)
CANARY = "verify-report-canary-value"
# An address with user information, as a transport error's text can carry one.
LEAKED_ADDRESS = "https://user:canary@destination.invalid"
UNREACHABLE = f"cannot reach {LEAKED_ADDRESS}"


def _operation(index: int, action: str = "create") -> PlannedOperation:
    identity = canonical_identity({"name": f"tag-{index}"}, kind=KIND)
    return PlannedOperation(
        operation_id=operation_id(action, KIND, identity),
        action=action,  # ty: ignore[invalid-argument-type]
        kind=KIND,
        identity=identity,
        tier=0,
        payload=None if action == "delete" else {"name": f"tag-{index}", "description": CANARY},
    )


def _operations(total: int) -> list[PlannedOperation]:
    return [_operation(index) for index in range(total)]


def _interrupted_run(
    operations: Sequence[PlannedOperation],
    *,
    marker: int | None,
    own_marker: bool = True,
    failure: Mapping[str, Any] | None = None,
) -> ProductRun:
    """A run whose apply saved `marker` (or an earlier apply did, when not `own_marker`) and was interrupted."""
    progress = (
        None
        if marker is None
        else ApplyProgress(
            position=marker, operation_id=operations[marker].operation_id, total=len(operations), saved_at=SAVED_AT
        )
    )
    executions = []
    if not own_marker:
        executions.append(
            PrefectExecutionLink(
                flow_run_id="earlier-apply",
                purpose="apply",
                attempt=1,
                terminal_at=SAVED_AT,
                terminal_state="interrupted",
                terminal_outcome="ambiguous",
                apply_progress=progress,
            )
        )
    executions.extend(
        (
            PrefectExecutionLink(
                flow_run_id="interrupted-apply",
                purpose="apply" if own_marker else "resume",
                attempt=1,
                terminal_at=SAVED_AT,
                terminal_state="interrupted",
                terminal_outcome="ambiguous",
                apply_progress=progress if own_marker else None,
            ),
            # The running verify itself: unfinished, so the store's resumable-run rule is not asked.
            PrefectExecutionLink(flow_run_id="running-verify", purpose="verify", attempt=1),
        )
    )
    failure_key = "apply_failure" if own_marker else "resume_failure"
    return ProductRun(
        run_id="interrupted-run",
        operation="plan",
        configuration_reference="config@1",
        started_at=SAVED_AT,
        phase="apply-interrupted",
        reconciliation_required=True,
        results={failure_key: dict(failure or {})},
        prefect_executions=tuple(executions),
    )


class _Reader:
    """A destination reader that records what it was asked for and answers one state for every operation."""

    def __init__(self, node: DestinationNode | None = None) -> None:
        self.node = node
        self.asked: list[list[str]] = []

    def read_planned_nodes(self, operations: Sequence[PlannedOperation]) -> dict[str, PlannedNodeRead]:
        self.asked.append([operation.operation_id for operation in operations])
        return {operation.operation_id: PlannedNodeRead(self.node) for operation in operations}


def _report(run: ProductRun, operations: Sequence[PlannedOperation], reader: object) -> dict[str, Any]:
    projection = SimpleNamespace(lookup_run=lambda _run_id: SimpleNamespace(value=run))
    return service_flow._destination_report(
        projection,  # ty: ignore[invalid-argument-type]
        run.run_id,
        operations=operations,
        destination=reader,
    )


def _reported_positions(report: Mapping[str, Any], operations: Sequence[PlannedOperation]) -> list[int]:
    positions = {operation.operation_id: index for index, operation in enumerate(operations)}
    return [positions[entry["operation_id"]] for entry in report["operations"]]


# ======================================================================================
# The uncertain window
# ======================================================================================


def test_the_window_ends_with_the_failing_operation_when_it_is_known() -> None:
    operations = _operations(40)
    run = _interrupted_run(operations, marker=9, failure={"failed_operation": operations[14].operation_id})

    report = _report(run, operations, _Reader())

    assert _reported_positions(report, operations) == [10, 11, 12, 13, 14]
    assert "operations_truncated" not in report


def test_the_window_holds_the_next_500_operations_when_the_last_save_succeeded() -> None:
    operations = _operations(1_200)
    run = _interrupted_run(operations, marker=99, failure={"error_type": "AbandonedExecution"})

    report = _report(run, operations, _Reader())

    assert _reported_positions(report, operations) == list(range(100, 100 + UNCERTAIN_AFTER_SUCCESSFUL_SAVE))


@pytest.mark.parametrize(
    ("own_marker", "failure"),
    [
        # The apply stopped because its progress could not be saved.
        (True, {"error_type": "ApplyProgressLostError"}),
        # The latest write execution saved no position of its own.
        (False, {}),
    ],
    ids=["progress-lost", "no-own-marker"],
)
def test_the_window_holds_every_operation_after_the_marker_when_the_last_save_failed(
    own_marker: bool,  # noqa: FBT001
    failure: dict[str, Any],
) -> None:
    operations = _operations(1_200)
    run = _interrupted_run(operations, marker=99, own_marker=own_marker, failure=failure)

    report = _report(run, operations, _Reader())

    assert _reported_positions(report, operations) == list(range(100, 1_200))


def test_a_window_above_2000_operations_reports_2000_and_counts_the_rest() -> None:
    operations = _operations(2_600)
    run = _interrupted_run(operations, marker=None)

    reader = _Reader()
    report = _report(run, operations, reader)

    assert len(report["operations"]) == VERIFY_REPORT_LIMIT
    assert report["operations_truncated"] == 600
    # Only the reported operations are read.
    assert len(reader.asked[0]) == VERIFY_REPORT_LIMIT


def test_deletes_in_the_window_are_neither_read_nor_reported() -> None:
    operations = [_operation(0), _operation(1, "delete"), _operation(2)]
    run = _interrupted_run(operations, marker=None)

    reader = _Reader()
    report = _report(run, operations, reader)

    assert _reported_positions(report, operations) == [0, 2]
    assert reader.asked == [[operations[0].operation_id, operations[2].operation_id]]


def test_a_run_that_requires_no_reconciliation_gets_no_report_and_reads_nothing() -> None:
    operations = _operations(3)
    run = _interrupted_run(operations, marker=None).model_copy(update={"reconciliation_required": False})

    reader = _Reader()

    assert _report(run, operations, reader) == {}
    assert reader.asked == []


@pytest.mark.parametrize(
    ("node", "state"),
    [
        (DestinationNode(attributes={"name": "tag-0", "description": CANARY}), "matches"),
        (DestinationNode(attributes={"name": "tag-0", "description": f"{CANARY}-changed"}), "differs"),
        (None, "missing"),
    ],
)
def test_each_state_is_reported_with_the_operation_and_never_a_value(node: DestinationNode | None, state: str) -> None:
    operations = _operations(1)
    run = _interrupted_run(operations, marker=None)

    report = _report(run, operations, _Reader(node))

    assert report == {
        "operations": [{"operation_id": operations[0].operation_id, "kind": KIND, "action": "create", "state": state}]
    }
    assert CANARY not in json.dumps(report)


def test_a_failed_read_fails_with_a_fixed_message_and_no_attached_cause() -> None:
    operations = _operations(1)
    run = _interrupted_run(operations, marker=None)

    class _Failing:
        def read_planned_nodes(self, operations: object) -> object:  # noqa: PLR6301
            del operations
            raise httpx.ConnectError(UNREACHABLE)

    with pytest.raises(DestinationReadError) as raised:
        _report(run, operations, _Failing())

    assert LEAKED_ADDRESS not in str(raised.value)
    assert raised.value.__cause__ is None
    assert raised.value.__suppress_context__ is True


# ======================================================================================
# The verify stage
# ======================================================================================


def _requiring_reconciliation(harness: _Harness, monkeypatch: pytest.MonkeyPatch) -> None:
    """Make the harness's run one that requires reconciliation, as an interrupted apply leaves it."""
    lookup_run = harness.projection.lookup_run

    def interrupted(run_id: str) -> Any:  # noqa: ANN401
        found = lookup_run(run_id)
        if found.value is None:
            return found
        return dataclasses.replace(found, value=found.value.model_copy(update={"reconciliation_required": True}))

    monkeypatch.setattr(harness.projection, "lookup_run", interrupted)


def _reading_destination(
    harness: _Harness, *, node: DestinationNode | None = None, error: Exception | None = None
) -> list[list[str]]:
    """Give the harness's destination a read that records each call and answers `node` for every operation."""
    asked: list[list[str]] = []

    def read_planned_nodes(self: object, operations: Sequence[PlannedOperation]) -> dict[str, PlannedNodeRead]:
        del self
        asked.append([operation.operation_id for operation in operations])
        if error is not None:
            raise error
        return {operation.operation_id: PlannedNodeRead(node) for operation in operations}

    harness.destination = type("Reading", (harness.destination,), {"read_planned_nodes": read_planned_nodes})
    return asked


@pytest.mark.parametrize(
    ("node", "state"),
    [
        (DestinationNode(attributes={"name": "different", "description": "changed"}), "differs"),
        (None, "missing"),
    ],
)
def test_differs_and_missing_never_fail_verify(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, node: DestinationNode | None, state: str
) -> None:
    harness = _harness(tmp_path, monkeypatch)
    _requiring_reconciliation(harness, monkeypatch)
    asked = _reading_destination(harness, node=node)

    verified = _execute(harness, "verify", approved=harness.recorded_checksum)

    assert verified["outcome"] == "verified"
    assert verified["checks"] == ALL_CHECKS
    assert [entry["state"] for entry in verified["operations"]] == [state]
    assert len(asked) == 1
    assert TAMPERABLE_VALUE not in json.dumps(verified)
    assert harness.writes == []


def test_a_run_that_was_never_interrupted_gets_no_report(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    harness = _harness(tmp_path, monkeypatch)
    asked = _reading_destination(harness)

    verified = _execute(harness, "verify", approved=harness.recorded_checksum)

    assert "operations" not in verified
    assert asked == []


def test_a_failed_pre_write_check_fails_verify_and_produces_no_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    harness = _harness(tmp_path, monkeypatch)
    _requiring_reconciliation(harness, monkeypatch)
    asked = _reading_destination(harness)
    _break_lineage(harness)

    with pytest.raises(Exception) as refused:
        _execute(harness, "verify", approved=harness.recorded_checksum)

    assert failed_checks(refused.value) == ("lineage",)
    assert asked == []
    assert harness.writes == []


def test_a_failed_destination_read_fails_verify_without_its_text(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    harness = _harness(tmp_path, monkeypatch)
    _requiring_reconciliation(harness, monkeypatch)
    _reading_destination(harness, error=httpx.ConnectError(UNREACHABLE))

    with pytest.raises(DestinationReadError) as raised:
        _execute(harness, "verify", approved=harness.recorded_checksum)

    assert LEAKED_ADDRESS not in str(raised.value)
    assert harness.writes == []
