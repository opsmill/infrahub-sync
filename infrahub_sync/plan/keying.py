"""Whether a planned write can key itself, and the refusals when it cannot.

One module, because two callers need the same answer and must not drift: plan derivation
proves a create before it records it, and the destination write surface proves it again
before it issues a mutation. A create refused at plan time and the same create arriving in a
hand-built artifact are then refused for the same reason and with the same words.

Nothing here contacts a destination. Every answer is read from the operation and the cached
destination schema, which is what lets the write surface apply it without the destination
load FR-012 forbids.

The rules themselves, and the measurements behind them, are recorded in
[ADR 0013](../../dev/adr/0013-writes-are-keyed-by-recorded-id-and-complete-hfid.md).
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from infrahub_sync.plan.errors import DestinationIdentityCollisionError, UnkeyedCreateRefusedError

if TYPE_CHECKING:
    from collections.abc import Sequence

    from infrahub_sync.plan.models import PlannedOperation

# Separator between the segments of a *schema* component path — `name__value` for a direct
# attribute, `site__name__value` for one that crosses a relationship. The only thing split on
# it here is a schema path; a DiffSync unique id is never split.
COMPONENT_PATH_SEPARATOR = "__"


def _component_field(component: str) -> str:
    """The mapping field name a schema component path starts with.

    Infrahub writes a human-friendly-ID component and a uniqueness-constraint component as
    a path — `name__value` for a direct attribute, `site__name__value` for one that crosses
    a relationship — while the plan's destination identity is keyed by the mapping field
    name, which is the path's first segment.
    """
    return component.split(COMPONENT_PATH_SEPARATOR, 1)[0]


def component_value(identity: Mapping[str, Any], component: str) -> Any:
    """The value an operation's identity supplies for one schema component path, or `None`.

    A component is a path: `name__value` for a direct attribute, `site__name__value` for one
    that crosses a relationship. A crossing component is resolved through the nested
    `{peer_kind, identity}` pair AD043 records, so its value comes from the **peer's own
    identity** — which is the only place a plan holds it, since a plan names peers by identity
    and never by a destination-assigned id.

    `None` means the identity does not supply it, which is the same answer for an absent
    field, a peer whose identity omits the component, and a path that runs out of identity to
    walk. The three have one remedy, so they are one answer.
    """
    field, _, rest = component.partition(COMPONENT_PATH_SEPARATOR)
    if field not in identity:
        return None
    value = identity[field]
    if not rest or rest == "value":
        # A nested pair is a peer reference, not a scalar the destination can match on.
        return None if isinstance(value, Mapping) else value
    if isinstance(value, Mapping) and isinstance(value.get("identity"), Mapping):
        return component_value(value["identity"], rest)
    return None


def is_usable_component_value(value: Any) -> bool:
    """Whether a component value can key a write.

    Absent keys nothing, and so does an empty or **whitespace-only** string: the destination
    matches on the value it is sent, and `"   "` matches nothing anyone meant. Falsiness in
    general is **not** the test — `0` and `False` are values the destination matches on
    perfectly well, and reading them as missing would refuse a create that is correctly keyed.

    Public because AD051's value check at the write surface asks the same question of the
    assembled write, and the two must answer it identically.
    """
    if value is None:
        return False
    return not (isinstance(value, str) and not value.strip())


def _usable(value: Any) -> bool:
    """Module-local alias for `is_usable_component_value`."""
    return is_usable_component_value(value)


def unkeyed_create_coverage_reason(operation: PlannedOperation, *, node: Any) -> str | None:
    """Why this create's **identity** cannot key it, or `None` if it can.

    The first of the two create arms, and the only one both callers run. It asks a question
    about the plan rather than about the write: does the operation's `identity` name every
    human-friendly-ID component of the kind, and if the kind declares none, does the identity
    cover a uniqueness constraint the destination will refuse duplicates on?

    A component resolvable from the payload but absent from identity still fails, because
    identity is what the plan proves the write on and what a reviewer reads.

    Value presence is deliberately **not** asked here. At the write surface that is AD051's
    question, asked of the assembled write and answered per component
    (`_assert_identity_components_accounted_for`), which is the only check that can say which
    component is missing. Plan derivation has no assembled write, so it adds
    `unkeyed_create_value_reason` below.
    """
    identity = operation.identity
    human_friendly_id = list(getattr(node, "human_friendly_id", None) or ())
    if human_friendly_id:
        uncovered = sorted(component for component in human_friendly_id if _component_field(component) not in identity)
        if uncovered:
            return (
                f"its identity ({', '.join(sorted(identity)) or 'none'}) does not name every "
                f"human-friendly-ID component of the kind; missing: {', '.join(uncovered)}"
            )
        return None

    constraints = [list(constraint) for constraint in (getattr(node, "uniqueness_constraints", None) or [])]
    for constraint in constraints:
        if all(
            _component_field(component) in identity and _usable(component_value(identity, component))
            for component in constraint
        ):
            return None
    if constraints:
        declared = "; ".join(", ".join(constraint) for constraint in constraints)
        return (
            f"the kind declares no human-friendly ID, and its identity "
            f"({', '.join(sorted(identity)) or 'none'}) covers none of its uniqueness constraints ({declared})"
        )
    return (
        "the kind declares neither a human-friendly ID nor a uniqueness constraint, so the destination "
        "cannot match the object and every write would add another"
    )


def unkeyed_create_value_reason(operation: PlannedOperation, *, node: Any) -> str | None:
    """Why this create's covered components carry no usable value, or `None` if they do.

    The second arm, for **plan time only**. The write surface answers the same question
    through AD051, against the assembled write rather than the identity alone, so it does not
    call this — see `unkeyed_create_coverage_reason`.

    A component that crosses a relationship is proven from the peer's own identity, which is
    where a plan holds it: peers are named by identity and never by a destination-assigned id.
    """
    identity = operation.identity
    human_friendly_id = list(getattr(node, "human_friendly_id", None) or ())
    if not human_friendly_id:
        return None
    valueless = sorted(
        component for component in human_friendly_id if not _usable(component_value(identity, component))
    )
    if not valueless:
        return None
    return (
        f"its identity names every human-friendly-ID component but supplies no usable value for {', '.join(valueless)}"
    )


def unkeyed_create_reason(operation: PlannedOperation, *, node: Any) -> str | None:
    """Both create arms, in order: identity coverage, then value presence.

    Plan derivation's entry point. The write surface composes the arms differently — coverage
    here, then AD051 for values — so that a valueless component is diagnosed by the mechanism
    that can name it.
    """
    return unkeyed_create_coverage_reason(operation, node=node) or unkeyed_create_value_reason(operation, node=node)


def refuse_unkeyed_create_coverage(operation: PlannedOperation, *, node: Any) -> None:
    """Raise where a create's identity cannot key it. The write surface's first arm."""
    _refuse(unkeyed_create_coverage_reason(operation, node=node), operation)


def refuse_unkeyed_create(operation: PlannedOperation, *, node: Any) -> None:
    """Raise where a create cannot be proven keyed. Plan derivation's entry point."""
    _refuse(unkeyed_create_reason(operation, node=node), operation)


def _refuse(reason: str | None, operation: PlannedOperation) -> None:
    """Raise `UnkeyedCreateRefusedError` for a non-`None` reason, in one wording."""
    if reason is None:
        return
    msg = (
        f"Operation {operation.operation_id!r} creates a {operation.kind!r} object that cannot be proven "
        f"to key itself: {reason}. A create is matched by the destination on the components its payload "
        "carries, and one that omits a component creates a second object instead of converging. The "
        "operation was refused and no destination write was attempted."
    )
    raise UnkeyedCreateRefusedError(msg)


def _refuse_destination_identity_collisions(*, kind: str, node: Any, creates: Sequence[PlannedOperation]) -> None:
    """Refuse where two or more creates project onto one destination human-friendly ID.

    Projected onto the **kind's actual** human-friendly ID rather than onto whichever key the
    merge warning picked as closest: the question is what the destination will converge, and
    only its own identity answers that.

    Creates alone. An update is keyed by its recorded destination id and cannot converge onto
    another operation's object, so counting it here would refuse a plan that is safe.
    """
    human_friendly_id = list(getattr(node, "human_friendly_id", None) or ())
    if not human_friendly_id or len(creates) < 2:
        return
    by_projection: dict[tuple[Any, ...], list[PlannedOperation]] = {}
    for operation in creates:
        projection = tuple(component_value(operation.identity, component) for component in human_friendly_id)
        if any(value is None for value in projection):
            continue
        by_projection.setdefault(projection, []).append(operation)
    collided = {projection: group for projection, group in by_projection.items() if len(group) > 1}
    if not collided:
        return
    detail = "; ".join(
        f"{', '.join(str(value) for value in projection)} <- "
        f"{', '.join(operation.operation_id for operation in sorted(group, key=lambda item: item.operation_id))}"
        for projection, group in sorted(collided.items(), key=lambda item: [str(part) for part in item[0]])
    )
    total = sum(len(group) for group in collided.values())
    msg = (
        f"{total} planned creates of destination kind {kind!r} project onto {len(collided)} destination "
        f"human-friendly ID(s) ({', '.join(human_friendly_id)}): {detail}. The destination cannot tell them "
        "apart, so applying them would converge them onto one object each and lose the rest silently."
    )
    raise DestinationIdentityCollisionError(msg)
