"""Placeholder peers for references to records a plan leaves out (spec 009 research R7).

The source store keeps every reference as the source holds it (research R2). Two kinds of
reference name a peer that is not in the store:

- a reference to a record the load could not build. The admission step recorded the record in
  the load report with its identity, and `peer_unique_id` returned the unique ID the record
  would have had;
- a reference through an optional relationship to a peer the source does not return.
  `peer_unique_id` returned `not_in_source_placeholder(peer_kind, peer_source_id)`.

Plan derivation resolves such a reference to a placeholder pair marked left out, instead of
refusing the plan. The selection step removes every placeholder from the operations, and the
plan writer refuses an operation that still holds one.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal

from infrahub_sync import NOT_IN_SOURCE_PLACEHOLDER_PREFIX, is_not_in_source_placeholder

if TYPE_CHECKING:
    from collections.abc import Sequence

    from infrahub_sync.plan.models import PlannedOperation

LEFT_OUT_PEER_KEY = "infrahub-sync:left-out-peer"
"""The only key of a placeholder peer identity. No mapping field can have this name."""

LeftOutPeerCause = Literal["not_loaded", "not_in_source"]
"""`not_loaded`: the load could not build the peer. `not_in_source`: the source does not return it."""


def left_out_peer_pair(*, peer_kind: str, cause: LeftOutPeerCause, source_id: str | None) -> dict[str, Any]:
    """Return the `{"peer_kind", "identity"}` pair that stands for a left-out peer."""
    marker: dict[str, Any] = {"cause": cause}
    if source_id is not None:
        marker["source_id"] = source_id
    return {"peer_kind": peer_kind, "identity": {LEFT_OUT_PEER_KEY: marker}}


def is_left_out_peer(identity: object) -> bool:
    """Tell whether a peer identity is the placeholder of `left_out_peer_pair`."""
    return isinstance(identity, Mapping) and LEFT_OUT_PEER_KEY in identity


def _holds_placeholder(value: object) -> bool:
    if isinstance(value, Mapping):
        return LEFT_OUT_PEER_KEY in value or any(_holds_placeholder(item) for item in value.values())
    if isinstance(value, (list, tuple)):
        return any(_holds_placeholder(item) for item in value)
    return False


def holds_left_out_peer(operation: PlannedOperation) -> bool:
    """Tell whether an operation's identity or relationships still name a left-out peer."""
    if _holds_placeholder(operation.identity):
        return True
    return any(_holds_placeholder(reference.peers) for reference in operation.relationships or ())


@dataclass(frozen=True)
class LeftOutPeers:
    """The source records the load could not build, by kind and unique ID.

    `by_kind` maps a kind to the unique IDs of its load report records that have an identity,
    each with its source record ID. A record of the load report without an identity cannot be
    referred to: `peer_unique_id` refuses a reference to it (`PeerNotLoadedError`).
    """

    by_kind: Mapping[str, Mapping[str, str | None]] = field(default_factory=dict)

    @classmethod
    def from_adapter(cls, adapter: Any) -> LeftOutPeers:  # any DiffSync adapter
        """Read the load report of `adapter`; an adapter without one has no left-out peers."""
        by_kind: dict[str, dict[str, str | None]] = {}
        for entry in getattr(adapter, "load_report", None) or ():
            # A refused duplicate has the unique ID of the record kept in the store.
            if entry.loaded or entry.identity is None or entry.reason == "duplicate_source_identity":
                continue
            model = getattr(adapter, entry.kind, None)
            if model is None:
                continue
            by_kind.setdefault(entry.kind, {})[model.create_unique_id(**entry.identity)] = entry.source_id
        return cls(by_kind=by_kind)

    def pair(self, *, candidates: Sequence[str], unique_id: object) -> dict[str, Any] | None:
        """Return the placeholder pair for a reference the store cannot resolve, or `None`.

        `None` when the reference names neither a peer marked "not in the source" nor exactly
        one candidate kind's load report record; derivation then refuses it as before.
        """
        if is_not_in_source_placeholder(unique_id):
            peer_kind, _, source_id = str(unique_id).removeprefix(NOT_IN_SOURCE_PLACEHOLDER_PREFIX).partition(":")
            return left_out_peer_pair(peer_kind=peer_kind, cause="not_in_source", source_id=source_id)
        if not isinstance(unique_id, str):
            return None
        hits = [kind for kind in candidates if unique_id in self.by_kind.get(kind, {})]
        if len(hits) != 1:
            return None
        (peer_kind,) = hits
        return left_out_peer_pair(peer_kind=peer_kind, cause="not_loaded", source_id=self.by_kind[peer_kind][unique_id])
