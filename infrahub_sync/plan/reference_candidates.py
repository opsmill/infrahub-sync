"""Mapped peer candidates for plan relationship resolution."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Mapping

    from infrahub_sync import SyncConfig


def _generic_peers(config: SyncConfig) -> Mapping[str, tuple[str, ...]]:
    runtime_models = getattr(config, "_runtime_models", None)
    return (
        runtime_models.generic_peers
        if runtime_models is not None
        else getattr(config, "_direct_generic_peers", None) or {}
    )


def literal_peer_kind_is_unambiguous(config: SyncConfig | None, kind: str, field: str, peer_kind: str) -> bool:
    """Require a generic's complete concrete set to prove a missing peer's kind.

    Filtering to mapped kinds bounds store probes, but cannot prove that an absent peer
    belongs to the remaining kind. Explicit concrete references retain their literal rule.
    """
    if config is None:
        return True
    generic_peers = _generic_peers(config)
    return all(
        set(generic_peers[mapped_field.reference]) == {peer_kind}
        for entry in config.schema_mapping
        if entry.name == kind
        for mapped_field in entry.fields
        if mapped_field.name == field and mapped_field.reference in generic_peers
    )


def reference_candidates(config: SyncConfig | None, kind: str) -> dict[str, tuple[str, ...]]:
    """Candidate peer kinds per reference-bearing field of `kind`, sorted (AD050).

    The candidate set for a field is every kind the configuration declares as that field's
    `reference` across **every** `schema_mapping` entry whose `name` is `kind` —
    `{LocationRack, LocationSite}` for `DcimDevice.location` on the qualified path. Sorted
    so the probe order, and therefore the wording of a failure, is deterministic.

    A `reference` that names a generic is expanded to the concrete kinds the destination
    schema lists for it and the configuration maps, the same set automatic ordering uses,
    because concrete-only mappings store no record under the generic's own name. A mapped
    generic remains a candidate too. The schema is the validated
    snapshot, or on a direct run the destination adapter's live schema.
    """
    if config is None:
        return {}
    generic_peers = _generic_peers(config)
    mapped = {entry.name for entry in config.schema_mapping}
    by_field: dict[str, set[str]] = {}
    for entry in config.schema_mapping:
        if entry.name != kind:
            continue
        for field in entry.fields:
            if field.reference:
                kinds = by_field.setdefault(field.name, set())
                if field.reference in generic_peers:
                    expanded = mapped.intersection(generic_peers[field.reference])
                    if field.reference in mapped:
                        expanded.add(field.reference)
                    # An empty expansion keeps the generic, so the peer still fails as unresolved.
                    kinds.update(expanded or {field.reference})
                else:
                    kinds.add(field.reference)
    return {name: tuple(sorted(kinds)) for name, kinds in by_field.items()}
