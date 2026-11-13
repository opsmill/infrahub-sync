"""Compare a planned create or update with the node the destination holds (spec 022, research R7).

`verify` reports, for each uncertain operation of an interrupted run, whether the destination
already matches the plan, differs from it, or has no such node. The comparison is pure: the
destination adapter reads the nodes (`DestinationNodeReader`) and resolves the plan's peers to
the destination's node ids, and `compare_planned_operation` turns one operation and what was
read into a state.

Values read from the destination stay in memory: a state, never a value, leaves this module.
"""

from __future__ import annotations

import ipaddress
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal, Protocol, runtime_checkable

from infrahub_sync.plan.canonical import canonical_value
from infrahub_sync.plan.errors import UnserializablePayloadValueError

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from infrahub_sync.plan.models import PlannedOperation

DestinationState = Literal["matches", "differs", "missing"]

# How many operations a `verify` report lists; the rest are counted in `operations_truncated`.
VERIFY_REPORT_LIMIT = 2000

_IP_TYPES = (ipaddress.IPv4Interface, ipaddress.IPv6Interface, ipaddress.IPv4Network, ipaddress.IPv6Network)


class DestinationReadError(Exception):
    """Reading the uncertain operations' nodes from the destination failed.

    Raised with a fixed message, because the original's text can carry a destination
    address, a header or a credential. The original is kept as the cause, so the run's
    failure record can name the failure type by the cause's class.
    """

    def __init__(self) -> None:
        super().__init__(
            "verify could not read the nodes of the uncertain operations from the destination. "
            "Nothing was written. Run verify again when the destination is reachable."
        )


@dataclass(frozen=True)
class DestinationNode:
    """What the destination holds for the fields one planned operation writes.

    `attributes` maps each attribute name to the value read. `peers` maps each relationship
    name to the ids of its peers: none or one for a cardinality-one relationship.
    """

    attributes: Mapping[str, Any] = field(default_factory=dict)
    peers: Mapping[str, frozenset[str]] = field(default_factory=dict)


@dataclass(frozen=True)
class PlannedNodeRead:
    """The node read for one planned operation, and the plan's peers as destination node ids.

    `node` is `None` when the destination holds no node for the operation, or several that
    share its key: `ambiguous` is then true, as the apply's peer resolver refuses such a key.
    `planned_peers` maps each relationship the operation writes to the ids its planned peers
    resolve to, or to `None` when one of them matches no node, or several, at the destination.
    """

    node: DestinationNode | None
    planned_peers: Mapping[str, frozenset[str] | None] = field(default_factory=dict)
    ambiguous: bool = False


@runtime_checkable
class DestinationNodeReader(Protocol):
    """A destination that can read the nodes of planned creates and updates without writing."""

    def read_planned_nodes(self, operations: Sequence[PlannedOperation]) -> Mapping[str, PlannedNodeRead]:
        """Read the node of each create and update, keyed by operation id."""


def comparable_value(value: Any) -> Any:
    """Normalize a planned or a destination value so that equal values compare equal.

    The SDK returns IP attributes as `ipaddress` objects, which the plan records as their
    string form, as `infrahub_node_to_diffsync` does. Every value then takes its canonical
    JSON form; a value outside the canonical table compares by its string form.
    """
    if isinstance(value, _IP_TYPES):
        return str(value)
    try:
        return canonical_value(value)
    except UnserializablePayloadValueError:
        return str(value)


def compare_planned_operation(operation: PlannedOperation, read: PlannedNodeRead) -> DestinationState:
    """Return whether the destination node matches the planned create or update.

    `differs` when several destination nodes share the operation's key: which of them the
    plan names is not known, so it never `matches`. Otherwise `missing` when no node was read,
    and `matches` when every attribute of the payload and every relationship the operation
    writes equals the destination's: a cardinality-many relationship as a set of peer ids. Any
    difference, or a planned peer that resolves to no single node, is `differs`. Fields the
    plan does not write are not compared.
    """
    if read.ambiguous:
        return "differs"
    if read.node is None:
        return "missing"
    return "matches" if _node_holds_the_plan(operation, read.node, read.planned_peers) else "differs"


def _node_holds_the_plan(
    operation: PlannedOperation, node: DestinationNode, planned_peers: Mapping[str, frozenset[str] | None]
) -> bool:
    """Whether every field the operation writes holds the planned value in `node`."""
    for name, planned in (operation.payload or {}).items():
        if name in node.attributes:
            if comparable_value(node.attributes[name]) != comparable_value(planned):
                return False
        elif name in node.peers:
            # A null cardinality-one relationship a hand-built payload carries.
            if planned is not None or node.peers[name]:
                return False
        else:
            return False
    for reference in operation.relationships or ():
        expected = planned_peers.get(reference.field)
        if expected is None or node.peers.get(reference.field, frozenset()) != expected:
            return False
    return True


def reported_operations(
    operations: Sequence[PlannedOperation], window: range
) -> tuple[tuple[PlannedOperation, ...], int]:
    """The creates and updates of `window` a report lists, and how many it leaves out.

    Deletes are never written, so they are never reported. The report lists at most
    `VERIFY_REPORT_LIMIT` operations, in the plan's stored order.
    """
    candidates = [operations[index] for index in window if operations[index].action != "delete"]
    return tuple(candidates[:VERIFY_REPORT_LIMIT]), max(0, len(candidates) - VERIFY_REPORT_LIMIT)


def verification_report(
    operations: Sequence[PlannedOperation], reads: Mapping[str, PlannedNodeRead]
) -> list[dict[str, str]]:
    """One `{operation_id, kind, action, state}` entry per operation, never a value."""
    return [
        {
            "operation_id": operation.operation_id,
            "kind": operation.kind,
            "action": operation.action,
            "state": compare_planned_operation(operation, reads.get(operation.operation_id, PlannedNodeRead(None))),
        }
        for operation in operations
    ]
