"""The one compatibility property: a canonical projection of consumed schema semantics.

The fingerprint is SHA-256 over this projection. It carries every fact a
registered configuration consumes — each configured kind, its effective DiffSync
identifiers, its ordered destination human-friendly ID and uniqueness-constraint
component paths, every mapped field's model- and write-affecting properties, and the
semantics of every mandatory-without-default field on those kinds, mapped or not,
because such a field can reject a retained create.

Everything else is compatible growth: an unmapped kind, an optional or defaulted
unmapped field, and any difference in snapshot delivery order leave the projection —
and so the fingerprint — unchanged.

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
        }
    return {
        "name": member.name,
        "role": "relationship",
        "peer": member.peer,
        "cardinality": member.cardinality,
        "optional": member.optional,
        "kind": member.kind,
    }


def _is_mandatory_without_default(member: NormalizedAttribute | NormalizedRelationship) -> bool:
    """Whether this member can reject a create the plan retained."""
    if member.optional:
        return False
    return member.default_value is None if isinstance(member, NormalizedAttribute) else True


def _kind_projection(node: NormalizedKind, configuration: SyncConfig) -> dict[str, Any]:
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
        else _kind_projection(snapshot.kinds[mapping.name], configuration)
        for mapping in sorted(configuration.schema_mapping, key=lambda mapping: mapping.name)
    ]


def compute_consumed_schema_fingerprint(*, configuration: SyncConfig, snapshot: DestinationSchemaSnapshot) -> str:
    """Return the full SHA-256 digest of the canonical consumed-semantics projection."""
    projection = canonical_consumed_schema_projection(configuration=configuration, snapshot=snapshot)
    return hashlib.sha256(canonical_json_bytes(projection)).hexdigest()
