"""A destination that rejects one operation of an apply, for spec 009 User Story 4.

`RejectingDestination` is the write surface the engine applies a saved plan to. It keeps every
operation before operation `reject_at` (counted from 1, in the order the engine attempts them)
and rejects that one with an Infrahub `GraphQLError`, as Infrahub rejects an attribute value.
`destination_after` turns the kept writes into destination records, so a new plan can be made
against the destination as the stopped apply left it.

Not a test module: no assertions live here.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from infrahub_sdk.exceptions import GraphQLError

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    from diffsync import Adapter

    from infrahub_sync.plan.models import PlannedOperation

# The model fields DiffSync keeps on every model; they are not destination data.
_NON_DATA_FIELDS = frozenset({"adapter", "model_flags", "local_data"})


def rejection() -> GraphQLError:
    """Infrahub's rejection of an attribute value, as its error formatter writes it."""
    return GraphQLError(
        errors=[
            {
                "message": "row 1 is not a valid value for description",
                "extensions": {
                    "code": "ATTRIBUTE_CONSTRAINT_VIOLATION",
                    "http_status": 422,
                    "data": {"node_kind": "LocationRack", "field_name": "description"},
                },
            }
        ],
        query="mutation { LocationRackCreate }",
    )


class RejectingDestination:
    """A write surface that keeps every operation before `reject_at` and rejects that one.

    `error` builds the exception raised for operation `reject_at`; Infrahub's rejection of an
    attribute value by default.
    """

    def __init__(self, reject_at: int, error: Callable[[PlannedOperation], BaseException] | None = None) -> None:
        self.reject_at = reject_at
        self.error = error or (lambda _operation: rejection())
        self.attempted: list[str] = []
        self.written: list[PlannedOperation] = []

    def new_peer_resolver(self) -> object:  # noqa: PLR6301 - the write surface declares it on the instance
        return object()

    def apply_planned_operation(self, *, operation: PlannedOperation, peers: object) -> str:
        """Keep the operation, or reject it when it is operation `reject_at`."""
        _ = peers
        self.attempted.append(operation.operation_id)
        if len(self.attempted) == self.reject_at:
            raise self.error(operation)
        self.written.append(operation)
        return f"written-node-{len(self.written)}"


def _leaves(value: object) -> frozenset[str]:
    """Every scalar in an identity, nested peer identities included, as strings."""
    if isinstance(value, dict):
        # A peer reference in a plan identity names the peer's kind next to its identity.
        items = [item for key, item in value.items() if key != "peer_kind"]
        return frozenset().union(*(_leaves(item) for item in items)) if items else frozenset()
    if isinstance(value, str):
        # A model's reference to a peer is the peer's unique ID: its identity joined with "__".
        return frozenset(value.split("__"))
    return frozenset({str(value)})


def destination_after(
    source: Adapter,
    written: list[PlannedOperation],
    existing: Mapping[str, list[dict[str, Any]]] | None = None,
) -> dict[str, list[dict[str, Any]]]:
    """The destination records after `written` reached the destination.

    A written create or update leaves the destination node equal to its source model, so the
    record of each written operation is the data of the source model with the same kind and
    identity. `existing` holds the records the destination had before the apply.
    """
    records: dict[str, list[dict[str, Any]]] = {kind: list(items) for kind, items in (existing or {}).items()}
    for operation in written:
        identity = _leaves(operation.identity)
        (model,) = [model for model in source.get_all(operation.kind) if _leaves(model.get_identifiers()) == identity]
        data = {name: value for name, value in model.model_dump().items() if name not in _NON_DATA_FIELDS}
        data["local_id"] = f"destination-{data['local_id']}"
        records.setdefault(operation.kind, []).append(data)
    return records


__all__ = ["RejectingDestination", "destination_after", "rejection"]
