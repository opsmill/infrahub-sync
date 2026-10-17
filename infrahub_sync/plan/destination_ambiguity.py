"""Destination nodes that share one configured identity (spec 009 `destination_identity_ambiguous`).

The destination store holds one model per identity: the DiffSync unique ID built from the
mapping's `identifiers`. When Infrahub holds two or more nodes with that identity, the plan
cannot tell which node a source record maps to. Decided by the product owner on 2026-10-17:
this is a plan problem, not a silent merge in which the last node loaded wins.

`DestinationIdentityAmbiguities.store` is the destination side's `store_item` hook. It keeps
the store as `update_or_add_model_instance` kept it before, so the load does not fail, and
records the node IDs of every identity that more than one node holds. The plan problem pass
reads them with `destination_identity_ambiguities`. A record holds the kind, the identity and
the node IDs only, never another attribute value (FR-027).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from diffsync.exceptions import ObjectNotFound

if TYPE_CHECKING:
    from diffsync import Adapter, DiffSyncModel


@dataclass(frozen=True)
class DestinationIdentityAmbiguity:
    """One identity that two or more destination nodes hold."""

    kind: str
    unique_id: str
    identifiers: dict[str, Any]
    node_ids: tuple[str, ...]


@dataclass
class _Seen:
    identifiers: dict[str, Any]
    node_ids: list[str] = field(default_factory=list)

    def add(self, node_id: object) -> None:
        if node_id is not None and str(node_id) not in self.node_ids:
            self.node_ids.append(str(node_id))


@dataclass
class DestinationIdentityAmbiguities:
    """Records, per adapter, the destination nodes that share one identity."""

    _seen: dict[tuple[str, str], _Seen] = field(default_factory=dict)

    def store(self, adapter: Adapter, item: DiffSyncModel) -> DiffSyncModel:
        """Store `item` as `update_or_add_model_instance` does, and record a second node with its identity."""
        kind, unique_id = item.get_type(), item.get_unique_id()
        try:
            existing = adapter.store.get(model=kind, identifier=unique_id)
        except ObjectNotFound:
            existing = None
        if existing is not None:
            seen = self._seen.setdefault((kind, unique_id), _Seen(identifiers=dict(item.get_identifiers())))
            seen.add(getattr(existing, "local_id", None))
            seen.add(getattr(item, "local_id", None))
        stored, _ = adapter.update_or_add_model_instance(item)
        return stored

    def ambiguities(self) -> list[DestinationIdentityAmbiguity]:
        """Return each identity that two or more distinct nodes hold, in load order."""
        return [
            DestinationIdentityAmbiguity(
                kind=kind, unique_id=unique_id, identifiers=seen.identifiers, node_ids=tuple(seen.node_ids)
            )
            for (kind, unique_id), seen in self._seen.items()
            if len(seen.node_ids) > 1
        ]


def destination_identity_ambiguities(destination: Any) -> list[DestinationIdentityAmbiguity]:
    """Return the ambiguities a destination adapter recorded, or none for an adapter that records none."""
    found = getattr(destination, "destination_identity_ambiguities", None)
    if not isinstance(found, list):
        return []
    return [item for item in found if isinstance(item, DestinationIdentityAmbiguity)]
