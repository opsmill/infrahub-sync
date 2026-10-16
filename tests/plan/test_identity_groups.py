"""The identity checks return every group of records that one destination key cannot tell apart.

Spec 009 research R8: `refuse_destination_identity_collisions` and
`refuse_source_identity_collisions` are split into a function that returns the groups and a
function that raises when there is one, and `_warn_identity_finer_than_destination_key`
returns its merge groups. The problem pass leaves out every record of every group, so
`warn_missing_convergence_key` on the selected operations finds nothing.

The records follow the T001 fixture (`tests/plan/problem_fixtures.py`): two `DcimDevice`
`edge` creates share the human-friendly ID `name`, and two `LocationRack` `comms-closet`
records, one an update and one a create, are stored as one Infrahub node.
"""

from __future__ import annotations

import logging
from types import SimpleNamespace
from typing import Any

import pytest

from infrahub_sync.plan.canonical import canonical_json_bytes
from infrahub_sync.plan.derive import (
    _warn_identity_finer_than_destination_key,  # noqa: PLC2701 - the merge-group check under test is private.
    warn_missing_convergence_key,
)
from infrahub_sync.plan.errors import DestinationIdentityCollisionError
from infrahub_sync.plan.identity import canonical_identity, operation_id
from infrahub_sync.plan.keying import (
    IdentityGroup,
    destination_identity_collision_groups,
    refuse_destination_identity_collisions,
    refuse_source_identity_collisions,
    source_identity_collision_groups,
)
from infrahub_sync.plan.models import PlannedOperation


def _operation(action: str, kind: str, identity: dict[str, Any], **payload: Any) -> PlannedOperation:  # noqa: ANN401
    return PlannedOperation(
        operation_id=operation_id(action, kind, identity),
        action=action,  # ty: ignore[invalid-argument-type]
        kind=kind,
        identity=identity,
        tier=0,
        payload={**identity, **payload},
        destination_id="18d52a8a-0000-0000-0000-000000000021" if action == "update" else None,
    )


def _node(human_friendly_id: list[str], *, unique: tuple[str, ...] = (), inherit_from: tuple[str, ...] = ()) -> Any:  # noqa: ANN401
    """A destination schema node whose human-friendly ID attributes are writable."""
    names = sorted({*(component.split("__")[0] for component in human_friendly_id), *unique})
    return SimpleNamespace(
        kind="",
        human_friendly_id=human_friendly_id,
        uniqueness_constraints=[],
        attributes=[
            SimpleNamespace(name=name, unique=name in unique, read_only=False, computed_attribute=None)
            for name in names
        ],
        relationships=[],
        inherit_from=inherit_from,
    )


SCHEMA: dict[str, Any] = {
    "LocationSite": _node(["name__value"]),
    "LocationRack": _node(["name__value"]),
    "DcimDevice": _node(["name__value"]),
}

EDGE_1 = _operation("create", "DcimDevice", {"name": "edge", "serial": "E1"})
EDGE_2 = _operation("create", "DcimDevice", {"name": "edge", "serial": "E2"})
LEAF = _operation("create", "DcimDevice", {"name": "leaf-1", "serial": "L1"})
RACK_F1 = _operation("update", "LocationRack", {"name": "comms-closet", "facility_id": "F1"})
RACK_F2 = _operation("create", "LocationRack", {"name": "comms-closet", "facility_id": "F2"})
RACK_R1 = _operation("create", "LocationRack", {"name": "r1", "facility_id": "F1"})
SITE = _operation("create", "LocationSite", {"name": "paris"})

ALL_OPERATIONS = [SITE, RACK_F1, RACK_F2, RACK_R1, EDGE_1, EDGE_2, LEAF]


def _member(operation: PlannedOperation) -> tuple[str, bytes]:
    return operation.kind, canonical_json_bytes(operation.identity, kind=operation.kind)


def _source_identities(*operations: PlannedOperation) -> list[tuple[str, dict[str, Any]]]:
    return [(operation.operation_id, dict(operation.identity)) for operation in operations]


# ---------------------------------------------------------------------------------------
# Destination identity collisions
# ---------------------------------------------------------------------------------------


def test_destination_collision_groups_return_every_group() -> None:
    core_1 = _operation("create", "DcimDevice", {"name": "core", "serial": "C1"})
    core_2 = _operation("create", "DcimDevice", {"name": "core", "serial": "C2"})
    core_3 = _operation("create", "DcimDevice", {"name": "core", "serial": "C3"})

    groups = destination_identity_collision_groups(
        schema=SCHEMA, operations=[EDGE_1, core_1, EDGE_2, LEAF, core_2, core_3]
    )

    assert [(group.kind, group.rule, group.components) for group in groups] == [
        ("DcimDevice", "human-friendly ID", ("name__value",)),
        ("DcimDevice", "human-friendly ID", ("name__value",)),
    ]
    assert [group.operations for group in groups] == [(EDGE_1, EDGE_2), (core_1, core_2, core_3)]


def test_destination_collision_groups_are_empty_without_a_collision() -> None:
    assert destination_identity_collision_groups(schema=SCHEMA, operations=[EDGE_1, LEAF, RACK_R1, SITE]) == []


def test_destination_collision_groups_ignore_updates() -> None:
    """Only creates collide here; an update is keyed by its recorded destination ID."""
    assert destination_identity_collision_groups(schema=SCHEMA, operations=[RACK_F1, RACK_F2]) == []


def test_the_raising_half_raises_on_the_same_input() -> None:
    with pytest.raises(DestinationIdentityCollisionError) as error:
        refuse_destination_identity_collisions(schema=SCHEMA, operations=[EDGE_1, EDGE_2, LEAF])

    message = str(error.value)
    assert "Planned creates of destination kind 'DcimDevice' collide on human-friendly ID" in message
    assert "name__value='edge'" in message
    assert "The whole plan was refused before any destination write." in message


def test_the_raising_half_names_the_count_beyond_two() -> None:
    third = _operation("create", "DcimDevice", {"name": "edge", "serial": "E3"})

    with pytest.raises(DestinationIdentityCollisionError, match="and 1 more"):
        refuse_destination_identity_collisions(schema=SCHEMA, operations=[EDGE_1, EDGE_2, third])


def test_the_raising_half_is_silent_without_a_group() -> None:
    refuse_destination_identity_collisions(schema=SCHEMA, operations=[EDGE_1, LEAF])


def test_cross_kind_groups_on_an_inherited_unique_attribute_are_returned() -> None:
    schema = {
        "GenericInterface": _node([], unique=("name",)),
        "InterfacePhysical": _node(["name__value", "serial__value"], inherit_from=("GenericInterface",)),
        "InterfaceVirtual": _node(["name__value", "vlan__value"], inherit_from=("GenericInterface",)),
    }
    physical = _operation("create", "InterfacePhysical", {"name": "eth0", "serial": "S1"})
    virtual = _operation("create", "InterfaceVirtual", {"name": "eth0", "vlan": "10"})

    groups = destination_identity_collision_groups(schema=schema, operations=[physical, virtual])

    assert len(groups) == 1
    assert groups[0].generic == "GenericInterface"
    assert groups[0].components == ("name__value",)
    assert groups[0].operations == (physical, virtual)
    with pytest.raises(DestinationIdentityCollisionError, match="inherited unique attribute GenericInterface"):
        refuse_destination_identity_collisions(schema=schema, operations=[physical, virtual])


# ---------------------------------------------------------------------------------------
# Source identity collisions
# ---------------------------------------------------------------------------------------


def test_source_collision_groups_return_one_group_per_destination_key() -> None:
    """Both `edge` creates and an unchanged third `edge` record are one group, listed once."""
    unchanged = ("unchanged-edge", {"name": "edge", "serial": "E3"})
    identities = [*_source_identities(EDGE_1, EDGE_2, LEAF), unchanged]

    groups = source_identity_collision_groups(
        kind="DcimDevice", node=SCHEMA["DcimDevice"], creates=[EDGE_1, EDGE_2, LEAF], identities=identities
    )

    assert len(groups) == 1
    (group,) = groups
    assert group.operations == (EDGE_1, EDGE_2)
    assert [identifier for identifier, _ in group.source_records] == [
        EDGE_1.operation_id,
        EDGE_2.operation_id,
        "unchanged-edge",
    ]
    assert group.member_key() == frozenset(
        {
            _member(EDGE_1),
            _member(EDGE_2),
            ("DcimDevice", canonical_json_bytes(canonical_identity(unchanged[1]), kind="DcimDevice")),
        }
    )


def test_source_collision_groups_include_a_record_with_no_operation() -> None:
    """The `comms-closet` create collides with the `F1` record whatever that record's action."""
    groups = source_identity_collision_groups(
        kind="LocationRack",
        node=SCHEMA["LocationRack"],
        creates=[RACK_F2, RACK_R1],
        identities=_source_identities(RACK_F1, RACK_F2, RACK_R1),
    )

    assert len(groups) == 1
    assert groups[0].operations == (RACK_F2,)
    assert groups[0].member_key() == frozenset({_member(RACK_F1), _member(RACK_F2)})


def test_source_collision_groups_are_empty_without_a_collision() -> None:
    assert (
        source_identity_collision_groups(
            kind="DcimDevice", node=SCHEMA["DcimDevice"], creates=[LEAF], identities=_source_identities(LEAF, EDGE_1)
        )
        == []
    )


def test_the_raising_source_half_raises_on_the_same_input() -> None:
    with pytest.raises(DestinationIdentityCollisionError) as error:
        refuse_source_identity_collisions(
            kind="LocationRack",
            node=SCHEMA["LocationRack"],
            creates=[RACK_F2, RACK_R1],
            identities=_source_identities(RACK_F1, RACK_F2, RACK_R1),
            declared_identity={"name", "facility_id"},
        )

    message = str(error.value)
    assert f"Create {RACK_F2.operation_id!r} of destination kind 'LocationRack'" in message
    assert "Declared source identity (mapping identifiers): facility_id, name." in message
    assert "The destination cannot distinguish: facility_id__value." in message


# ---------------------------------------------------------------------------------------
# Merge groups of an identity finer than the destination key
# ---------------------------------------------------------------------------------------


def test_the_finer_identity_check_returns_its_merge_groups(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING):
        groups = _warn_identity_finer_than_destination_key(
            kind="LocationRack",
            node=SCHEMA["LocationRack"],
            supplied={"name", "facility_id"},
            operations=[RACK_F1, RACK_F2, RACK_R1],
        )

    assert len(groups) == 1
    assert isinstance(groups[0], IdentityGroup)
    assert groups[0].kind == "LocationRack"
    assert groups[0].components == ("name",)
    assert groups[0].operations == (RACK_F1, RACK_F2)
    assert "2 source objects share 1 destination identity" in caplog.text


def test_the_finer_identity_check_returns_nothing_without_a_merge() -> None:
    assert (
        _warn_identity_finer_than_destination_key(
            kind="LocationRack", node=SCHEMA["LocationRack"], supplied={"name", "facility_id"}, operations=[RACK_R1]
        )
        == []
    )


def test_the_finer_identity_check_returns_nothing_when_the_destination_key_covers_the_identity() -> None:
    assert (
        _warn_identity_finer_than_destination_key(
            kind="LocationSite", node=SCHEMA["LocationSite"], supplied={"name"}, operations=[SITE, SITE]
        )
        == []
    )


# ---------------------------------------------------------------------------------------
# Leaving out every record of every group
# ---------------------------------------------------------------------------------------


def _every_group(operations: list[PlannedOperation]) -> list[IdentityGroup]:
    groups = list(destination_identity_collision_groups(schema=SCHEMA, operations=operations))
    for kind in sorted({operation.kind for operation in operations}):
        of_kind = [operation for operation in operations if operation.kind == kind]
        creates = [operation for operation in of_kind if operation.action == "create"]
        groups.extend(
            source_identity_collision_groups(
                kind=kind, node=SCHEMA[kind], creates=creates, identities=_source_identities(*of_kind)
            )
        )
        supplied = set.intersection(*(set(operation.identity) for operation in of_kind))
        groups.extend(
            _warn_identity_finer_than_destination_key(
                kind=kind, node=SCHEMA[kind], supplied=supplied, operations=of_kind
            )
        )
    return groups


def test_every_record_of_a_group_is_a_member_of_the_group() -> None:
    members = set().union(*(group.member_key() for group in _every_group(ALL_OPERATIONS)))

    assert members == {_member(EDGE_1), _member(EDGE_2), _member(RACK_F1), _member(RACK_F2)}


def test_the_edge_pair_forms_the_same_group_in_every_check_that_finds_it() -> None:
    """The later problem pass reports a group once: the checks agree on its member key."""
    edge_keys = {group.member_key() for group in _every_group(ALL_OPERATIONS) if group.kind == "DcimDevice"}

    assert edge_keys == {frozenset({_member(EDGE_1), _member(EDGE_2)})}


def test_the_convergence_check_finds_nothing_once_every_member_is_left_out() -> None:
    destination = SimpleNamespace(type="Infrahub", schema=SCHEMA)
    with pytest.raises(DestinationIdentityCollisionError):
        warn_missing_convergence_key(destination=destination, operations=ALL_OPERATIONS)

    left_out = set().union(*(group.member_key() for group in _every_group(ALL_OPERATIONS)))
    selected = [operation for operation in ALL_OPERATIONS if _member(operation) not in left_out]

    assert selected == [SITE, RACK_R1, LEAF]
    assert _every_group(selected) == []
    warn_missing_convergence_key(destination=destination, operations=selected)
