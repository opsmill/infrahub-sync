"""A plan holds no delete for a node that Infrahub created itself (spec 019 T008, FR-001, FR-003).

`derive_deletes` skips an unmatched destination node whose id is in the destination's built-in
set (`BuiltinNodeReadingDestination`), keeps the delete of an unmatched node created by hand, and
leaves a built-in node that a source record matches to the comparison, like any other node.

The plans run the real `Potenda` plan path over the fixture of `tests/data/second_plan/`. A
destination row marks a node as built-in with the node metadata Infrahub returns for it (creator
`__system__`, research R2) when its kind is in `INFRAHUB_BUILTIN_NODE_KINDS` (ADR 0043); the
stand-in client of `tests/plan/problem_plan.py` reads that key
as node metadata, not as model data.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

import pytest

from infrahub_sync.plan.derive import derive_deletes
from infrahub_sync.plan.write_surface import BuiltinNodeReadingDestination, destination_builtin_node_ids
from tests.plan.problem_plan import plan_fixture
from tests.plan.second_plan import kinds_of, load_second_plan_fixture, second_plan_destination, second_plan_records

if TYPE_CHECKING:
    from pathlib import Path

    from tests.plan.conftest import PlanProblemFixture

BUILTIN_METADATA = {
    "created_by": {"id": "__system__", "display_label": "[Infrahub System]", "__typename": "CoreAccount"}
}
ADMIN_METADATA = {"created_by": {"id": "account-admin", "display_label": "Admin", "__typename": "CoreAccount"}}

NAMESPACE_ID = "18d52a8a-0000-0000-0019-000000000001"
HAND_MADE_DEVICE_ID = "18d52a8a-0000-0000-0019-000000000091"
BUILTIN_DEVICE_ID = "18d52a8a-0000-0000-0019-000000000092"
IMPORTED_IPAM_ID = "18d52a8a-0000-0000-0019-000000000093"
SECOND_NAMESPACE_ID = "18d52a8a-0000-0000-0019-000000000094"


@pytest.fixture
def second_plan(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> PlanProblemFixture:
    """The fixture of `tests/data/second_plan/`, with the plan cache under `tmp_path`."""
    monkeypatch.setenv("INFRAHUB_SYNC_CACHE_DIR", str(tmp_path / "cache"))
    return load_second_plan_fixture()


def _device(local_id: str, name: str, description: str, metadata: dict[str, Any] | None) -> dict[str, Any]:
    row: dict[str, Any] = {"local_id": local_id, "name": name, "description": description}
    if metadata is not None:
        row["node_metadata"] = metadata
    return row


def test_an_unmatched_builtin_node_gets_no_delete(second_plan: PlanProblemFixture) -> None:
    planned = plan_fixture(
        second_plan,
        records=second_plan_records(second_plan, "devices"),
        destination=second_plan_destination(second_plan),
    )

    assert planned.destination.builtin_node_ids == frozenset({NAMESPACE_ID})
    assert kinds_of(planned.operations) == [("create", "DcimDevice"), ("create", "DcimDevice")]
    assert planned.report == []


def test_an_unmatched_node_created_by_hand_keeps_its_delete(second_plan: PlanProblemFixture) -> None:
    destination = second_plan_destination(second_plan)
    destination["DcimDevice"] = [_device(HAND_MADE_DEVICE_ID, "leaf-9", "added by hand", ADMIN_METADATA)]

    planned = plan_fixture(second_plan, records=second_plan_records(second_plan, "devices"), destination=destination)

    deletes = [operation for operation in planned.operations if operation.action == "delete"]
    assert [(operation.kind, operation.identity) for operation in deletes] == [("DcimDevice", {"name": "leaf-9"})]


def test_a_node_without_node_metadata_keeps_its_delete(second_plan: PlanProblemFixture) -> None:
    """A read without metadata (Infrahub before 1.7.0) gives no mark, so the delete stays."""
    destination = second_plan_destination(second_plan)
    destination["IpamNamespace"][0].pop("node_metadata")

    planned = plan_fixture(second_plan, records=second_plan_records(second_plan, "devices"), destination=destination)

    assert planned.destination.builtin_node_ids == frozenset()
    deletes = [operation for operation in planned.operations if operation.action == "delete"]
    assert [(operation.kind, operation.identity) for operation in deletes] == [("IpamNamespace", {"name": "default"})]


@pytest.mark.parametrize(
    ("kind", "row"),
    [
        (
            "IpamPrefix",
            {"prefix": "10.9.0.0/24", "description": "imported from NetBox", "ip_namespace": "default"},
        ),
        (
            "IpamIPAddress",
            {"address": "10.9.0.1/24", "description": "imported from NetBox", "ip_namespace": "default"},
        ),
    ],
    ids=["prefix", "ip-address"],
)
def test_an_imported_ipam_node_created_by_system_keeps_its_delete(
    second_plan: PlanProblemFixture, kind: str, row: dict[str, Any]
) -> None:
    """ADR 0043 amendment: Infrahub 1.11.9 records `__system__` as the creator of imported IPAM nodes.

    The archived import run of `dev/specs/archive/009-import-lineage/opsmill-implement-report.md`
    found 274 NetBox-sourced `IpamPrefix`, `IpamIPAddress` and `IpamAggregate` nodes with that
    creator. Their kinds are not in `INFRAHUB_BUILTIN_NODE_KINDS`, so when the source no longer
    holds such a node, the plan keeps its delete.
    """
    destination = second_plan_destination(second_plan)
    destination[kind] = [{"local_id": IMPORTED_IPAM_ID, **row, "node_metadata": BUILTIN_METADATA}]

    planned = plan_fixture(
        second_plan, records=second_plan_records(second_plan, "devices", "no_vrf"), destination=destination
    )

    assert planned.destination.builtin_node_ids == frozenset({NAMESPACE_ID})
    deletes = [operation for operation in planned.operations if operation.action == "delete"]
    assert [operation.kind for operation in deletes] == [kind]
    (identity,) = [operation.identity for operation in deletes]
    assert {key: value for key, value in identity.items() if key != "ip_namespace"} == {
        key: value for key, value in row.items() if key in {"prefix", "address"}
    }


def test_a_matched_builtin_node_is_compared_like_any_other(second_plan: PlanProblemFixture) -> None:
    """FR-003: the mark only removes deletes; a matched node is compared and gets its update.

    The device carries the creator `__system__`, but its kind is not in
    `INFRAHUB_BUILTIN_NODE_KINDS`, so it is not built in; the matched `default` namespace is
    built in and equal to its source record, so it gets no operation.
    """
    destination = second_plan_destination(second_plan)
    destination["DcimDevice"] = [_device(BUILTIN_DEVICE_ID, "leaf-1", "set by Infrahub", BUILTIN_METADATA)]
    records = second_plan_records(second_plan, "devices")
    records["IpamNamespace"] = [{"id": 99, "name": "default"}]

    planned = plan_fixture(second_plan, records=records, destination=destination)

    assert planned.destination.builtin_node_ids == frozenset({NAMESPACE_ID})
    assert kinds_of(planned.operations) == [("create", "DcimDevice"), ("update", "DcimDevice")]
    (update,) = [operation for operation in planned.operations if operation.action == "update"]
    assert update.identity == {"name": "leaf-1"}
    assert update.destination_id == BUILTIN_DEVICE_ID
    assert update.payload == {"name": "leaf-1", "description": "first leaf"}


def test_the_mark_does_not_enter_the_destination_model_data(second_plan: PlanProblemFixture) -> None:
    planned = plan_fixture(
        second_plan,
        records=second_plan_records(second_plan, "devices"),
        destination=second_plan_destination(second_plan),
    )

    (namespace,) = planned.destination.get_all("IpamNamespace")
    assert "node_metadata" not in namespace.model_dump()


def test_derive_deletes_logs_one_info_line_with_the_count(
    second_plan: PlanProblemFixture, caplog: pytest.LogCaptureFixture
) -> None:
    destination = second_plan_destination(second_plan)
    destination["IpamNamespace"].append(
        {"local_id": SECOND_NAMESPACE_ID, "name": "second", "node_metadata": BUILTIN_METADATA}
    )
    planned = plan_fixture(second_plan, records=second_plan_records(second_plan, "devices"), destination=destination)
    # The plan above logs the same line; only the call below is under test.
    caplog.clear()

    with caplog.at_level(logging.INFO, logger="infrahub_sync.plan.derive"):
        deletes = derive_deletes(
            kinds=second_plan.order,
            source_adapter=planned.source,
            destination_adapter=planned.destination,
            config=planned.potenda.config,
            tier_of=lambda _kind: 0,
            destination_full_extract=True,
            builtin_node_ids=destination_builtin_node_ids(planned.destination),
        )

    assert deletes == []
    messages = [record.getMessage() for record in caplog.records if record.name == "infrahub_sync.plan.derive"]
    assert [message for message in messages if "Infrahub created itself" in message] == [
        "Plan: 2 destination node(s) that Infrahub created itself and no source record matches get no delete"
    ]


def test_derive_deletes_without_a_builtin_set_keeps_every_delete(second_plan: PlanProblemFixture) -> None:
    """A destination without the protocol keeps today's behavior."""
    planned = plan_fixture(
        second_plan,
        records=second_plan_records(second_plan, "devices"),
        destination=second_plan_destination(second_plan),
    )

    deletes = derive_deletes(
        kinds=second_plan.order,
        source_adapter=planned.source,
        destination_adapter=planned.destination,
        config=planned.potenda.config,
        tier_of=lambda _kind: 0,
        destination_full_extract=True,
    )

    assert [(operation.kind, operation.identity) for operation in deletes] == [("IpamNamespace", {"name": "default"})]


@pytest.mark.parametrize(
    ("destination", "expected"),
    [
        (object(), frozenset()),
        (type("Wrong", (), {"builtin_node_ids": ["a"]})(), frozenset()),
        (type("Mixed", (), {"builtin_node_ids": frozenset({"a", 1})})(), frozenset({"a"})),
    ],
    ids=["no-protocol", "not-a-set", "non-string-ids"],
)
def test_the_builtin_set_is_checked_before_use(destination: object, expected: frozenset[str]) -> None:
    assert destination_builtin_node_ids(destination) == expected


def test_the_infrahub_adapter_implements_the_protocol(second_plan: PlanProblemFixture) -> None:
    planned = plan_fixture(
        second_plan,
        records=second_plan_records(second_plan, "devices"),
        destination=second_plan_destination(second_plan),
    )

    assert isinstance(planned.destination, BuiltinNodeReadingDestination)
