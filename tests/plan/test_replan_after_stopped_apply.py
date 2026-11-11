"""Planning again after an apply that stopped partway (spec 019 T058, FR-022, SC-004).

The testing release cannot resume a stopped apply: the team plans again. The new plan compares
the source with the destination, so it must hold only the operations that the destination does
not already match:

- no operation for the nodes the stopped apply wrote
- the operation that failed, and every operation the apply did not attempt
- minus any of those that the destination now matches, for example because someone created the
  node by hand between the two plans

The first plan is the fixture's valid records (`Variant.NO_PROBLEMS`) against an empty
destination: ten creates. Its apply runs through `Potenda.apply_plan` against a destination
that writes a known number of operations and rejects the next one. The test stops it twice:
after four operations (nodes without relationships) and after eight (devices and interfaces,
whose identities hold a peer). The destination of the second plan holds the nodes of the
written operations, as model data built from the source records they came from. The source is
unchanged, so SC-004's count is the first count minus the operations written.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest
from infrahub_sdk.exceptions import GraphQLError

from infrahub_sync.plan.errors import OperationApplyFailedError
from tests.plan.conftest import Variant
from tests.plan.ownership_fixtures import granted_ownership
from tests.plan.problem_plan import plan_fixture

if TYPE_CHECKING:
    from pathlib import Path

    from infrahub_sync.plan.models import PlannedOperation
    from tests.plan.conftest import PlanProblemFixture
    from tests.plan.problem_plan import PlannedFixture

FIRST_RUN_ID = "20261111T120000-01900058"
SECOND_RUN_ID = "20261111T130000-01900058"

# The apply writes this many operations and stops at the next one.
STOP_POINTS = (4, 8)


class StoppingDestination:
    """A destination that writes `written` operations, then rejects the next one."""

    def __init__(self, written: int) -> None:
        self.written = written
        self.dispatched: list[PlannedOperation] = []

    def new_peer_resolver(self) -> object:  # noqa: PLR6301
        """The per-apply resolver factory; this double does not read it."""
        return object()

    def apply_planned_operation(self, *, operation: PlannedOperation, peers: Any) -> str:  # noqa: ANN401
        """Record the operation, or reject it once `written` operations are written."""
        _ = peers
        if len(self.dispatched) >= self.written:
            raise GraphQLError([{"message": "planned is not a valid value for status"}])
        self.dispatched.append(operation)
        return f"node-{len(self.dispatched)}"


def _key(operation: PlannedOperation) -> tuple[str, str, str]:
    """An operation's action, kind and identity, comparable across two plans."""
    return operation.action, operation.kind, repr(sorted(operation.identity.items()))


def _destination_node(planned: PlannedFixture, operation: PlannedOperation, index: int) -> tuple[str, dict[str, Any]]:
    """The model data Infrahub holds after a create wrote `operation`.

    The node is the source record the operation came from: the one record of the kind whose
    attributes equal the operation's payload.
    """
    payload = operation.payload or {}
    matches = [
        record
        for record in planned.source.get_all(operation.kind)
        if all(record.model_dump().get(name) == value for name, value in payload.items())
    ]
    assert len(matches) == 1, f"one source record must match {operation.kind} {payload}"
    record = matches[0]
    data = {**record.get_identifiers(), **record.get_attrs()}
    data["local_id"] = f"18d52a8a-0000-0000-0058-{index:012d}"
    return operation.kind, data


def _destination_after(planned: PlannedFixture, written: list[PlannedOperation]) -> dict[str, list[dict[str, Any]]]:
    """The nodes Infrahub holds after the `written` creates, by kind."""
    nodes: dict[str, list[dict[str, Any]]] = {}
    for index, operation in enumerate(written):
        kind, data = _destination_node(planned, operation, index)
        nodes.setdefault(kind, []).append(data)
    return nodes


def _stopped_apply(planned: PlannedFixture, written: int) -> tuple[list[PlannedOperation], OperationApplyFailedError]:
    """Apply the plan through a destination that stops after `written` operations."""
    stopping = StoppingDestination(written)
    planned.potenda.destination = stopping  # ty: ignore[invalid-assignment]
    with pytest.raises(OperationApplyFailedError) as caught:
        planned.potenda.apply_plan(ownership=granted_ownership())
    return stopping.dispatched, caught.value


@pytest.fixture(params=STOP_POINTS, ids=lambda written: f"stop-after-{written}")
def stop_after(request: pytest.FixtureRequest) -> int:
    """The number of operations the stopped apply writes."""
    return request.param


@pytest.fixture
def first_plan(plan_problems: PlanProblemFixture, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> PlannedFixture:
    """The plan of the valid records against an empty destination: one create per record."""
    monkeypatch.setenv("INFRAHUB_SYNC_CACHE_DIR", str(tmp_path / "cache"))
    planned = plan_fixture(plan_problems, Variant.NO_PROBLEMS, destination={}, run_id=FIRST_RUN_ID)
    assert len(planned.operations) > max(STOP_POINTS) + 1, "the apply must stop before the last operation"
    assert {operation.action for operation in planned.operations} == {"create"}
    return planned


def test_the_new_plan_holds_only_the_operations_the_stopped_apply_did_not_write(
    plan_problems: PlanProblemFixture, first_plan: PlannedFixture, stop_after: int
) -> None:
    """SC-004: the count is the first plan's count minus the operations written."""
    operations = first_plan.operations
    written, stopped = _stopped_apply(first_plan, stop_after)
    record = stopped.apply_record
    assert list(record.applied_operations) == [operation.operation_id for operation in operations[:stop_after]]
    assert record.failed_operation == operations[stop_after].operation_id
    assert record.operations_total == len(operations)

    second = plan_fixture(
        plan_problems,
        Variant.NO_PROBLEMS,
        destination=_destination_after(first_plan, written),
        run_id=SECOND_RUN_ID,
    )

    assert len(second.operations) == len(operations) - len(record.applied_operations)
    remaining = [_key(operation) for operation in operations[stop_after:]]
    assert sorted(_key(operation) for operation in second.operations) == sorted(remaining)
    written_keys = {_key(operation) for operation in written}
    assert not written_keys & {_key(operation) for operation in second.operations}, (
        "no operation for a node the stopped apply wrote"
    )


def test_the_new_plan_leaves_out_a_remaining_operation_the_destination_now_matches(
    plan_problems: PlanProblemFixture, first_plan: PlannedFixture, stop_after: int
) -> None:
    """FR-022: an operation the apply did not attempt drops out when the destination matches it."""
    operations = first_plan.operations
    written, _stopped = _stopped_apply(first_plan, stop_after)
    # Between the two plans, someone creates the last record of the plan by hand.
    created_by_hand = operations[-1]
    destination = _destination_after(first_plan, [*written, created_by_hand])

    second = plan_fixture(plan_problems, Variant.NO_PROBLEMS, destination=destination, run_id=SECOND_RUN_ID)

    assert len(second.operations) == len(operations) - stop_after - 1
    remaining = [_key(operation) for operation in operations[stop_after:-1]]
    assert sorted(_key(operation) for operation in second.operations) == sorted(remaining)
