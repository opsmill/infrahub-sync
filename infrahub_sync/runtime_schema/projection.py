"""The one compatibility property: a canonical projection of consumed schema semantics.

The fingerprint is SHA-256 over this projection. It carries every fact a
registered configuration consumes — each configured kind, its effective DiffSync
identifiers, its ordered destination human-friendly ID and uniqueness-constraint
component paths, every mapped field's model- and write-affecting properties, and the
semantics of every mandatory-without-default field on those kinds, mapped or not,
because such a field can reject a retained create. Key paths that cross relationships
also consume the traversed members of otherwise unmapped peer kinds.

Everything else is compatible growth: an unrelated unmapped kind, an optional or
defaulted unmapped field, and any difference in snapshot delivery order leave the
projection — and so the fingerprint — unchanged.

Registered configuration validation and registered worker construction compute this
today. Recording it on a saved plan, and comparing a plan's recorded value against the
live schema before an apply writes, is the plan-guard unit that follows this one.
"""

from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING, Any, cast

from infrahub_sync.generator import get_identifiers, has_field
from infrahub_sync.plan.canonical import canonical_json_bytes

from .domain import NormalizedAttribute

if TYPE_CHECKING:
    from infrahub_sdk.schema import NodeSchema

    from infrahub_sync import SyncConfig

    from .domain import DestinationSchemaSnapshot, NormalizedKind, NormalizedRelationship


def _member_semantics(member: NormalizedAttribute | NormalizedRelationship) -> dict[str, Any]:
    """Project one member's model- and write-affecting properties."""
    if isinstance(member, NormalizedAttribute):
        return {
            "name": member.name,
            "role": "attribute",
            "kind": member.kind,
            "optional": member.optional,
            "default_value": member.default_value,
            "unique": member.unique,
            "read_only": member.read_only,
        }
    return {
        "name": member.name,
        "role": "relationship",
        "peer": member.peer,
        "cardinality": member.cardinality,
        "optional": member.optional,
        "kind": member.kind,
        "read_only": member.read_only,
    }


def _is_mandatory_without_default(member: NormalizedAttribute | NormalizedRelationship) -> bool:
    """Whether this member can reject a create the plan retained."""
    if member.optional:
        return False
    return member.default_value is None if isinstance(member, NormalizedAttribute) else True


def _peer_key_fields(
    node: NormalizedKind, configuration: SyncConfig, snapshot: DestinationSchemaSnapshot
) -> list[dict[str, Any]]:
    """Project peer members traversed by this kind's destination key paths."""
    references = {
        mapping.name: {field.name: field.reference for field in mapping.fields if field.reference}
        for mapping in configuration.schema_mapping
    }
    component_paths = (
        *node.human_friendly_id,
        *(component for key in node.uniqueness_constraints for component in key),
    )
    consumed: dict[tuple[str, str], dict[str, Any]] = {}
    for component in component_paths:
        current = node
        segments = component.split("__")
        for index, segment in enumerate(segments[:-1]):
            relationship = next((item for item in current.relationships if item.name == segment), None)
            if relationship is None:
                if index and segment != "value":
                    attribute = next((item for item in current.attributes if item.name == segment), None)
                    consumed[current.kind, segment] = {
                        "kind": current.kind,
                        "member": _member_semantics(attribute) if attribute is not None else None,
                    }
                break
            if index:
                consumed[current.kind, segment] = {"kind": current.kind, "member": _member_semantics(relationship)}
            peer_kind = references.get(current.kind, {}).get(segment) or relationship.peer
            peer = snapshot.kinds.get(peer_kind)
            if peer is None:
                consumed[peer_kind, ""] = {"kind": peer_kind, "member": None}
                break
            current = peer
    return [consumed[key] for key in sorted(consumed)]


def _kind_projection(
    node: NormalizedKind, configuration: SyncConfig, snapshot: DestinationSchemaSnapshot
) -> dict[str, Any]:
    """Project one consumed kind's identity, mapped fields, and mandatory fields."""
    members = (*node.attributes, *node.relationships)
    mapped = {
        member.name: member for member in members if has_field(config=configuration, name=node.kind, field=member.name)
    }
    identifiers = get_identifiers(node=cast("NodeSchema", node), config=configuration)
    return {
        "kind": node.kind,
        "present": True,
        "identifiers": list(identifiers) if identifiers else None,
        "human_friendly_id": list(node.human_friendly_id),
        # The outer list is sorted because the destination does not order its constraints
        # against each other; the components inside one constraint stay in declared order.
        "uniqueness_constraints": sorted(list(constraint) for constraint in node.uniqueness_constraints),
        "peer_key_fields": _peer_key_fields(node, configuration, snapshot),
        "fields": [_member_semantics(mapped[name]) for name in sorted(mapped)],
        "mandatory_without_default": [
            _member_semantics(member)
            for member in sorted(members, key=lambda item: item.name)
            if member.name not in mapped and _is_mandatory_without_default(member)
        ],
    }


def canonical_consumed_schema_projection(
    *, configuration: SyncConfig, snapshot: DestinationSchemaSnapshot
) -> list[dict[str, Any]]:
    """Project the schema semantics one configuration consumes, in a canonical order."""
    return [
        {"kind": mapping.name, "present": False}
        if mapping.name not in snapshot.kinds
        else _kind_projection(snapshot.kinds[mapping.name], configuration, snapshot)
        for mapping in sorted(configuration.schema_mapping, key=lambda mapping: mapping.name)
    ]


def compute_consumed_schema_fingerprint(*, configuration: SyncConfig, snapshot: DestinationSchemaSnapshot) -> str:
    """Return the full SHA-256 digest of the canonical consumed-semantics projection."""
    projection = canonical_consumed_schema_projection(configuration=configuration, snapshot=snapshot)
    return hashlib.sha256(canonical_json_bytes(projection)).hexdigest()
