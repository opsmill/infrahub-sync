"""The stand-in destination and the second-plan fixture (spec 019 T006, T007).

These tests check the helpers of `tests/plan/second_plan.py` on what the code does before
User Story 1: the first plan of the fixture creates every source record and deletes the
`default` namespace, the apply writes the creates, and the second plan holds only that delete,
because `Potenda.apply_plan` does not write a delete. User Story 1 removes the delete; its tests
in `tests/plan/test_second_plan.py` then require an empty second plan.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest

from infrahub_sync.plan.identity import operation_id
from infrahub_sync.plan.models import PlannedOperation
from tests.plan.problem_plan import plan_fixture
from tests.plan.second_plan import (
    ApplyingDestination,
    PeerMissingError,
    kinds_of,
    load_second_plan_fixture,
    second_plan_destination,
    second_plan_records,
)

if TYPE_CHECKING:
    from pathlib import Path

    from tests.plan.conftest import PlanProblemFixture
    from tests.plan.problem_plan import PlannedFixture

FIRST_RUN_ID = "20261116T100000-01900006"
SECOND_RUN_ID = "20261116T110000-01900006"

DEFAULT_NAMESPACE_DELETE = ("delete", "IpamNamespace")


@pytest.fixture
def second_plan(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> PlanProblemFixture:
    """The fixture of `tests/data/second_plan/`, with the plan cache under `tmp_path`."""
    monkeypatch.setenv("INFRAHUB_SYNC_CACHE_DIR", str(tmp_path / "cache"))
    return load_second_plan_fixture()


@pytest.fixture
def first_plan(second_plan: PlanProblemFixture) -> PlannedFixture:
    """The first plan of every group against a destination that holds the `default` namespace."""
    return plan_fixture(
        second_plan,
        records=second_plan_records(second_plan),
        destination=second_plan_destination(second_plan),
        run_id=FIRST_RUN_ID,
    )


def update_of(kind: str, identity: dict[str, Any], **values: Any) -> PlannedOperation:  # noqa: ANN401
    """An update of one node, with its derived identifier."""
    return PlannedOperation(
        operation_id=operation_id("update", kind, identity),
        action="update",
        kind=kind,
        identity=identity,
        tier=0,
        **values,
    )


def test_the_first_plan_creates_every_source_record_and_deletes_the_default_namespace(
    second_plan: PlanProblemFixture, first_plan: PlannedFixture
) -> None:
    records = second_plan_records(second_plan)
    creates = sorted(("create", kind) for kind, rows in records.items() for _ in rows)

    assert first_plan.report == []
    assert kinds_of(first_plan.operations) == sorted([*creates, DEFAULT_NAMESPACE_DELETE])


def test_a_second_plan_after_the_apply_holds_only_the_default_namespace_delete(
    second_plan: PlanProblemFixture, first_plan: PlannedFixture
) -> None:
    """Every create matches what the apply wrote: LAG members, prefixes and addresses, tagged VLANs."""
    applying = ApplyingDestination.for_plan(first_plan, second_plan_destination(second_plan))
    destination = applying.apply(first_plan)

    second = plan_fixture(
        second_plan, records=second_plan_records(second_plan), destination=destination, run_id=SECOND_RUN_ID
    )

    assert len(applying.applied) == len(first_plan.operations) - 1
    assert second.report == []
    assert kinds_of(second.operations) == [DEFAULT_NAMESPACE_DELETE]
    (delete,) = second.operations
    assert delete.identity == {"name": "default"}


def test_the_apply_writes_peers_as_unique_ids(second_plan: PlanProblemFixture, first_plan: PlannedFixture) -> None:
    destination = ApplyingDestination.for_plan(first_plan, second_plan_destination(second_plan)).apply(first_plan)

    interfaces = {(row["device"], row["name"]): row for row in destination["InterfacePhysical"]}
    assert interfaces["leaf-1", "eth0"]["bundle"] == "ae0__leaf-1"
    assert "bundle" not in interfaces["leaf-1", "eth2"]
    assert interfaces["leaf-2", "eth1"]["tagged_vlan"] == ["100", "200", "300"]
    assert interfaces["leaf-2", "eth2"]["tagged_vlan"] == []
    assert {row["ip_namespace"] for row in destination["IpamPrefix"]} == {"default"}


def test_an_update_replaces_a_one_peer_relationship_and_a_peer_set(
    second_plan: PlanProblemFixture, first_plan: PlannedFixture
) -> None:
    applying = ApplyingDestination.for_plan(first_plan, second_plan_destination(second_plan))
    destination = applying.apply(first_plan)
    (node,) = [row for row in destination["InterfacePhysical"] if (row["device"], row["name"]) == ("leaf-2", "eth0")]
    device = {"peer_kind": "DcimDevice", "identity": {"name": "leaf-2"}}

    applying.apply_planned_operation(
        operation=update_of(
            "InterfacePhysical",
            {"name": "eth0", "device": device},
            payload={"name": "eth0", "description": "moved"},
            relationships=[
                {"field": "device", "peer_kind": "DcimDevice", "cardinality": "one", "peers": [{"name": "leaf-2"}]},
                {"field": "tagged_vlan", "peer_kind": "IpamVLAN", "cardinality": "many", "peers": [{"vlan_id": 300}]},
            ],
            destination_id=node["local_id"],
        ),
        peers=applying.new_peer_resolver(),
    )

    (updated,) = [
        row
        for row in applying.destination_data()["InterfacePhysical"]
        if (row["device"], row["name"]) == ("leaf-2", "eth0")
    ]
    assert updated["description"] == "moved"
    assert updated["tagged_vlan"] == ["300"]


def test_a_peer_the_destination_does_not_hold_fails_the_operation(
    second_plan: PlanProblemFixture, first_plan: PlannedFixture
) -> None:
    applying = ApplyingDestination.for_plan(first_plan, second_plan_destination(second_plan))
    applying.apply(first_plan)
    (node,) = [
        row
        for row in applying.destination_data()["InterfacePhysical"]
        if (row["device"], row["name"]) == ("leaf-1", "eth2")
    ]
    device = {"peer_kind": "DcimDevice", "identity": {"name": "leaf-1"}}
    lag = {"name": "ae9", "device": device}

    with pytest.raises(PeerMissingError, match="InterfaceLag"):
        applying.apply_planned_operation(
            operation=update_of(
                "InterfacePhysical",
                {"name": "eth2", "device": device},
                payload={"name": "eth2"},
                relationships=[
                    {"field": "device", "peer_kind": "DcimDevice", "cardinality": "one", "peers": [{"name": "leaf-1"}]},
                    {"field": "bundle", "peer_kind": "InterfaceLag", "cardinality": "one", "peers": [lag]},
                ],
                destination_id=node["local_id"],
            ),
            peers=applying.new_peer_resolver(),
        )
