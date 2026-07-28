"""Offline cover for the Phase H fixture's load-bearing preconditions (T074 Trap 2, T080/AD092).

`assert_convergence_key_is_supplied` lives in `tests/integration/test_saved_plan_apply_integration.py`
and is called only from fixtures in that module, every one of which is `integration`-marked
and therefore skipped on a default run. It is the check that decides whether a duplicate
object at a live destination is reported as a **fixture** error — a kind the plan cannot key
against — or is misread as a convergence bug in the product. A check with that job cannot
itself be unexercised, and it does not need a destination to exercise: it is pure, and takes
a sequence of `PlannedOperation` and a schema mapping as plain data.

So the cases below import it and run it offline. They are deliberately **not**
`integration`-marked and contact nothing, which is what makes the docstring's "exercisable,
and is exercised, offline" true rather than aspirational.

The schema doubles are `SimpleNamespace`, because the function reads exactly one attribute
off a node schema — `human_friendly_id`, through `getattr(..., None)`. A richer double would
assert more about the SDK's schema type than this function depends on.

`covering_uniqueness_constraint` is covered here for the same reason (AD092). It is the check
that decides whether SC-016's live half **runs or skips**: it answers whether the destination
schema lets two objects both match the query the resolver issues for one peer identity. A skip
decided by an unexercised check is indistinguishable from a deletion, so the cases below run it
offline over schema doubles carrying `uniqueness_constraints` and `relationships` — including the
case where the schema *does* admit an ambiguity and the live test therefore runs.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

import pytest

from infrahub_sync.plan.models import PlannedOperation
from tests.integration.test_saved_plan_apply_integration import (
    LivePlanPreconditionError,
    assert_convergence_key_is_supplied,
    covering_uniqueness_constraint,
)
from tests.plan.artifact_fixtures import operation_record

if TYPE_CHECKING:
    from collections.abc import Mapping

# A human-friendly ID that **crosses a relationship** is the shape AD043's nesting exists for:
# `location__name__value` is answered by the identity's nested
# `{"peer_kind": …, "identity": {"name": …}}` pair, and the trailing `value` segment is a
# schema spelling with no counterpart in the data. The doubles below are *shapes*, not claims
# about any particular kind: on the live destination `DcimDevice` is in fact all-direct
# (`['name__value']`) and the crossing shape belongs to the interface kinds
# (`['device__name__value', 'name__value']`) and to `IpamPrefix` / `IpamIPAddress` — see AD091.
DEVICE_HFID = ["location__name__value", "name__value"]
TAG_HFID = ["name__value"]


def _schema(**kinds: list[str] | None) -> Mapping[str, Any]:
    """A destination schema double: kind name to a node schema declaring `human_friendly_id`."""
    return {kind: SimpleNamespace(human_friendly_id=components) for kind, components in kinds.items()}


def _operation(**kwargs: Any) -> PlannedOperation:  # noqa: ANN401 — mirrors `operation_record`'s field types
    """One validated operation, built through the same fixture the artifact tests use."""
    return PlannedOperation.model_validate(operation_record(**kwargs))


NESTED_DEVICE_IDENTITY: dict[str, Any] = {
    "name": "router1",
    "location": {"peer_kind": "LocationRack", "identity": {"name": "rack-7"}},
}


def test_a_kind_absent_from_the_destination_schema_is_a_fixture_error() -> None:
    """The plan names a kind the destination does not declare, so nothing can be keyed."""
    operation = _operation(kind="DcimDevice", identity=NESTED_DEVICE_IDENTITY)

    with pytest.raises(LivePlanPreconditionError) as caught:
        assert_convergence_key_is_supplied(
            operations=[operation],
            destination_schema=_schema(BuiltinTag=TAG_HFID),
        )

    message = str(caught.value)
    assert "declares no kind 'DcimDevice'" in message, message
    assert operation.operation_id in message, message


def test_a_kind_declaring_no_human_friendly_id_is_a_fixture_error() -> None:
    """A kind with no HFID cannot be upserted convergently, whatever the identity supplies."""
    operation = _operation(kind="BuiltinTag", identity={"name": "edge"})

    with pytest.raises(LivePlanPreconditionError) as caught:
        assert_convergence_key_is_supplied(
            operations=[operation],
            destination_schema=_schema(BuiltinTag=None),
        )

    message = str(caught.value)
    assert "declares no 'human_friendly_id'" in message, message
    assert "every re-apply would duplicate" in message, message


def test_a_component_the_identity_does_not_supply_is_named_in_the_refusal() -> None:
    """The refusal names **which** component is missing — the reason it is checked per component.

    `location__name__value` is declared by the schema and unanswerable from a flat identity,
    so the fixture is refused before any assertion about convergence can be made on it.
    """
    operation = _operation(kind="DcimDevice", identity={"name": "router1"})

    with pytest.raises(LivePlanPreconditionError) as caught:
        assert_convergence_key_is_supplied(
            operations=[operation],
            destination_schema=_schema(DcimDevice=DEVICE_HFID),
        )

    message = str(caught.value)
    assert "Missing component(s): location__name__value" in message, message
    assert "name__value" in message, message


def test_a_nested_peer_component_the_identity_does_supply_passes() -> None:
    """AD043's nesting is followed, not flattened: the pair answers `location__name__value`.

    The negative cases above all pass against a check that answered "missing" for every
    relationship-crossing component. This one is what distinguishes a check that follows the
    nesting from one that only ever refuses.
    """
    operations = [
        _operation(kind="DcimDevice", identity=NESTED_DEVICE_IDENTITY),
        _operation(kind="BuiltinTag", identity={"name": "edge"}),
    ]

    assert_convergence_key_is_supplied(
        operations=operations,
        destination_schema=_schema(DcimDevice=DEVICE_HFID, BuiltinTag=TAG_HFID),
    )


def test_a_nested_component_the_pair_does_not_reach_is_still_refused() -> None:
    """The pair is present but its own identity lacks the segment, which is not "supplied".

    Distinguishes following the nesting from merely finding a mapping at the head segment —
    a check that stopped at `location` would accept this and let an unkeyed write through.
    """
    operation = _operation(
        kind="DcimDevice",
        identity={"name": "router1", "location": {"peer_kind": "LocationRack", "identity": {"slug": "rack-7"}}},
    )

    with pytest.raises(LivePlanPreconditionError) as caught:
        assert_convergence_key_is_supplied(
            operations=[operation],
            destination_schema=_schema(DcimDevice=DEVICE_HFID),
        )

    assert "Missing component(s): location__name__value" in str(caught.value)


# ---------------------------------------------------------------------------------------
# AD092 — whether the destination schema admits an ambiguous peer at all
# ---------------------------------------------------------------------------------------

# The live shape (V30, AD091): `InterfaceLag` is keyed `['device__name__value', 'name__value']`
# and constrained `[['device', 'name__value']]`, so the two filters the resolver queries it with
# pin every component of that constraint and a second matching object is refused with HTTP 422.
LAG_FILTERS = {"device__name__value": "dmi01-akron-rtr01", "name__value": "lag1"}


def _peer_schema(
    *,
    lag_constraints: list[list[str]] | None,
    device_constraints: list[list[str]] | None,
) -> Mapping[str, Any]:
    """An interface keyed through its device, with each kind's constraints under the test's control."""
    return {
        "InterfaceLag": SimpleNamespace(
            human_friendly_id=["device__name__value", "name__value"],
            uniqueness_constraints=lag_constraints,
            relationships=[SimpleNamespace(name="device", peer="DcimDevice")],
        ),
        "DcimDevice": SimpleNamespace(
            human_friendly_id=["name__value"],
            uniqueness_constraints=device_constraints,
            relationships=[],
        ),
    }


def test_a_constraint_the_resolver_filters_pin_admits_no_ambiguity() -> None:
    """The live case: the covering constraint is returned, so SC-016's live half skips.

    `device` is not filtered on directly; it is pinned because the filters reach through it to
    a device name that the device kind's own constraint makes unique.
    """
    covering = covering_uniqueness_constraint(
        destination_schema=_peer_schema(
            lag_constraints=[["device", "name__value"]], device_constraints=[["name__value"]]
        ),
        kind="InterfaceLag",
        filters=LAG_FILTERS,
    )

    assert covering == ["device", "name__value"]


def test_a_constraint_left_free_by_the_filters_admits_an_ambiguity() -> None:
    """A constraint carrying a component the resolver does not filter on: the test would run.

    This is the case that keeps the guard a skip rather than a deletion — the same filters, a
    schema whose constraint leaves `description__value` free, and a second matching object is
    creatable.
    """
    covering = covering_uniqueness_constraint(
        destination_schema=_peer_schema(
            lag_constraints=[["device", "name__value", "description__value"]],
            device_constraints=[["name__value"]],
        ),
        kind="InterfaceLag",
        filters=LAG_FILTERS,
    )

    assert covering is None


def test_a_kind_declaring_no_uniqueness_constraint_admits_an_ambiguity() -> None:
    """Nothing to violate, so nothing stops the clone."""
    covering = covering_uniqueness_constraint(
        destination_schema=_peer_schema(lag_constraints=None, device_constraints=[["name__value"]]),
        kind="InterfaceLag",
        filters=LAG_FILTERS,
    )

    assert covering is None


def test_a_relationship_component_is_pinned_only_when_its_peer_is_keyed() -> None:
    """`device__name__value` pins `device` only if a device name identifies one device.

    Distinguishes this check from a prefix match: with device names unconstrained, two devices
    may share the name, two LAGs may match the filters, and the ambiguity is seedable — so the
    same constraint that covers the query above does not cover it here.
    """
    covering = covering_uniqueness_constraint(
        destination_schema=_peer_schema(lag_constraints=[["device", "name__value"]], device_constraints=None),
        kind="InterfaceLag",
        filters=LAG_FILTERS,
    )

    assert covering is None
