"""The operations digest (spec 019 T004, research R16).

Two plans of the same data hold the same operations in any order, and their updates name the
Infrahub ID of a node that differs on each rebuilt Infrahub. The digest must be equal for both,
and must differ as soon as one planned value differs.
"""

from __future__ import annotations

from typing import Any

from infrahub_sync.client.models import PlanOperationResource
from infrahub_sync.plan.digest import operations_digest
from infrahub_sync.plan.identity import operation_id
from infrahub_sync.plan.models import PlannedOperation


def planned(
    action: str,
    identity: dict[str, Any],
    *,
    payload: dict[str, Any] | None = None,
    relationships: list[dict[str, Any]] | None = None,
    destination_id: str | None = None,
) -> PlannedOperation:
    """One `DcimDevice` operation with its derived identifier."""
    return PlannedOperation.model_validate(
        {
            "operation_id": operation_id(action, "DcimDevice", identity),
            "action": action,
            "kind": "DcimDevice",
            "identity": identity,
            "tier": 1,
            "payload": payload,
            "relationships": relationships,
            "destination_id": destination_id,
        }
    )


def operations(
    *, description: str = "leaf", destination_id: str = "18d52a8a-0000-0000-0000-000000000001"
) -> list[PlannedOperation]:
    """A create, an update with a relationship and a delete."""
    return [
        planned("create", {"name": "leaf-1"}, payload={"name": "leaf-1", "description": "new"}),
        planned(
            "update",
            {"name": "leaf-2"},
            payload={"name": "leaf-2", "description": description},
            relationships=[
                {
                    "field": "site",
                    "peer_kind": "LocationSite",
                    "cardinality": "one",
                    "peers": [{"name": "paris"}],
                }
            ],
            destination_id=destination_id,
        ),
        planned("delete", {"name": "leaf-3"}),
    ]


def test_the_digest_is_equal_for_the_same_operations_in_another_order() -> None:
    first = operations()

    assert operations_digest(first) == operations_digest(list(reversed(first)))


def test_the_digest_differs_when_one_payload_value_differs() -> None:
    assert operations_digest(operations(description="leaf")) != operations_digest(operations(description="spine"))


def test_the_digest_is_unchanged_when_only_the_destination_id_differs() -> None:
    first = operations(destination_id="18d52a8a-0000-0000-0000-000000000001")
    second = operations(destination_id="18d52a8a-0000-0000-0000-000000000002")

    assert operations_digest(first) == operations_digest(second)


def test_the_digest_is_equal_for_the_operations_of_the_saved_plan_route() -> None:
    """The benchmark reads `PlanOperationResource` values; they digest like the saved plan's."""
    first = operations()
    resources = [PlanOperationResource.model_validate(operation.model_dump(mode="json")) for operation in first]

    assert operations_digest(resources) == operations_digest(first)
