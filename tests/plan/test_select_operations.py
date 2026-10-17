"""`select_operations(operations, report)` applies the plan report (spec 009 T033, research R7 point 3)."""

from __future__ import annotations

import copy
from typing import Any

from infrahub_sync.plan.identity import operation_id
from infrahub_sync.plan.left_out_peer import holds_left_out_peer, left_out_peer_pair
from infrahub_sync.plan.models import (
    KeptRecord,
    LeftOutRecord,
    PlannedOperation,
    PlanRecordReference,
    PlanReport,
    RelationshipReference,
)
from infrahub_sync.plan.problems import select_operations

CORE_1 = {"name": "core-1", "serial": "C1"}
LEAF_1 = {"name": "leaf-1", "serial": "L1"}
DESTINATION_ID = "18d52a8a-7e7d-9bf5-3967-c51149d169da"


def _operation(
    action: str,
    kind: str,
    identity: dict[str, Any],
    *,
    relationships: list[RelationshipReference] | None = None,
    payload: dict[str, Any] | None = None,
) -> PlannedOperation:
    return PlannedOperation(
        operation_id=operation_id(action, kind, identity),
        action=action,  # ty: ignore[invalid-argument-type]
        kind=kind,
        identity=identity,
        tier=0,
        payload=None if action == "delete" else (payload if payload is not None else _identity_payload(identity)),
        relationships=relationships,
        destination_id=DESTINATION_ID if action == "update" else None,
    )


def _identity_payload(identity: dict[str, Any]) -> dict[str, Any]:
    """The identity's own values; a reference component travels as a relationship instead."""
    return {name: value for name, value in identity.items() if not isinstance(value, dict)}


def _device_ref(*peers: dict[str, Any], cardinality: str = "one", field: str = "device") -> RelationshipReference:
    return RelationshipReference(
        field=field,
        peer_kind="DcimDevice",
        cardinality=cardinality,  # ty: ignore[invalid-argument-type]
        peers=list(peers),
    )


def _not_loaded_device() -> dict[str, Any]:
    return left_out_peer_pair(peer_kind="DcimDevice", cause="not_loaded", source_id="30")["identity"]


def _report(*, kept: list[KeptRecord] | None = None) -> PlanReport:
    core = PlanRecordReference(kind="DcimDevice", identity=CORE_1, source_id="30")
    interface = PlanRecordReference(
        kind="InterfacePhysical",
        identity={"device": {"peer_kind": "DcimDevice", "identity": CORE_1}, "name": "eth0"},
        source_id="40",
    )
    return PlanReport(
        left_out=[
            LeftOutRecord(record=core, cause="has_problem", problem_types=["wrong_value_type"], loaded=False),
            LeftOutRecord(
                record=interface, cause="depends_on_left_out", depends_on=core, relationship="device", loaded=True
            ),
        ],
        kept=kept or [],
    )


def _kept(address: str, *, relationship: str = "device", only: bool = False) -> KeptRecord:
    return KeptRecord(
        record=PlanRecordReference(kind="IpamIPAddress", identity={"address": address}, source_id=address),
        cause="peer_left_out",
        relationship=relationship,
        left_out_peer=PlanRecordReference(kind="DcimDevice", identity=CORE_1, source_id="30"),
        relationship_only_update=only,
    )


def test_every_create_and_update_of_a_left_out_record_is_removed() -> None:
    interface_identity = {
        "device": left_out_peer_pair(peer_kind="DcimDevice", cause="not_loaded", source_id="30"),
        "name": "eth0",
    }
    operations = [
        _operation("update", "DcimDevice", CORE_1),
        _operation(
            "create", "InterfacePhysical", interface_identity, relationships=[_device_ref(_not_loaded_device())]
        ),
        _operation("create", "DcimDevice", LEAF_1),
    ]

    selected = select_operations(operations, _report())

    assert selected == [operations[2]]


def test_a_delete_of_a_left_out_record_is_removed_so_it_stays_unchanged() -> None:
    operations = [_operation("delete", "DcimDevice", CORE_1), _operation("delete", "DcimDevice", LEAF_1)]

    assert select_operations(operations, _report()) == [operations[1]]


def test_on_a_create_a_cardinality_one_relationship_to_a_left_out_peer_is_absent() -> None:
    create = _operation(
        "create",
        "IpamIPAddress",
        {"address": "10.0.0.5/32"},
        relationships=[_device_ref(_not_loaded_device()), _device_ref({"name": "default"}, field="vrf")],
    )

    (selected,) = select_operations([create], _report(kept=[_kept("10.0.0.5/32")]))

    assert [reference.field for reference in selected.relationships or []] == ["vrf"]


def test_on_a_create_a_cardinality_many_relationship_keeps_the_other_peers() -> None:
    create = _operation(
        "create",
        "IpamIPAddress",
        {"address": "10.0.0.5/32"},
        relationships=[_device_ref(CORE_1, LEAF_1, cardinality="many", field="devices")],
    )

    (selected,) = select_operations([create], _report(kept=[_kept("10.0.0.5/32", relationship="devices")]))

    (reference,) = selected.relationships or []
    assert reference.peers == [LEAF_1]


def test_on_an_update_the_whole_relationship_is_removed_for_cardinality_one_and_many() -> None:
    update = _operation(
        "update",
        "IpamIPAddress",
        {"address": "10.0.0.5/32"},
        payload={"address": "10.0.0.5/32", "description": "changed"},
        relationships=[
            _device_ref(_not_loaded_device()),
            _device_ref(CORE_1, LEAF_1, cardinality="many", field="devices"),
            _device_ref({"name": "default"}, field="vrf"),
        ],
    )
    report = _report(kept=[_kept("10.0.0.5/32"), _kept("10.0.0.5/32", relationship="devices")])

    (selected,) = select_operations([update], report)

    assert [reference.field for reference in selected.relationships or []] == ["vrf"]
    assert selected.payload == update.payload


def test_an_update_whose_only_change_was_that_relationship_is_removed() -> None:
    update = _operation(
        "update", "IpamIPAddress", {"address": "10.0.0.5/32"}, relationships=[_device_ref(_not_loaded_device())]
    )

    assert select_operations([update], _report(kept=[_kept("10.0.0.5/32", only=True)])) == []


def test_a_peer_not_in_the_source_is_removed_from_the_operation() -> None:
    vrf_placeholder = left_out_peer_pair(peer_kind="IpamVRF", cause="not_in_source", source_id="998")["identity"]
    create = _operation(
        "create",
        "IpamIPAddress",
        {"address": "10.0.0.9/32"},
        relationships=[
            RelationshipReference(field="vrf", peer_kind="IpamVRF", cardinality="one", peers=[vrf_placeholder])
        ],
    )
    kept = KeptRecord(
        record=PlanRecordReference(kind="IpamIPAddress", identity={"address": "10.0.0.9/32"}, source_id="82"),
        cause="peer_not_found_in_source",
        relationship="vrf",
        left_out_peer=PlanRecordReference(kind="IpamVRF", source_id="998"),
    )

    (selected,) = select_operations([create], PlanReport(kept=[kept]))

    assert selected.relationships is None


def test_no_placeholder_remains_and_the_inputs_are_not_changed() -> None:
    operations = [
        _operation(
            "create", "IpamIPAddress", {"address": "10.0.0.5/32"}, relationships=[_device_ref(_not_loaded_device())]
        ),
        _operation(
            "create",
            "InterfacePhysical",
            {"device": left_out_peer_pair(peer_kind="DcimDevice", cause="not_loaded", source_id="30"), "name": "eth0"},
            relationships=[_device_ref(_not_loaded_device())],
        ),
        _operation("create", "DcimDevice", LEAF_1),
    ]
    report = _report(kept=[_kept("10.0.0.5/32")])
    operations_before = copy.deepcopy([operation.model_dump() for operation in operations])
    report_before = report.model_dump()

    selected = select_operations(operations, report)

    assert not any(holds_left_out_peer(operation) for operation in selected)
    assert [operation.model_dump() for operation in operations] == operations_before
    assert report.model_dump() == report_before


def test_no_delete_of_a_kind_with_a_left_out_record_of_unknown_identity() -> None:
    """Review M4 (FR-008): the plan cannot tell which Infrahub node such a record is, so it deletes none of its kind."""
    unknown = LeftOutRecord(
        record=PlanRecordReference(kind="DcimDevice", identity=None, source_id="36"),
        cause="has_problem",
        problem_types=["peer_not_found_in_source"],
        loaded=False,
    )
    operations = [
        _operation("delete", "DcimDevice", LEAF_1),
        _operation("create", "DcimDevice", {"name": "leaf-3", "serial": "L3"}),
        _operation("delete", "LocationSite", {"name": "paris"}),
    ]

    selected = select_operations(operations, PlanReport(left_out=[unknown]))

    assert selected == operations[1:]
