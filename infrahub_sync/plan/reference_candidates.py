"""Mapped peer candidates for plan relationship resolution."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Mapping

    from infrahub_sync import SyncConfig


def reference_candidates(config: SyncConfig | None, kind: str) -> dict[str, tuple[str, ...]]:
    """Candidate peer kinds per reference-bearing field of `kind`, sorted (AD050).

    The candidate set for a field is every kind the configuration declares as that field's
    `reference` across **every** `schema_mapping` entry whose `name` is `kind` —
    `{LocationRack, LocationSite}` for `DcimDevice.location` on the qualified path. Sorted
    so the probe order, and therefore the wording of a failure, is deterministic.

    A `reference` that names a generic is expanded to the concrete kinds the destination
    schema lists for it and the configuration maps, the same set automatic ordering uses,
    because no record is stored under the generic's own name. The schema is the validated
    snapshot, or on a direct run the destination adapter's live schema.
    """
    if config is None:
        return {}
    runtime_models = getattr(config, "_runtime_models", None)
    generic_peers: Mapping[str, tuple[str, ...]] = (
        runtime_models.generic_peers
        if runtime_models is not None
        else getattr(config, "_direct_generic_peers", None) or {}
    )
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
