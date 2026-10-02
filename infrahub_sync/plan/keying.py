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

import logging
from collections.abc import Collection, Mapping
from hashlib import sha256
from itertools import starmap
from operator import itemgetter
from typing import TYPE_CHECKING, Any

from infrahub_sync.plan.canonical import canonical_json_bytes
from infrahub_sync.plan.errors import DestinationIdentityCollisionError, UnkeyedCreateRefusedError

if TYPE_CHECKING:
    from collections.abc import Sequence

    from infrahub_sync.plan.models import PlannedOperation

# Separator between the segments of a *schema* component path — `name__value` for a direct
# attribute, `site__name__value` for one that crosses a relationship. The only thing split on
# it here is a schema path; a DiffSync unique id is never split.
COMPONENT_PATH_SEPARATOR = "__"
logger = logging.getLogger(__name__)


def require_infrahub_create_schema(*, destination: Any, operations: Sequence[PlannedOperation]) -> None:
    """Refuse Infrahub creates when the cached schema cannot prove their match keys."""
    if getattr(destination, "type", None) != "Infrahub":
        return
    schema = getattr(destination, "schema", None) or {}
    missing = sorted({op.kind for op in operations if op.action == "create" and schema.get(op.kind) is None})
    if not missing:
        return
    msg = (
        f"Infrahub creates of kind(s) {', '.join(missing)} cannot be checked because their destination schema "
        "is unavailable. The whole plan was refused before any destination write."
    )
    raise UnkeyedCreateRefusedError(msg, next_action="Load the destination schema, then re-run `diff`.")


def _identity_paths(identity: Mapping[str, Any], prefix: str = "") -> set[str]:
    """Name identity leaves, including fields inside nested peer identities."""
    paths: set[str] = set()
    for field, value in identity.items():
        path = f"{prefix}{field}"
        if isinstance(value, Mapping) and isinstance(value.get("identity"), Mapping):
            paths.update(_identity_paths(value["identity"], f"{path}__"))
        else:
            paths.add(f"{path}__value")
    return paths


def refuse_source_identity_collisions(
    *,
    kind: str,
    node: Any,
    creates: Sequence[PlannedOperation],
    identities: Sequence[tuple[str, Mapping[str, Any]]],
    declared_identity: Collection[str],
) -> None:
    """Refuse a create matching a distinct loaded source identity, even without a diff.

    Only the human-friendly ID matches a converging create. Uniqueness constraints
    may reject a write but do not provide an alternative upsert match key.
    """
    components = tuple(getattr(node, "human_friendly_id", None) or ())
    if not components or not creates:
        return
    population: dict[bytes, dict[bytes, tuple[str, Mapping[str, Any]]]] = {}
    missing: set[str] = set()
    for identifier, identity in identities:
        values = [component_value(identity, component) for component in components]
        absent = {component for component, value in zip(components, values, strict=True) if not _usable(value)}
        if absent:
            missing.update(absent)
            continue
        projection = canonical_json_bytes(values, kind=kind)
        population.setdefault(projection, {})[canonical_json_bytes(identity, kind=kind)] = (identifier, identity)
    if missing:
        logger.warning(
            "Plan: source population for destination kind %s supplies no value for match component(s) %s; "
            "those records cannot be compared for convergence and are allowed to proceed",
            kind,
            ", ".join(sorted(missing)),
        )
    for operation in creates:
        values = [_create_component_value(operation, component) for component in components]
        if not all(_usable(value) for value in values):
            continue
        group = population.get(canonical_json_bytes(values, kind=kind), {})
        own = canonical_json_bytes(operation.identity, kind=kind)
        others = [record for encoded, record in group.items() if encoded != own]
        if not others:
            continue
        paths = _identity_paths(operation.identity)
        for _, identity in others:
            paths.update(_identity_paths(identity))
        indistinguishable = ", ".join(sorted(paths - set(components))) or "distinct source identities"
        ids = ", ".join(starmap(_source_record_identifier, sorted(others, key=itemgetter(0))[:2]))
        msg = (
            f"Create {operation.operation_id!r} of destination kind {kind!r} shares its actual destination "
            f"match key (human-friendly ID: {', '.join(components)}) with a distinct loaded source record. "
            f"Declared source identity (mapping identifiers): {', '.join(sorted(declared_identity))}. "
            f"The destination cannot distinguish: {indistinguishable}. Other loaded source record identifiers: {ids}. "
            f"{_collision_values(components, operation)}. Applying the create could replace that record's data. "
            "The whole plan was refused before any destination write."
        )
        raise DestinationIdentityCollisionError(msg)


def _source_record_identifier(identifier: str, identity: Mapping[str, Any]) -> str:
    """Show store ids containing only names; otherwise show an opaque identity fingerprint."""
    if all(path.split(COMPONENT_PATH_SEPARATOR)[-2] == "name" for path in _identity_paths(identity)):
        return repr(identifier)
    fingerprint = sha256(canonical_json_bytes(identity)).hexdigest()[:16]
    return f"source identity fingerprint {fingerprint} (non-name values withheld)"


def _component_field(component: str) -> str:
    """The mapping field name a schema component path starts with.

    Infrahub writes a human-friendly-ID component and a uniqueness-constraint component as
    a path — `name__value` for a direct attribute, `site__name__value` for one that crosses
    a relationship — while the plan's destination identity is keyed by the mapping field
    name, which is the path's first segment.
    """
    return component.split(COMPONENT_PATH_SEPARATOR, 1)[0]


def _writable_component(
    component: str,
    node: Any,
    schemas: Mapping[str, Any] | None,
    identity: Mapping[str, Any] | None = None,
    references: Mapping[str, Mapping[str, str]] | None = None,
) -> bool:
    """Whether a schema path can be recreated by a destination write."""
    field, _, rest = component.partition(COMPONENT_PATH_SEPARATOR)
    for attribute in getattr(node, "attributes", ()):
        if attribute.name == field:
            computed = getattr(attribute, "computed_attribute", None)
            return (
                rest in ("", "value")
                and not getattr(attribute, "read_only", False)
                and (computed is None or getattr(computed, "kind", None) == "User")
            )
    for relationship in getattr(node, "relationships", ()):
        if relationship.name == field:
            if getattr(relationship, "read_only", False):
                return False
            if not rest or schemas is None:
                return True
            reference = identity.get(field) if identity is not None else None
            peer_kind = reference.get("peer_kind") if isinstance(reference, Mapping) else None
            configured_peer = (references or {}).get(node.kind, {}).get(field)
            peer = schemas.get(peer_kind or configured_peer or relationship.peer)
            if peer is None:
                return False
            peer_identity = reference.get("identity") if isinstance(reference, Mapping) else None
            return _writable_component(
                rest, peer, schemas, peer_identity if isinstance(peer_identity, Mapping) else None, references
            )
    # Older schema fixtures expose keys but no member list. Keep their existing behavior.
    return not hasattr(node, "attributes")


def writable_convergence_reason(
    *,
    node: Any,
    identity_fields: Collection[str],
    mapped_fields: Collection[str] | None = None,
    schemas: Mapping[str, Any] | None = None,
    identity: Mapping[str, Any] | None = None,
    check_values: bool = False,
    write_values: Mapping[str, Any] | None = None,
    references: Mapping[str, Mapping[str, str]] | None = None,
) -> str | None:
    """Explain why no complete destination key can be recreated from mapped identity fields.

    ``write_values`` maps fields to resolved write values; a peer ID there proves that the
    relationship has a write value. No product caller passes it: planned callers pass
    ``identity``, whose relationship components carry the peer's nested identity and are
    checked component by component.
    """
    keys = [list(getattr(node, "human_friendly_id", None) or ())]
    keys.extend(list(key) for key in (getattr(node, "uniqueness_constraints", None) or ()))
    unusable: set[str] = set()
    unwritable: set[str] = set()
    for key in keys:
        if not key:
            continue
        unwritable.update(
            component for component in key if not _writable_component(component, node, schemas, identity, references)
        )
        missing = {
            component
            for component in key
            if _component_field(component) not in identity_fields
            or (mapped_fields is not None and _component_field(component) not in mapped_fields)
            or not _writable_component(component, node, schemas, identity, references)
            or (check_values and not _usable(_convergence_value(component, identity, write_values)))
        }
        if not missing:
            return None
        unusable.update(missing)
    detail = ", ".join(sorted(unusable)) or "no declared destination key"
    explanation = (
        "Read-only values (including server-allocated values) and server-computed values "
        "cannot be recreated from mapped source values. "
        if unwritable
        else ""
    )
    return (
        f"no complete writable destination convergence identity; unusable components: {detail}. "
        f"{explanation}Map and select every component of a writable destination uniqueness constraint"
    )


def _convergence_value(
    component: str, identity: Mapping[str, Any] | None, write_values: Mapping[str, Any] | None
) -> Any:
    """Get a plan component or the resolved write value of its root field."""
    if write_values is not None:
        return write_values.get(_component_field(component))
    return component_value(identity, component) if identity is not None else None


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


def unkeyed_create_coverage_reason(
    operation: PlannedOperation, *, node: Any, schemas: Mapping[str, Any] | None = None
) -> str | None:
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
    if any(
        not _writable_component(component, node, schemas, identity)
        for key in [
            getattr(node, "human_friendly_id", None) or (),
            *(getattr(node, "uniqueness_constraints", None) or ()),
        ]
        for component in key
    ):
        return writable_convergence_reason(node=node, identity_fields=identity, schemas=schemas, identity=identity)
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


def unkeyed_create_value_reason(
    operation: PlannedOperation, *, node: Any, schemas: Mapping[str, Any] | None = None
) -> str | None:
    """Why this create's covered components carry no usable value, or `None` if they do.

    The second arm, for **plan time only**. The write surface answers the same question
    through AD051, against the assembled write rather than the identity alone, so it does not
    call this — see `unkeyed_create_coverage_reason`.

    A component that crosses a relationship is proven from the peer's own identity, which is
    where a plan holds it: peers are named by identity and never by a destination-assigned id.
    """
    identity = operation.identity
    human_friendly_id = list(getattr(node, "human_friendly_id", None) or ())
    if human_friendly_id and any(
        not _writable_component(component, node, schemas, identity) for component in human_friendly_id
    ):
        return writable_convergence_reason(
            node=node, identity_fields=identity, identity=identity, schemas=schemas, check_values=True
        )
    if not human_friendly_id:
        return None
    valueless = sorted(
        component for component in human_friendly_id if not _usable(component_value(identity, component))
    )
    if not valueless:
        return None
    return f"its identity supplies no usable value for human-friendly-ID component(s) {', '.join(valueless)}"


def unkeyed_create_reason(
    operation: PlannedOperation, *, node: Any, schemas: Mapping[str, Any] | None = None
) -> str | None:
    """Both create arms, in order: identity coverage, then value presence.

    Plan derivation's entry point. The write surface composes the arms differently — coverage
    here, then AD051 for values — so that a valueless component is diagnosed by the mechanism
    that can name it.
    """
    return unkeyed_create_coverage_reason(operation, node=node, schemas=schemas) or unkeyed_create_value_reason(
        operation, node=node, schemas=schemas
    )


def refuse_unkeyed_create_coverage(
    operation: PlannedOperation, *, node: Any, schemas: Mapping[str, Any] | None = None
) -> None:
    """Raise where a create's identity cannot key it. The write surface's first arm."""
    _refuse(unkeyed_create_coverage_reason(operation, node=node, schemas=schemas), operation)


def refuse_unkeyed_create(operation: PlannedOperation, *, node: Any, schemas: Mapping[str, Any] | None = None) -> None:
    """Raise where a create cannot be proven keyed. Plan derivation's entry point."""
    _refuse(unkeyed_create_reason(operation, node=node, schemas=schemas), operation)


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


def _uniqueness_rules(node: Any, schemas: Mapping[str, Any]) -> list[tuple[str, tuple[str, ...]]]:
    """Return the kind's HFID, unique attributes, and composite constraints."""
    rules: list[tuple[str, tuple[str, ...]]] = []
    hfid = tuple(getattr(node, "human_friendly_id", None) or ())
    if hfid:
        rules.append(("human-friendly ID", hfid))
    seen_attributes: set[str] = set()
    seen_constraints: set[tuple[str, ...]] = set()
    seen_parents: set[str] = set()
    pending = [node]
    while pending:
        current = pending.pop()
        for attribute in getattr(current, "attributes", ()):
            if getattr(attribute, "unique", False) and attribute.name not in seen_attributes:
                rules.append(("unique attribute", (f"{attribute.name}__value",)))
                seen_attributes.add(attribute.name)
        for constraint in getattr(current, "uniqueness_constraints", None) or ():
            components = tuple(constraint)
            if components and components not in seen_constraints:
                rules.append(("uniqueness constraint", components))
                seen_constraints.add(components)
        for parent in getattr(current, "inherit_from", ()) or ():
            inherited = schemas.get(parent)
            if inherited is not None and parent not in seen_parents:
                pending.append(inherited)
                seen_parents.add(parent)
    return rules


def _create_component_value(operation: PlannedOperation, component: str) -> Any:
    """Read a direct write value from the payload, or a peer path from identity."""
    field, _, rest = component.partition(COMPONENT_PATH_SEPARATOR)
    payload = operation.payload or {}
    if rest in ("", "value") and field in payload:
        return payload[field]
    if not rest and isinstance(operation.identity.get(field), Mapping):
        return operation.identity[field]
    return component_value(operation.identity, component)


def _collision_values(components: tuple[str, ...], operation: PlannedOperation) -> str:
    """Show bounded name values; other unique fields may contain credentials."""
    if len(components) > 3 or any(
        component.split(COMPONENT_PATH_SEPARATOR)[-2:] != ["name", "value"] for component in components
    ):
        return "values withheld (rule contains a non-name field)"
    values = [_create_component_value(operation, component) for component in components]
    if any(not isinstance(value, str) for value in values):
        return "values withheld (rule contains a non-text value)"
    return ", ".join(f"{component}={value[:80]!r}" for component, value in zip(components, values, strict=True))


def _unique_generic_attributes(node: Any, schema: Mapping[str, Any]) -> set[tuple[str, str]]:
    """Find unique attributes shared by every kind inheriting each generic."""
    found: set[tuple[str, str]] = set()
    pending = list(getattr(node, "inherit_from", ()) or ())
    seen: set[str] = set()
    while pending:
        parent = pending.pop()
        if parent in seen:
            continue
        seen.add(parent)
        generic = schema.get(parent)
        if generic is None:
            continue
        found.update(
            (parent, attribute.name)
            for attribute in getattr(generic, "attributes", ())
            if getattr(attribute, "unique", False)
        )
        pending.extend(getattr(generic, "inherit_from", ()) or ())
    return found


def _refuse_cross_kind_generic_collisions(
    *, by_kind: Mapping[str, list[PlannedOperation]], schema: Mapping[str, Any]
) -> None:
    """Refuse creates matched across kinds by a shared generic's unique attribute."""
    by_generic_rule: dict[tuple[str, str], list[PlannedOperation]] = {}
    for kind, creates in by_kind.items():
        node = schema.get(kind)
        if node is None:
            continue
        for rule in _unique_generic_attributes(node, schema):
            by_generic_rule.setdefault(rule, []).extend(creates)
    for (generic, attribute), creates in sorted(by_generic_rule.items()):
        if len({operation.kind for operation in creates}) < 2:
            continue
        component = f"{attribute}__value"
        by_projection: dict[bytes, list[PlannedOperation]] = {}
        for operation in creates:
            value = _create_component_value(operation, component)
            if _usable(value):
                by_projection.setdefault(canonical_json_bytes([value], kind=generic), []).append(operation)
        for group in by_projection.values():
            if len({operation.kind for operation in group}) < 2:
                continue
            kinds = ", ".join(sorted({operation.kind for operation in group}))
            ids = ", ".join(sorted(operation.operation_id for operation in group[:2]))
            msg = (
                f"Planned creates of destination kinds {kinds} collide on inherited unique attribute "
                f"{generic}.{component}, {_collision_values((component,), group[0])}: {ids}. "
                "Applying them could match one generic object across kinds. "
                "The whole plan was refused before any destination write."
            )
            raise DestinationIdentityCollisionError(msg)


def refuse_destination_identity_collisions(
    *, schema: Mapping[str, Any], operations: Sequence[PlannedOperation]
) -> None:
    """Refuse creates sharing any declared destination uniqueness rule before a write."""
    by_kind: dict[str, list[PlannedOperation]] = {}
    for operation in operations:
        if operation.action == "create":
            by_kind.setdefault(operation.kind, []).append(operation)
    for kind, creates in sorted(by_kind.items()):
        node = schema.get(kind)
        if node is None or len(creates) < 2:
            continue
        for rule_name, components in _uniqueness_rules(node, schema):
            by_projection: dict[bytes, list[PlannedOperation]] = {}
            for operation in creates:
                values = [_create_component_value(operation, component) for component in components]
                if not all(_usable(value) for value in values):
                    continue
                projection = canonical_json_bytes(values, kind=kind)
                by_projection.setdefault(projection, []).append(operation)
            collided = [group for group in by_projection.values() if len(group) > 1]
            if not collided:
                continue
            group = collided[0]
            ids = ", ".join(sorted(operation.operation_id for operation in group[:2]))
            if len(group) > 2:
                ids += f", and {len(group) - 2} more"
            msg = (
                f"Planned creates of destination kind {kind!r} collide on {rule_name} "
                f"({', '.join(components)}), {_collision_values(components, group[0])}: {ids}. "
                "Applying them could merge distinct planned objects "
                "into one destination object. The whole plan was refused before any destination write."
            )
            raise DestinationIdentityCollisionError(msg)
    _refuse_cross_kind_generic_collisions(by_kind=by_kind, schema=schema)
