"""The problem pass groups merged identities by the real destination key only (review #4).

`identities_merge_in_destination` is listed when two or more source records with different
identities would be stored as one Infrahub node. The grouping projects each identity onto the
destination key closest to it. That is only true of Infrahub when the key is a strict subset of
the configured identity: a key with a field that the identity does not hold would group records
that Infrahub keeps apart. And a group of updates only holds records that already exist in
Infrahub as separate nodes, so it does not merge anything: only a group with a create does.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

from infrahub_sync.plan.identity import operation_id
from infrahub_sync.plan.models import PlannedOperation
from infrahub_sync.plan.problems import _identity_groups  # noqa: PLC2701 - the grouping under test is private
from tests.plan.problem_fixtures import Variant, source_records
from tests.plan.problem_plan import plan_fixture

if TYPE_CHECKING:
    from pathlib import Path


def _operation(action: str, kind: str, identity: dict[str, Any], node: int = 1) -> PlannedOperation:
    return PlannedOperation(
        operation_id=operation_id(action, kind, identity),
        action=action,  # ty: ignore[invalid-argument-type]
        kind=kind,
        identity=identity,
        tier=0,
        payload=dict(identity),
        destination_id=f"18d52a8a-0000-0000-0000-00000000002{node}" if action == "update" else None,
    )


def _node(human_friendly_id: list[str]) -> Any:  # noqa: ANN401 - a schema node stand-in
    names = sorted({component.split("__")[0] for component in human_friendly_id})
    return SimpleNamespace(
        kind="",
        human_friendly_id=human_friendly_id,
        uniqueness_constraints=[],
        attributes=[
            SimpleNamespace(name=name, unique=False, read_only=False, computed_attribute=None) for name in names
        ],
        relationships=[],
        inherit_from=(),
    )


def _merge_groups(operations: list[PlannedOperation], schema: dict[str, Any]) -> list[set[str]]:
    groups = _identity_groups(
        operations=operations, source=None, config=None, destination=SimpleNamespace(schema=schema)
    )
    return [
        {operation.operation_id for operation in group.operations}
        for problem_type, group in groups
        if problem_type == "identities_merge_in_destination"
    ]


def test_a_create_and_an_update_that_one_node_holds_are_grouped() -> None:
    update = _operation("update", "LocationRack", {"name": "comms-closet", "facility_id": "F1"})
    create = _operation("create", "LocationRack", {"name": "comms-closet", "facility_id": "F2"})

    groups = _merge_groups([update, create], {"LocationRack": _node(["name__value"])})

    assert groups == [{update.operation_id, create.operation_id}]


def test_updates_only_are_not_grouped() -> None:
    """Both records already exist in Infrahub as separate nodes, so nothing merges."""
    first = _operation("update", "LocationRack", {"name": "comms-closet", "facility_id": "F1"}, node=1)
    second = _operation("update", "LocationRack", {"name": "comms-closet", "facility_id": "F2"}, node=2)

    assert _merge_groups([first, second], {"LocationRack": _node(["name__value"])}) == []


def test_a_key_that_is_not_a_subset_of_the_identity_groups_nothing() -> None:
    """The key `name, site` tells apart records the identity `name, serial` cannot group by `name` alone."""
    first = _operation("create", "DcimDevice", {"name": "edge", "serial": "E1"})
    second = _operation("create", "DcimDevice", {"name": "edge", "serial": "E2"})

    groups = _merge_groups([first, second], {"DcimDevice": _node(["name__value", "site__name__value"])})

    assert groups == []


def test_two_updates_with_one_destination_key_plan_normally(tmp_path: Path) -> None:
    """The false positive of review #4: two racks Infrahub already holds are updated, not left out."""
    records = source_records(Variant.NO_PROBLEMS)
    records["LocationRack"] += [
        {"id": 21, "name": "comms-closet", "facility_id": "F1", "description": "first floor"},
        {"id": 22, "name": "comms-closet", "facility_id": "F2", "description": "second floor"},
    ]
    destination = {
        "LocationRack": [
            {"local_id": f"18d52a8a-0000-0000-0000-00000000002{index}", "name": "comms-closet", "facility_id": facility}
            for index, facility in ((1, "F1"), (2, "F2"))
        ]
    }

    planned = plan_fixture(tmp_path / "run", records=records, destination=destination)

    assert planned.report.problems == []
    updates = sorted(
        operation.identity["facility_id"]
        for operation in planned.operations
        if operation.kind == "LocationRack" and operation.action == "update"
    )
    assert updates == ["F1", "F2"]
