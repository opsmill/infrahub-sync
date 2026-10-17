"""Resolve source references through the adapter base class (spec 009 research R4).

Every adapter that resolves references while loading calls these helpers from its
`record_to_data`. They wrap `DiffSyncMixin.peer_unique_id`, which decides what a reference to a
peer that is not in the store becomes.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, get_args

from infrahub_sync import (
    ExcludedRequiredPeerError,
    PeerNotFoundError,
    PeerNotLoadedError,
    PeerReferenceError,
    is_not_in_source_placeholder,
)

if TYPE_CHECKING:
    from collections.abc import Iterable

    from diffsync import DiffSyncModel

    from infrahub_sync import DiffSyncMixin


def reference_is_required(model: type[DiffSyncModel], field_name: str) -> bool:
    """Tell whether a relationship field needs a peer.

    It does when its annotation does not accept None, or when it is one of the mapping's
    identifiers: a record cannot form its identity without that peer.
    """
    field = model.model_fields.get(field_name)
    if field is None:
        return False
    return field_name in getattr(model, "_identifiers", ()) or type(None) not in get_args(field.annotation)


def owner_kind(model: type[DiffSyncModel], default: str) -> str:
    """Return the kind the load report names for a record of `model`."""
    return getattr(model, "_modelname", default)


def resolve_peer_list(
    adapter: DiffSyncMixin,
    *,
    peer_kind: str,
    peer_source_ids: Iterable[Any],  # source IDs of any scalar type; converted with str()
    owner: tuple[str, str | None],
    field: str,
    required: bool,
) -> tuple[list[str], list[str]]:
    """Resolve the peers of a cardinality-many reference.

    Returns the sorted unique IDs of the peers and the source IDs of the peers a configured
    filter removed. A peer that is not in the source resolves to a placeholder, and a peer in
    the load report without a known identity gives no value, as for an optional
    cardinality-one reference. A required relationship needs at least one peer that resolves
    to a record: when none does, the record cannot be built, so this raises
    `PeerNotFoundError` when a peer is not in the source, `PeerNotLoadedError` when a peer was
    not loaded, and `ExcludedRequiredPeerError` when a configured filter removed every peer.
    """
    resolved: list[str] = []
    excluded: list[str] = []
    not_in_source: list[str] = []
    not_loaded: list[str] = []
    for peer_source_id in peer_source_ids:
        source_id = str(peer_source_id)
        unique_id = adapter.peer_unique_id(peer_kind, source_id, owner=owner, field=field, required=False)
        if unique_id is None:
            if source_id in adapter.excluded_peer_ids(peer_kind):
                excluded.append(source_id)
            else:
                not_loaded.append(source_id)
        else:
            resolved.append(unique_id)
            if is_not_in_source_placeholder(unique_id):
                not_in_source.append(source_id)
    if required and len(resolved) == len(not_in_source):
        error: type[PeerReferenceError] | None = None
        if not_in_source:
            error, peer_source_id = PeerNotFoundError, not_in_source[0]
        elif not_loaded:
            error, peer_source_id = PeerNotLoadedError, not_loaded[0]
        elif excluded:
            error, peer_source_id = ExcludedRequiredPeerError, excluded[0]
        if error is not None:
            raise error(
                kind=owner[0], source_id=owner[1], field=field, peer_kind=peer_kind, peer_source_id=peer_source_id
            )
    return sorted(resolved), excluded
