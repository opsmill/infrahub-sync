"""Resolve source references through the adapter base class (spec 009 research R4).

Every adapter that resolves references while loading calls these helpers from its
`record_to_data`. They wrap `DiffSyncMixin.peer_unique_id`, which decides what a reference to a
peer that is not in the store becomes.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, get_args

from infrahub_sync import ExcludedRequiredPeerError

if TYPE_CHECKING:
    from collections.abc import Iterable

    from diffsync import DiffSyncModel

    from infrahub_sync import DiffSyncMixin


def reference_is_required(model: type[DiffSyncModel], field_name: str) -> bool:
    """Tell whether a relationship field needs a peer: its annotation does not accept None."""
    field = model.model_fields.get(field_name)
    return field is not None and type(None) not in get_args(field.annotation)


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
    filter removed. A peer that is not in the source resolves to a placeholder, as for an
    optional cardinality-one reference. When every peer of a required relationship was removed
    by a filter, the record cannot be built, so `ExcludedRequiredPeerError` is raised.
    """
    resolved: list[str] = []
    excluded: list[str] = []
    for peer_source_id in peer_source_ids:
        unique_id = adapter.peer_unique_id(peer_kind, str(peer_source_id), owner=owner, field=field, required=False)
        if unique_id is None:
            excluded.append(str(peer_source_id))
        else:
            resolved.append(unique_id)
    if required and excluded and not resolved:
        raise ExcludedRequiredPeerError(
            kind=owner[0], source_id=owner[1], field=field, peer_kind=peer_kind, peer_source_id=excluded[0]
        )
    return sorted(resolved), excluded
