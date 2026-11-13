"""Resolve the references of a source record while an adapter reads it (spec 020 research R5).

Every adapter that resolves references while it reads (NetBox, Nautobot, ACI, generic REST and
the adapters built on it) calls `peer_unique_id` or `peer_unique_ids` from its `record_to_data`.
A peer is looked up by its kind and source record ID (`local_id`) among the stored records.
A peer of the kind being read that the read lists and has not admitted yet raises
`PeerNotAdmittedYetError`, and `DiffSyncMixin.load_records` admits the record again after the
rest of the kind. When the peer is not stored otherwise:

- through a required relationship, the helper raises `ExcludedRequiredPeerError` (a configured
  filter removed the peer), `PeerNotLoadedError` (the read could not build the peer) or
  `PeerNotFoundError` (the source does not return the peer); the admission step records the
  record in the load report and leaves it out;
- through an optional relationship, the record is kept: a peer the source does not return gives
  a placeholder value and a load report entry, a peer the read could not build gives no value
  and a load report entry, and a peer a filter removed gives no value.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, get_args

from infrahub_sync import (
    ExcludedRequiredPeerError,
    PeerNotAdmittedYetError,
    PeerNotFoundError,
    PeerNotLoadedError,
    PeerReferenceError,
    is_not_in_source_placeholder,
    not_in_source_placeholder,
)
from infrahub_sync.load_report import LoadReportEntry
from infrahub_sync.plan.models import SourceRecordReference

if TYPE_CHECKING:
    from collections.abc import Iterable

    from infrahub_sync import DiffSyncMixin

logger = logging.getLogger(__name__)


def reference_is_required(model: type[Any], field_name: str) -> bool:
    """Tell whether a relationship field needs a peer.

    It does when its annotation does not accept `None`, or when it is one of the mapping's
    identifiers: a record cannot form its identity without that peer.
    """
    model_field = getattr(model, "model_fields", {}).get(field_name)
    if model_field is None:
        return False
    return field_name in getattr(model, "_identifiers", ()) or type(None) not in get_args(model_field.annotation)


def peer_unique_id(
    adapter: DiffSyncMixin,
    peer_kind: str,
    peer_source_id: Any,
    *,
    owner: tuple[str, str | None],
    field: str,
    required: bool,
) -> str | None:
    """Resolve one reference of the record `owner` (kind, source record ID) to its peer's unique ID.

    See the module docstring for a peer that is not stored.
    """
    source_id = str(peer_source_id)
    unique_id = adapter.stored_peer_unique_id(peer_kind, source_id)
    if unique_id is not None:
        return unique_id

    owner_kind, owner_source_id = owner
    in_cycle = adapter.peer_is_pending(peer_kind, source_id)
    if in_cycle and not adapter.wait_is_released(owner_kind, owner_source_id):
        # A record of the kind being read that is listed and not admitted yet (a parent after
        # its child): `load_records` admits the referring record again later.
        raise PeerNotAdmittedYetError(peer_kind=peer_kind, peer_source_id=source_id)
    details: dict[str, Any] = {
        "kind": owner_kind,
        "source_id": owner_source_id,
        "field": field,
        "peer_kind": peer_kind,
        "peer_source_id": source_id,
        "in_cycle": in_cycle,
    }
    not_loaded = adapter.not_loaded_entry(peer_kind, source_id)
    if not_loaded is not None:
        identity = not_loaded.record.identity
        if not_loaded.reason == "duplicate_source_identity" and identity is not None:
            # The store kept the first record of this identity; the reference names that identity.
            return getattr(adapter, peer_kind).create_unique_id(**identity)
        if required:
            raise PeerNotLoadedError(**details)
        _report(adapter, owner, field=field, reason="peer_not_loaded", peer_kind=peer_kind, peer_source_id=source_id)
        return None
    filtered, _ = adapter.filtered_peer_position(peer_kind, source_id)
    if filtered:
        if required:
            raise ExcludedRequiredPeerError(**details)
        logger.warning(
            "A configured filter removed the peer of %s record %s field %s (peer kind %s, source ID %s)",
            owner_kind,
            owner_source_id,
            field,
            peer_kind,
            source_id,
        )
        return None
    if required:
        raise PeerNotFoundError(**details)
    _report(
        adapter,
        owner,
        field=field,
        reason="peer_not_found_in_source",
        peer_kind=peer_kind,
        peer_source_id=source_id,
        in_cycle=in_cycle,
    )
    return not_in_source_placeholder(peer_kind, source_id)


def peer_unique_ids(
    adapter: DiffSyncMixin,
    peer_kind: str,
    peer_source_ids: Iterable[Any],
    *,
    owner: tuple[str, str | None],
    field: str,
    required: bool,
) -> list[str]:
    """Resolve the peers of a cardinality-many reference to their sorted unique IDs.

    Each peer resolves as an optional reference of `peer_unique_id`. A required relationship
    needs at least one peer that resolves to a record; when none does, the error of the first
    unresolved peer is raised: `PeerNotFoundError`, then `PeerNotLoadedError`, then
    `ExcludedRequiredPeerError`.
    """
    resolved: list[str] = []
    not_in_source: list[str] = []
    not_loaded: list[str] = []
    excluded: list[str] = []
    start = len(adapter.load_report.entries)
    for peer_source_id in peer_source_ids:
        source_id = str(peer_source_id)
        unique_id = peer_unique_id(adapter, peer_kind, source_id, owner=owner, field=field, required=False)
        if unique_id is None:
            if adapter.filtered_peer_position(peer_kind, source_id)[0]:
                excluded.append(source_id)
            else:
                not_loaded.append(source_id)
        elif is_not_in_source_placeholder(unique_id):
            not_in_source.append(source_id)
            resolved.append(unique_id)
        else:
            resolved.append(unique_id)
    if required and len(resolved) == len(not_in_source):
        error: type[PeerReferenceError] | None = None
        first = ""
        if not_in_source:
            error, first = PeerNotFoundError, not_in_source[0]
        elif not_loaded:
            error, first = PeerNotLoadedError, not_loaded[0]
        elif excluded:
            error, first = ExcludedRequiredPeerError, excluded[0]
        if error is not None:
            in_cycle = any(
                entry.in_cycle and entry.peer is not None and entry.peer.source_id == first
                for entry in adapter.load_report.entries[start:]
            )
            # The entries written for the optional lookups above describe a record that is left out.
            del adapter.load_report.entries[start:]
            owner_kind, owner_source_id = owner
            raise error(
                kind=owner_kind,
                source_id=owner_source_id,
                field=field,
                peer_kind=peer_kind,
                peer_source_id=first,
                in_cycle=in_cycle,
            )
    return sorted(resolved)


def _report(
    adapter: DiffSyncMixin,
    owner: tuple[str, str | None],
    *,
    field: str,
    reason: str,
    peer_kind: str,
    peer_source_id: str,
    in_cycle: bool = False,
) -> None:
    owner_kind, owner_source_id = owner
    adapter.report_reference(
        LoadReportEntry(
            record=SourceRecordReference(kind=owner_kind, source_id=owner_source_id),
            reason=reason,  # ty: ignore[invalid-argument-type] -- one of the two reference reasons
            field=field,
            peer=SourceRecordReference(kind=peer_kind, source_id=peer_source_id),
            loaded=True,
            in_cycle=in_cycle,
        )
    )


__all__ = ["peer_unique_id", "peer_unique_ids", "reference_is_required"]
