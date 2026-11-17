"""Plan, apply and plan again over a stand-in destination (spec 019 T006, T007, research R9).

Not a test module: it holds the fixture loader and the stand-in destination that the
second-plan tests share. No assertion lives here.

The fixture lives in `tests/data/second_plan/`, in the file shapes of `tests/data/plan_problems/`
(see `tests/plan/conftest.py`). Its source records are in groups, so that a test plans the
edge case it is about:

- `devices`: two devices, `leaf-1` and `leaf-2`. Every other group refers to them.
- `lag`: the `InterfaceLag` `ae0` on `leaf-1`, two `InterfacePhysical` members that refer to it
  through the optional one-peer relationship `bundle` (the field name of the shipped NetBox
  example, mapped from NetBox `lag`), and one interface outside the LAG.
- `no_vrf`: prefixes and IP addresses with no VRF. Each names the `IpamNamespace` `default` by
  its unique ID, as the NetBox example's `namespace_name` does; no source record holds that
  namespace, so the plan records it as a literal identity
  (`tests/plan/test_destination_only_peer.py`).
- `tagged_vlans`: three VLANs and three interfaces on `leaf-2` with the many relationship
  `tagged_vlan`: two peers, three peers listed in reverse order, and none.

The destination holds one node before the first plan: the `IpamNamespace` named `default`,
which Infrahub creates itself in every branch (`tests/data/infrahub_builtin_nodes/`). Its row
carries the node metadata Infrahub returns for it (creator `__system__`) in a `node_metadata`
key; the stand-in client turns that key into node metadata, so it is not model data, and the
plan's destination counts the node as built-in by its id, because its kind is in
`INFRAHUB_BUILTIN_NODE_KINDS` (spec 019 research R2, ADR 0047).

`ApplyingDestination` takes the place of the Infrahub adapter during `Potenda.apply_plan`. It
writes each planned operation into model data by kind, and `destination_data` returns that data
for the `destination` argument of the next `plan_fixture` call. The second plan therefore
compares the source with what the apply wrote, not with the source records, so a defect of the
apply shows in the second plan.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

from infrahub_sync.adapters.infrahub import INFRAHUB_BUILTIN_NODE_KINDS, INFRAHUB_SYSTEM_ACCOUNT_ID
from tests.plan.conftest import PlanProblemFixture
from tests.plan.ownership_fixtures import granted_ownership

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

    from diffsync import DiffSyncModel

    from infrahub_sync.plan.models import PlannedOperation, RelationshipReference
    from tests.plan.problem_plan import PlannedFixture

SECOND_PLAN_DIR = Path(__file__).resolve().parent.parent / "data" / "second_plan"

# The groups of `source_records.json`, in the order a test usually combines them.
GROUPS: tuple[str, ...] = ("devices", "lag", "no_vrf", "tagged_vlans")

# The first part of each `local_id` that the stand-in destination gives a created node.
CREATED_ID_PREFIX = "18d52a8a-0000-0000-0919-"


def _read_json(name: str) -> dict[str, Any]:
    return json.loads((SECOND_PLAN_DIR / name).read_text(encoding="utf-8"))


def load_second_plan_fixture() -> PlanProblemFixture:
    """Read the files of `tests/data/second_plan/` into the fixture type that `plan_fixture` takes.

    The fixture has no expected problems. Pass `records` and `destination` to `plan_fixture`
    explicitly: the `Variant` groups and the `twin` site rule of the problem fixture do not
    apply to it.
    """
    return PlanProblemFixture(
        schema_record=_read_json("destination_schema.json"),
        config_data=_read_json("sync_config.json"),
        record_groups=_read_json("source_records.json"),
        destination_data=_read_json("destination_records.json"),
        expected={},
    )


def second_plan_records(fixture: PlanProblemFixture, *groups: str) -> dict[str, list[dict[str, Any]]]:
    """The raw source records of `groups` (all of them by default), by kind in load order."""
    merged: dict[str, list[dict[str, Any]]] = {kind: [] for kind in fixture.order}
    for group in groups or GROUPS:
        for kind, records in fixture.record_groups[group].items():
            merged[kind].extend(copy.deepcopy(records))
    return merged


def second_plan_destination(fixture: PlanProblemFixture) -> dict[str, list[dict[str, Any]]]:
    """The nodes the destination holds before the first plan: the built-in `default` namespace."""
    return copy.deepcopy(fixture.destination_data)


class PeerMissingError(LookupError):
    """A planned operation names a peer or a node that the stand-in destination does not hold."""


class ApplyingDestination:
    """A stand-in destination that writes planned operations into model data by kind.

    It implements the two methods of `PlannedWriteDestination`. A node is the model data the
    Infrahub adapter loads: `local_id`, attribute values, the unique ID of a one-peer
    relationship's peer (or `None`) and the sorted unique IDs of a many relationship's peers.

    - A create adds a node with a new `local_id` and returns it.
    - An update finds the node by `destination_id`, then sets each payload value and each
      relationship. An empty one-peer reference sets the field to `None`; a many reference
      replaces the set.
    - A delete removes the node of the operation's identity. `Potenda.apply_plan` never
      dispatches a delete today, so the method is not reached through it.

    A peer the destination does not hold fails the operation with `PeerMissingError`, as
    Infrahub fails a write that names a node it does not hold.
    """

    def __init__(self, *, models: Mapping[str, type[DiffSyncModel]], nodes: Mapping[str, list[dict[str, Any]]]) -> None:
        """Hold a copy of `nodes`; `models` gives each kind's identifiers and unique ID."""
        self._models = dict(models)
        self._nodes: dict[str, list[dict[str, Any]]] = {kind: copy.deepcopy(rows) for kind, rows in nodes.items()}
        self._created = 0
        self.applied: list[PlannedOperation] = []

    @classmethod
    def for_plan(cls, planned: PlannedFixture, nodes: Mapping[str, list[dict[str, Any]]]) -> ApplyingDestination:
        """A stand-in holding `nodes`, the destination of `planned`, with that plan's bound models."""
        models = {kind: getattr(planned.destination, kind) for kind in planned.potenda.top_level}
        return cls(models=models, nodes=nodes)

    def apply(self, planned: PlannedFixture, *, confirm_left_out: bool = False) -> dict[str, list[dict[str, Any]]]:
        """Apply the saved plan of `planned` through `Potenda.apply_plan`; return `destination_data()`.

        `confirm_left_out` confirms that the records the plan leaves out were reviewed, which
        `Potenda.apply_plan` requires before it writes a plan that leaves records out.
        """
        planned.potenda.destination = self  # ty: ignore[invalid-assignment]
        planned.potenda.apply_plan(ownership=granted_ownership(), confirm_left_out=confirm_left_out)
        return self.destination_data()

    def destination_data(self) -> dict[str, list[dict[str, Any]]]:
        """The nodes held, by kind, for the `destination` argument of the next `plan_fixture` call."""
        return {kind: copy.deepcopy(rows) for kind, rows in self._nodes.items() if rows}

    @property
    def builtin_node_ids(self) -> frozenset[str]:
        """The ids of the held nodes that Infrahub created itself (`BuiltinNodeReadingDestination`).

        A held node carries the mark as Infrahub returns it, in its `node_metadata` key; the
        stand-in client of `tests/plan/problem_plan.py` reads that key as node metadata, so the
        next plan's destination finds the same set. As in the Infrahub adapter, only a node of a
        kind in `INFRAHUB_BUILTIN_NODE_KINDS` counts.
        """
        return frozenset(
            str(row["local_id"])
            for kind, rows in self._nodes.items()
            if kind in INFRAHUB_BUILTIN_NODE_KINDS
            for row in rows
            if (row.get("node_metadata") or {}).get("created_by", {}).get("id") == INFRAHUB_SYSTEM_ACCOUNT_ID
        )

    def new_peer_resolver(self) -> ApplyingDestination:
        """The per-apply resolver: the stand-in resolves peers against the nodes it holds."""
        return self

    def apply_planned_operation(self, *, operation: PlannedOperation, peers: object) -> str:
        """Write `operation` into the nodes held and return the node's `local_id`."""
        _ = peers
        rows = self._nodes.setdefault(operation.kind, [])
        if operation.action == "create":
            self._created += 1
            node: dict[str, Any] = {"local_id": f"{CREATED_ID_PREFIX}{self._created:012d}"}
            node.update(self._identity_values(operation.identity))
            rows.append(node)
        elif operation.action == "update":
            node = self._node_by_id(operation.kind, operation.destination_id)
        else:
            node = self._node_by_identity(operation.kind, operation.identity)
            rows.remove(node)
            self.applied.append(operation)
            return str(node["local_id"])
        node.update(copy.deepcopy(operation.payload or {}))
        for reference in operation.relationships or ():
            node[reference.field] = self._reference_value(reference)
        self.applied.append(operation)
        return str(node["local_id"])

    def unique_id(self, kind: str, identity: Mapping[str, Any]) -> str:
        """The unique ID of the node of `kind` whose canonical plan identity is `identity`."""
        return self._models[kind].create_unique_id(**self._identity_values(identity))

    def _identity_values(self, identity: Mapping[str, Any]) -> dict[str, Any]:
        """The model data of a canonical identity: a nested peer identity becomes its unique ID."""
        return {
            name: self.unique_id(value["peer_kind"], value["identity"]) if _is_peer(value) else value
            for name, value in identity.items()
        }

    def _reference_value(self, reference: RelationshipReference) -> str | list[str] | None:
        """The model data of one relationship reference, after checking that each peer is held."""
        unique_ids = [self._held_peer(reference.peer_kind, peer) for peer in reference.peers]
        if reference.cardinality == "many":
            return sorted(unique_ids)
        return unique_ids[0] if unique_ids else None

    def _held_peer(self, kind: str, identity: Mapping[str, Any]) -> str:
        """The unique ID of a peer, or `PeerMissingError` when no node of `kind` has it."""
        unique_id = self.unique_id(kind, identity)
        if unique_id not in self._held_unique_ids(kind):
            msg = f"The destination holds no {kind} node {unique_id!r}."
            raise PeerMissingError(msg)
        return unique_id

    def _row_unique_id(self, kind: str, row: Mapping[str, Any]) -> str:
        """The unique ID of a held node, from its identifier values."""
        model = self._models[kind]
        identifiers: dict[str, Any] = {name: row.get(name) for name in model._identifiers}
        return model.create_unique_id(**identifiers)

    def _held_unique_ids(self, kind: str) -> set[str]:
        return {self._row_unique_id(kind, row) for row in self._nodes.get(kind, [])}

    def _node_by_id(self, kind: str, destination_id: str | None) -> dict[str, Any]:
        for row in self._nodes.get(kind, []):
            if row["local_id"] == destination_id:
                return row
        msg = f"The destination holds no {kind} node with the ID {destination_id!r}."
        raise PeerMissingError(msg)

    def _node_by_identity(self, kind: str, identity: Mapping[str, Any]) -> dict[str, Any]:
        unique_id = self.unique_id(kind, identity)
        for row in self._nodes.get(kind, []):
            if self._row_unique_id(kind, row) == unique_id:
                return row
        msg = f"The destination holds no {kind} node {unique_id!r}."
        raise PeerMissingError(msg)


def _is_peer(value: object) -> bool:
    """Tell whether an identity value is a nested `{"peer_kind": ..., "identity": ...}` reference."""
    return isinstance(value, dict) and set(value) == {"peer_kind", "identity"}


def kinds_of(operations: Iterable[PlannedOperation]) -> list[tuple[str, str]]:
    """The action and kind of each operation, sorted, for an assertion message."""
    return sorted((operation.action, operation.kind) for operation in operations)


__all__ = [
    "CREATED_ID_PREFIX",
    "GROUPS",
    "SECOND_PLAN_DIR",
    "ApplyingDestination",
    "PeerMissingError",
    "kinds_of",
    "load_second_plan_fixture",
    "second_plan_destination",
    "second_plan_records",
]
