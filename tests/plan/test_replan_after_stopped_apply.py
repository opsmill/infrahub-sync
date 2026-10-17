"""A new plan after a stopped apply holds only the operations that remain (spec 009 T081, SC-004).

An apply of the N operations of the fixture plan stops at operation K: the destination keeps
the K - 1 operations written before it. A new plan with no source change, made against that
destination, has N - (K - 1) operations: the failed operation and the operations not
attempted, and none for a node already written.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest

from infrahub_sync.plan.errors import OperationApplyFailedError
from tests.adapters.test_infrahub_planned_write import engine_over
from tests.plan.problem_fixtures import Variant, destination_records, source_records
from tests.plan.problem_plan import RUN_ID, plan_fixture
from tests.plan.stopped_apply import RejectingDestination, destination_after
from tests.plan.test_plan_format_3 import RecordingOwnership

if TYPE_CHECKING:
    from pathlib import Path

    from infrahub_sync.plan.models import PlannedOperation


def _change(operation: PlannedOperation) -> tuple[str, str, Any]:
    """What an operation changes, independent of the run that planned it."""
    return operation.action, operation.kind, operation.identity


# Operation 1 writes nothing before it; operation 4 follows the skipped delete; operation 8
# follows writes of nodes with a relationship to a written peer.
@pytest.mark.parametrize("reject_at", [1, 4, 8])
def test_a_new_plan_holds_only_the_operations_the_stopped_apply_did_not_write(tmp_path: Path, reject_at: int) -> None:
    first = plan_fixture(tmp_path / "first", Variant.NO_PROBLEMS)
    total = len(first.operations)
    destination = RejectingDestination(reject_at)

    with pytest.raises(OperationApplyFailedError) as stopped:
        engine_over(first.run_dir, destination, run_id=RUN_ID).apply_plan(
            ownership=RecordingOwnership(),
            config_version=first.manifest.config_version,
            confirm_left_out=False,
        )

    record = stopped.value.apply_record
    assert record is not None
    assert len(record.applied_operations) == reject_at - 1
    failed = destination.attempted[-1]
    assert record.failed_operation == failed
    second = plan_fixture(
        tmp_path / "second",
        Variant.NO_PROBLEMS,
        destination=destination_after(
            first.source, destination.written, destination_records(source_records(Variant.NO_PROBLEMS))
        ),
    )

    assert len(second.operations) == total - (reject_at - 1)
    remaining = [_change(item) for item in second.operations]
    written = [_change(item) for item in destination.written]
    by_id = {item.operation_id: item for item in first.operations}
    assert _change(by_id[failed]) in remaining, "The failed operation is planned again."
    not_attempted = [_change(item) for item in first.operations if item.operation_id not in set(destination.attempted)]
    assert not_attempted, "The apply stopped before the end of the plan."
    assert all(change in remaining for change in not_attempted), "Every operation not attempted is planned again."
    assert not [change for change in written if change in remaining], "No node already written is planned again."
