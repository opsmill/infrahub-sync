"""Recording a relationship peer that exists only at the destination (AD050, AD052).

Plan derivation resolves a reference's peer kind by probing the **source** store, because
the mapping alone is ambiguous (AD046). A peer the source can never hold therefore refuses
the plan — and Infrahub's built-in `default` IP namespace is exactly that peer: NetBox has
no VRF named `default`, so every prefix and address with no VRF names a namespace no source
extract produces.

This module answers the one question that lets such a peer be recorded literally instead:
is a literal identity provably **the** identity the destination matches that kind on? It is
the only part of derivation that reads the destination's schema, which is why it lives here
rather than beside the source-store probe that raises the refusal.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from dataclasses import field as dataclass_field
from typing import TYPE_CHECKING, Any

from infrahub_sync.plan.canonical import canonical_json_bytes, canonical_value
from infrahub_sync.plan.identity import canonical_identity
from infrahub_sync.plan.keying import is_usable_component_value

if TYPE_CHECKING:
    from collections.abc import Callable

    from infrahub_sync import SyncConfig

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DestinationOnlyPeers:
    """What one derivation needs to record a destination-only peer literally.

    `hfid` answers the destination's human-friendly ID for one kind, or `None` where the
    destination declares no key for it — the one fact the source side cannot supply.

    `warned` de-duplicates the plan-time warning across the whole derivation: every no-VRF
    prefix and address names the same namespace, so a real plan would otherwise flood the
    log. It holds canonical **bytes** rather than the values themselves, because `False == 0`
    and `True == 1 == 1.0` in Python — a set of raw values would silently collapse two
    references that the artifact encodes, and the destination therefore matches, as
    different literals.
    """

    hfid: Callable[[str], tuple[str, ...] | None]
    warned: set[bytes] = dataclass_field(default_factory=set)


def destination_only_peers(destination_adapter: Any) -> DestinationOnlyPeers | None:
    """Read the destination's schema once per derivation, or `None` where it exposes none.

    `schema` is defined on the Infrahub adapter and on no other while derivation runs for
    every destination, so its absence is ordinary rather than an error (AD052): it leaves
    the rule unavailable, and every missing peer is refused exactly as before.
    """
    schema = getattr(destination_adapter, "schema", None)
    if not schema:
        return None

    def hfid(kind: str) -> tuple[str, ...] | None:
        node = schema.get(kind) if hasattr(schema, "get") else None
        components = getattr(node, "human_friendly_id", None) if node is not None else None
        return tuple(components) if components else None

    return DestinationOnlyPeers(hfid=hfid)


def _sole_direct_identifier(config: SyncConfig | None, kind: str) -> str | None:
    """The one direct field every `schema_mapping` entry for `kind` identifies it by.

    `None` unless the mapping is unanimous. A kind may be declared by more than one entry —
    the shipped NetBox example declares `DcimDevice` twice — so reading the identifier off
    whichever entry came first would make the answer depend on mapping order. A composite
    identifier names only part of an identity, and a reference-bearing one names another
    peer rather than a value, so neither can be carried by the single value a field holds.
    """
    entries = [entry for entry in (config.schema_mapping if config else []) if entry.name == kind]
    if not entries:
        return None
    identifiers = set()
    for entry in entries:
        # An entry may declare no identifiers at all, which names no identity to record.
        declared_identifiers = entry.identifiers or []
        if len(declared_identifiers) != 1:
            return None
        identifier = declared_identifiers[0]
        declared = next((candidate for candidate in entry.fields if candidate.name == identifier), None)
        if declared is None or declared.reference:
            return None
        identifiers.add(identifier)
    return identifiers.pop() if len(identifiers) == 1 else None


def destination_only_identity(
    *,
    config: SyncConfig | None,
    peer_kind: str,
    unique_id: Any,
    owning_kind: str,
    field: str,
    peers: DestinationOnlyPeers | None,
) -> dict[str, Any] | None:
    """The literal identity naming a peer that exists only at the destination, or `None`.

    `None` means "not this case", and the caller's refusal stands. The identity is recorded
    only where a literal provably **is** the identity the destination matches on, which takes
    three conditions on top of the caller's single-candidate check:

    1. every `schema_mapping` entry for that kind identifies it by the same single direct
       field, so the field's one value carries the whole identity;
    2. the destination's human-friendly ID for that kind is exactly that field, so what is
       recorded here is what `PeerResolver` will match on at apply;
    3. the value survives canonicalisation as a scalar the artifact can encode and the
       destination can match. The type check comes first because `is_usable_component_value`
       rejects only `None`, empty and whitespace-only strings — `0` and `False` stay valid.
       A non-finite float passes both that check and the canonical-value table but is refused
       by `canonical_json_bytes`, which would move the failure to `operation_id`, where
       nothing names the reference that caused it.

    Nothing weaker is admitted: with the peer absent from every source bucket there is no
    object to probe, so anything the destination key does not pin down would be a guess.
    """
    if peers is None:
        return None

    identifier_field = _sole_direct_identifier(config, peer_kind)
    if identifier_field is None or peers.hfid(peer_kind) != (f"{identifier_field}__value",):
        return None

    value = canonical_value(unique_id, kind=peer_kind, field=identifier_field)
    if not isinstance(value, (str, int, float)):
        return None
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if not is_usable_component_value(value):
        return None

    warning_key = canonical_json_bytes([owning_kind, field, peer_kind, value])
    if warning_key not in peers.warned:
        peers.warned.add(warning_key)
        logger.warning(
            "Plan: field %s of kind %s references the peer %r of kind %s, which no candidate kind holds in the "
            "loaded source store. It is recorded as a literal destination identity and resolved at apply by the "
            "destination's human-friendly ID for that kind",
            field,
            owning_kind,
            value,
            peer_kind,
        )
    return {"peer_kind": peer_kind, "identity": canonical_identity({identifier_field: value}, kind=peer_kind)}
