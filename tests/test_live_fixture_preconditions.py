"""Offline cover for the Phase H fixture's load-bearing precondition (T074, Trap 2).

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
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

import pytest

from infrahub_sync.plan.models import PlannedOperation
from tests.integration.test_saved_plan_apply_integration import (
    LivePlanPreconditionError,
    assert_convergence_key_is_supplied,
)
from tests.plan.artifact_fixtures import operation_record

if TYPE_CHECKING:
    from collections.abc import Mapping

# `DcimDevice`'s human-friendly ID crosses a relationship, which is the shape AD043's nesting
# exists for: `location__name__value` is answered by the identity's nested
# `{"peer_kind": …, "identity": {"name": …}}` pair, and the trailing `value` segment is a
# schema spelling with no counterpart in the data.
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
