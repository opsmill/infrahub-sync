from __future__ import annotations

import copy
import ipaddress
import logging
import os
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from diffsync import Adapter, DiffSyncModel
from infrahub_sdk import (
    Config,
    InfrahubClientSync,
)
from infrahub_sdk.exceptions import NodeNotFoundError
from infrahub_sdk.graphql import Mutation
from infrahub_sdk.node.property import NodeProperty
from infrahub_sdk.schema.main import GenericSchemaAPI, NodeSchemaAPI, RelationshipSchemaAPI
from infrahub_sdk.utils import compare_lists
from pydantic import ValidationError
from typing_extensions import Self

from infrahub_sync import (
    DiffSyncMixin,
    DiffSyncModelMixin,
    SyncAdapter,
    SyncConfig,
)
from infrahub_sync.cache.cursors import CursorState, CursorTier
from infrahub_sync.generator import has_field
from infrahub_sync.plan.canonical import canonical_json_bytes
from infrahub_sync.plan.errors import (
    PeerAmbiguousError,
    PeerNotFoundError,
    SkippedDeleteOperation,
    UnaccountedIdentityComponentError,
    UnkeyedWriteRefusedError,
)
from infrahub_sync.plan.identity import canonical_identity
from infrahub_sync.plan.models import DestinationBindingRecord

logger = logging.getLogger(__name__)


def resolved_endpoint(settings: Mapping[str, Any], branch: str | None) -> tuple[str | None, str | None]:
    """The effective `(url, branch)` this adapter connects with — env vars before settings.

    One resolution for both consumers: the SDK client's construction and the plan's
    destination binding. The environment-over-settings precedence is
    the whole point of recording the *effective* values — the config-version digest covers
    the parsed YAML only, and the repo's own guidance keeps credentials and addresses in
    environment variables, exactly where that digest is blind.
    """
    url = os.environ.get("INFRAHUB_ADDRESS") or os.environ.get("INFRAHUB_URL") or settings.get("url")
    return url, settings.get("branch") or branch


# GraphQL filter kwarg for timestamp-based incremental queries.
# Verified against a live Infrahub via __type introspection — every node
# exposes the metadata-prefixed `node_metadata__updated_at__after` arg.
# Adjust if the server renames this field.
_TIMESTAMP_FILTER_KW = "node_metadata__updated_at__after"

if TYPE_CHECKING:
    from collections.abc import Iterator, MutableMapping, Sequence

    from infrahub_sdk.node import InfrahubNodeSync, RelatedNodeSync, RelationshipManagerSync
    from infrahub_sdk.schema import MainSchemaTypesAPI
    from infrahub_sdk.store import NodeStoreSync

    from infrahub_sync.plan.models import PlannedOperation


def _node_has_complete_attributes(node: InfrahubNodeSync) -> bool:
    """Check if a node has all its non-optional attributes populated."""
    for attr_schema in node._schema.attributes:
        if attr_schema.optional:
            continue
        attr = getattr(node, attr_schema.name, None)
        if attr is None or attr.value is None:
            return False
    return True


def resolve_peer_node(
    key: str,
    rel_schema: RelationshipSchemaAPI,
    peer_schema: MainSchemaTypesAPI,
    store: NodeStoreSync,
    client: InfrahubClientSync | None = None,
    fallback: bool | None = False,
) -> InfrahubNodeSync | None:
    """
    Resolve a peer node given a key.

    Resolution logic:
      - If peer_schema is not a GenericSchemaAPI, try fetching the node from the store using rel_schema.peer.
      - If it is a GenericSchemaAPI, iterate over its `used_by` list and return the first matching node.
      - If not found and fallback is enabled, use the client to fetch the node.
      - If node is found but has incomplete attributes, re-fetch from Infrahub.

    Returns the found peer node or None.
    """
    peer_node = None
    if not isinstance(peer_schema, GenericSchemaAPI):
        peer_node = store.get(key=key, kind=rel_schema.peer, raise_when_missing=False)
    else:
        for used_by in peer_schema.used_by:
            peer_node = store.get(key=key, kind=used_by, raise_when_missing=False)
            if peer_node and peer_node.get_kind() == used_by:
                break

    # Check if the node from store has incomplete attributes and needs re-fetching
    if peer_node and fallback and client and not _node_has_complete_attributes(peer_node):
        peer_node = client.get(id=key, kind=peer_node.get_kind(), populate_store=True)

    if not peer_node and fallback and client is not None:
        logger.warning("Unable to find %s [%s] in Store - Fallback to Infrahub", rel_schema.peer, key)
        peer_node = client.get(id=key, kind=rel_schema.peer, populate_store=True)
        if not peer_node:
            logger.warning("Unable to find %s [%s] - Ignored", rel_schema.peer, key)
    return peer_node


def update_node(
    node: InfrahubNodeSync,
    attrs: Mapping[str, Any],
    source: str | None = None,
    owner: str | None = None,
) -> InfrahubNodeSync:
    """
    Update the given node using the provided attributes and relationship values.

    For relationship attributes, the function uses `resolve_peer_node` or `resolve_peer_nodes`
    to update one-to-one and one-to-many relationships, respectively.

    Args:
        node: The node to update.
        attrs: The attributes and relationships to update.
        source: Optional source ID to set on updated attributes.
        owner: Optional owner ID to set on updated attributes.
    """
    schemas: Mapping[str, MainSchemaTypesAPI] = node._client.schema.all(branch=node._branch)
    for attr_name, attr_value in attrs.items():
        if attr_name in node._schema.attribute_names:
            attr = getattr(node, attr_name)
            attr.value = attr_value
            if source:
                attr.source = NodeProperty(data=source)
            if owner:
                attr.owner = NodeProperty(data=owner)

        if attr_name in node._schema.relationship_names:
            for rel_schema in node._schema.relationships:
                peer_schema = schemas.get(rel_schema.peer)
                if attr_name != rel_schema.name or peer_schema is None:
                    continue

                if rel_schema.cardinality == "one":
                    if attr_value:
                        peer_node = resolve_peer_node(
                            key=attr_value,
                            rel_schema=rel_schema,
                            peer_schema=peer_schema,
                            store=node._client.store,
                            client=node._client,
                            fallback=False,
                        )
                        if not peer_node:
                            logger.warning("Unable to find %s [%s] in the Store - Ignored", rel_schema.peer, attr_value)
                            continue
                        setattr(node, attr_name, peer_node)
                    else:
                        # TODO: delete the old relationship data ?
                        pass

                elif rel_schema.cardinality == "many":
                    attr_manager: RelationshipManagerSync = getattr(node, attr_name)
                    existing_peer_ids = attr_manager.peer_ids
                    new_peer_ids = []

                    for value in list(attr_value):
                        peer_node = resolve_peer_node(
                            key=value,
                            rel_schema=rel_schema,
                            peer_schema=peer_schema,
                            store=node._client.store,
                            client=node._client,
                            fallback=False,
                        )
                        if peer_node:
                            new_peer_ids.append(peer_node.id)

                    _, existing_only, new_only = compare_lists(existing_peer_ids, new_peer_ids)

                    if not attr_manager.initialized:
                        attr_manager.fetch()

                    for existing_id in existing_only:
                        attr_manager.remove(existing_id)

                    for new_id in new_only:
                        attr_manager.add(new_id)

    return node


def _flush_replaced_relationship_sets(node: InfrahubNodeSync, rel_names: Sequence[str]) -> None:
    """Issue the plan's cardinality-many peer sets on `node`, and nothing else (AD088).

    THE FLUSH. A **targeted relationship write**: a hand-built `<kind>Update` carrying the
    node's `id` plus only the fields named in `rel_names`, each rendered from the manager the
    create payload built — which already holds exactly the plan's resolved peer set, with the
    same per-peer `source`/`owner`/`is_protected` metadata the upsert carried.

    **Peer removal relies on the destination Update mutation's replace semantics, pinned by
    the live shrink test** (`tests/integration/test_infrahub_replace_set_shrink_integration.py`).
    Nothing about a *removal* can reach the wire from here: the SDK's
    `RelationshipManagerBase._generate_input_data` renders only the surviving peer list —
    `[{id: ...}, ...]` with no removal directive — so a fetch-and-reconcile round trip before
    this write added nothing. Under replace semantics the written list *is* the destination's
    new set (AD085's `peers: []` empties it); if the destination ever merged instead, no
    in-process reconciliation could have removed a peer either, and the pinned test is what
    would catch the change. The round trips were therefore simplified away: this path issues
    **no destination read** — which also removes the SDK's `populate_store=True` peer-hydration
    batch the forced-cold `fetch()` used to trigger.

    **Why it does not re-render the node.** The obvious flush — `node.update(...)` — renders the
    whole node through `InfrahubNodeBase._generate_input_data`, and that render emits
    `data[<rel>] = None` for **every** uninitialized optional cardinality-one relationship once
    the node is marked existing (the SDK's own comment says
    it is there "to allow clearing relationships"). The convergent upsert marks the node existing,
    so any re-render clears every optional cardinality-one relationship the plan never mapped —
    and FR-013 requires an update payload to touch no unmapped destination field. The null goes
    out under both render modes: with unmodified-field stripping **off** nothing is stripped at
    all, and with it **on** the field survives both stripping loops anyway — the first loop does
    not pop it, and the second never visits it because an unmapped field is absent from the
    original data it compares against. So no flag on `update()` avoids this; only not re-rendering
    does.

    Restoring or pre-initialising the unmapped relationships before the flush would treat the
    symptom, and would have to read every one of them from the destination to do it. Writing only
    the fields being replaced removes the exposure instead.

    **The peer list is still rendered by the SDK.** `RelationshipManagerBase._generate_input_data`
    is what `_generate_input_data` would have called for these same fields, so the value written
    here is byte-identical to what the full render produced for them — including `[]` for a peer
    set the plan records as empty, which is the case AD085 exists for. `id` is set last, mirroring the
    SDK's own ordering, so the write targets this node.

    The mutation is built and issued the way `InfrahubNodeSync.update` builds and issues its own
    and the response is handed back to the SDK, so nothing about transport,
    error handling or the mutation's response shape changes.
    """
    node_id = _require_node_id(node, context="for the replace-set flush")
    data: dict[str, Any] = {}
    for rel_name in rel_names:
        manager: RelationshipManagerSync = getattr(node, rel_name)
        data[rel_name] = manager._generate_input_data()
    data["id"] = node_id

    mutation_name = f"{node._schema.kind}Update"
    query = Mutation(
        mutation=mutation_name,
        input_data={"data": data},
        query=node._generate_mutation_query(),
    )
    response = node._client.execute_graphql(
        query=query.render(),
        branch_name=node._branch,
        tracker=f"mutation-{str(node._schema.kind).lower()}-update",
    )
    node._process_mutation_result(mutation_name=mutation_name, response=response)


# An Infrahub schema component path — a human-friendly-ID or uniqueness-constraint entry —
# is written as `name__value` for a direct attribute and `site__name__value` for one that
# crosses a relationship. A **schema path** is split on this separator; a **data value**
# never is, which is the v1 flaw this outcome exists to avoid (PD-004).
_COMPONENT_PATH_SEPARATOR = "__"
_COMPONENT_VALUE_SUFFIX = "value"

# Distinguishes "the identity supplies no value for this path" from "it supplies None".
_UNRESOLVED = object()


def _hfid_components(node_schema: Any) -> list[str]:
    """The kind's human-friendly-ID component paths, empty when it declares none."""
    return list(getattr(node_schema, "human_friendly_id", None) or ())


def _component_segments(component: str) -> list[str]:
    """Field segments of a schema component path, with a trailing `value` dropped.

    `name__value` is one segment, `site__name__value` is two. A single-segment path is a
    **direct** component; more than one **crosses a relationship** (AD051).
    """
    segments = component.split(_COMPONENT_PATH_SEPARATOR)
    if len(segments) > 1 and segments[-1] == _COMPONENT_VALUE_SUFFIX:
        segments = segments[:-1]
    return segments


def _filter_kwarg_name(component: str) -> str:
    """The GraphQL filter kwarg a schema component path is queried by (PD-004)."""
    if component.rsplit(_COMPONENT_PATH_SEPARATOR, maxsplit=1)[-1] == _COMPONENT_VALUE_SUFFIX:
        return component
    return f"{component}{_COMPONENT_PATH_SEPARATOR}{_COMPONENT_VALUE_SUFFIX}"


def _require_node_id(node: InfrahubNodeSync, *, context: str) -> str:
    """The destination id of a node the SDK has round-tripped, as a `str`.

    `InfrahubNodeSync.id` is optional because a node built locally has none until it is
    written or read back. Every caller here holds a node the destination has just returned,
    so an absent id is an SDK invariant violation rather than an operator error, and it is
    surfaced as one instead of being carried onward as `None`.
    """
    node_id = node.id
    if not isinstance(node_id, str) or not node_id:
        msg = f"The destination returned no node id {context}, so the node cannot be referred to."
        raise TypeError(msg)
    return node_id


def _nested_peer_identity(value: Any) -> Mapping[str, Any] | None:
    """The identity inside a nested `{"peer_kind", "identity"}` pair, or None (AD043).

    A cardinality-many reference records a **list** of such pairs, and a component path
    cannot cross into a list without naming which peer, so that shape yields nothing here.
    """
    if isinstance(value, Mapping) and isinstance(value.get("identity"), Mapping):
        return value["identity"]
    return None


def _identity_path_value(identity: Mapping[str, Any], segments: Sequence[str]) -> Any:
    """Walk `segments` through a destination identity, recursing into nested pairs (AD043).

    Returns `_UNRESOLVED` when the identity supplies no value for the path — including
    when it supplies `None`, or when the path has to cross a component that is not a
    nested `{"peer_kind", "identity"}` pair. Recursion is what keeps a data value from
    ever being split on `__`.
    """
    if not segments:
        return _UNRESOLVED
    head, rest = segments[0], segments[1:]
    if head not in identity:
        return _UNRESOLVED
    value = identity[head]
    if not rest:
        return _UNRESOLVED if value is None else value
    nested = _nested_peer_identity(value)
    if nested is None:
        return _UNRESOLVED
    return _identity_path_value(nested, rest)


def _operation_peer_identity(operation: PlannedOperation, field: str) -> Mapping[str, Any] | None:
    """The nested peer identity the operation records for `field` (AD043, AD051).

    Read from the operation's own **identity** first, where an identity-bearing reference
    is recorded as a `{"peer_kind", "identity"}` pair, and otherwise from the relationship
    reference of that name, whose `peers` hold those identities directly. A
    cardinality-many reference names no single peer, so it supplies no component.
    """
    if field in operation.identity:
        nested = _nested_peer_identity(operation.identity[field])
        if nested is not None:
            return nested
    for reference in operation.relationships or ():
        if reference.field == field and reference.cardinality == "one" and reference.peers:
            return reference.peers[0]
    return None


def _hfid_component_accounted_for(
    *,
    component: str,
    data: Mapping[str, Any],
    operation: PlannedOperation,
) -> bool:
    """Whether one human-friendly-ID component is accounted for by the write (AD051).

    Per component **shape**, because "resolves against the create data" is not
    implementable: by the time this runs, a relationship key in `data` holds a resolved
    destination node id and no attribute can be read out of it.

    - a **direct** component (`<attr>` / `<attr>__value`): `data` carries `<attr>` non-null;
    - a **relationship-crossing** component (`<rel>__<attr>__value`): `data` carries `<rel>`
      non-null **and** the operation's nested `{peer_kind, identity}` for `<rel>` supplies
      `<attr>`, recursing through the nested identity for deeper paths.
    """
    segments = _component_segments(component)
    if not segments:
        return False
    field = segments[0]
    if data.get(field) is None:
        return False
    if len(segments) == 1:
        return True
    nested = _operation_peer_identity(operation, field)
    if nested is None:
        return False
    return _identity_path_value(nested, segments[1:]) is not _UNRESOLVED


class PeerResolver:
    """Resolve a plan's peer identities to destination node ids, for one apply (FR-014).

    A memo keyed on `(kind, canonical identity)`, populated from every completed
    create/update so an operation's own result resolves later operations that refer to it.
    Its lifetime is **one apply**: it is created at the start and discarded with it, and
    nothing about it is persisted.

    It never reads `client.store` or the DiffSync store. That store dependency is exactly
    what `resolve_peer_node` above has and what a saved-plan apply cannot satisfy, since a
    saved plan is applied without loading either side (FR-012, DBR-007) — which is also why
    the destination query below passes `populate_store=False`.

    Failed lookups and failed writes are **never** memoized, so a later operation referring
    to the same peer re-attempts resolution rather than inheriting a negative result
    (AD036).
    """

    def __init__(self, adapter: InfrahubAdapter) -> None:
        self._adapter = adapter
        # The canonical identity is unhashable, so the memo key carries its canonical JSON
        # encoding — the same normalization the operation identifier hashes (FR-028.3).
        self._memo: dict[tuple[str, bytes], str] = {}
        # Kinds whose partial filter has been warned about, once per kind per apply — the
        # same lifetime rule as the adapter's unkeyed-render report (AD078), and it holds
        # here for the same reason: the resolver lives for exactly one apply.
        self._partial_filter_reported: set[str] = set()

    @staticmethod
    def _key(kind: str, identity: Mapping[str, Any]) -> tuple[str, bytes]:
        """The memo key for one peer, in the plan's canonical identity form."""
        return (kind, canonical_json_bytes(canonical_identity(identity, kind=kind)))

    def remember(self, kind: str, identity: Mapping[str, Any], node_id: str) -> None:
        """Memoize a **completed** write's destination node id (FR-014)."""
        self._memo[self._key(kind, identity)] = node_id

    def resolve(self, *, peer_kind: str, identity: Mapping[str, Any], referring_operation_id: str) -> str:
        """Resolve one peer identity to one destination node id (FR-014, AD058).

        The only entry point. Cardinality is the **caller's** concern, read off the
        relationship reference: this resolves one identity to one id and knows nothing
        about the shape of the field it is being resolved for.

        Raises:
            PeerNotFoundError: the peer identity matches no destination object.
            PeerAmbiguousError: it matches more than one.
        """
        key = self._key(peer_kind, identity)
        memoized = self._memo.get(key)
        if memoized is not None:
            return memoized
        node_id = self._query(
            peer_kind=peer_kind,
            identity=identity,
            referring_operation_id=referring_operation_id,
        )
        # Only a successful lookup is memoized (AD036).
        self._memo[key] = node_id
        return node_id

    def _filter_kwargs(self, *, peer_kind: str, identity: Mapping[str, Any]) -> dict[str, Any]:
        """Build the destination query's filter kwargs from the peer kind's HFID (PD-004).

        An `<attr>__value` path takes its value from the identity's scalar under `<attr>`;
        an `<rel>__<attr>__value` path takes its value from the nested
        `{peer_kind, identity}` pair the identity records under `<rel>`, read at
        `identity[<attr>]` and recursing for deeper nesting (AD043). The **schema path** is
        split; the **data value** never is.

        Two degraded cases, and neither is silent — a too-loose query can match **exactly
        one** node and bind it wrongly, so `_query`'s zero- and multi-match refusals are not
        a defense against degradation:

        - a component whose value the identity does not supply is **skipped**, and the skip
          is disclosed by a per-kind apply-time warning naming the dropped components. The
          specified remedy for a kind whose HFID does not cover its plan identity is the
          `<rel>__ids` fallback — resolve the reference component's own peer first,
          recursively through this same resolver, then filter `<rel>__ids=[<id>]` — which
          this release does not implement; FR-024 already warns at plan time about exactly
          this condition, and this is its apply-time counterpart;
        - a kind that declares no usable HFID component at all falls back to the identity's
          own direct scalars as `<attr>__value` filters, which is the only thing the apply
          holds for it. When even those yield nothing, the returned mapping is empty and
          `_query` refuses before querying: an unfiltered query lists every node of the
          kind, and with exactly one at the destination it would bind silently.

        Raises:
            ValueError: the destination schema declares no kind `peer_kind`. The
                operation path raises on an unknown kind before writing; resolving a peer
                against a kind the destination does not know is the same condition, so it is
                refused just as loudly instead of silently degrading to the scalar fallback.

                Deliberately outside `OPERATIONAL_APPLY_FAILURES`, which routes it to the
                CLI's defect arm — so the message states the diagnosis and **prescribes no
                remedy**. That arm already tells the operator this is a defect rather
                than a destination refusal and not to re-plan on the assumption the
                destination is at fault; a remedy here would contradict it in the same ERROR
                line. It ends without a full stop for the same reason, because the arm's
                format string supplies one — the sibling guard in
                `apply_planned_operation` is worded on both counts.
        """
        node_schema = self._adapter.schema.get(peer_kind)
        if node_schema is None:
            msg = (
                f"The destination schema declares no kind {peer_kind!r}, so no peer of that kind "
                "can be resolved. The plan was derived against a configuration or schema this "
                "destination does not carry"
            )
            raise ValueError(msg)
        components = _hfid_components(node_schema)

        kwargs: dict[str, Any] = {}
        dropped: list[str] = []
        for component in components:
            value = _identity_path_value(identity, _component_segments(component))
            if value is _UNRESOLVED or isinstance(value, (Mapping, list, tuple)):
                dropped.append(component)
                continue
            kwargs[_filter_kwarg_name(component)] = value

        if not kwargs:
            for name, value in identity.items():
                if value is None or isinstance(value, (Mapping, list, tuple)):
                    continue
                kwargs[_filter_kwarg_name(name)] = value

        if kwargs and dropped and peer_kind not in self._partial_filter_reported:
            self._partial_filter_reported.add(peer_kind)
            logger.warning(
                "Planned apply: resolving %s peers on a PARTIAL filter. The plan identity supplies no "
                "value for human-friendly-ID component(s) %s, so they were dropped and the destination "
                "is queried on %s — a strict subset of the kind's convergence key, which can match a "
                "single wrong node and bind it. Re-plan so the identity supplies the dropped "
                "component(s) (FR-024's plan-time warning names the same condition).",
                peer_kind,
                ", ".join(dropped),
                sorted(kwargs),
            )
        return kwargs

    def _query(self, *, peer_kind: str, identity: Mapping[str, Any], referring_operation_id: str) -> str:
        """Query the destination for one peer, refusing on zero and on more than one.

        The refusals belong to **this** resolver only (AD048). The live `sync` write path's
        warn-and-continue on an unresolvable peer, and the SDK's bare `IndexError` on a
        multi-match, are existing behavior on an existing path and are left exactly as they
        are.

        An **empty** filter set is refused before the query is issued: an
        unfiltered `client.filters(kind=...)` lists every node of the kind, and with exactly
        one node at the destination it returns it — the one shape the zero- and multi-match
        refusals cannot catch, and silent wrong-peer wiring if it binds.

        That refusal overrides `PeerNotFoundError`'s class-level next action (the AD082
        pattern). The generic one — "create the peer at the destination" — is wrong for
        **this** condition and dangerous: nothing here established that the peer is absent,
        only that no filter could be derived to look for it, so the peer very likely exists
        already and creating it would duplicate it. The remedy is the identity, not the
        destination.
        """
        filter_kwargs = self._filter_kwargs(peer_kind=peer_kind, identity=identity)
        readable = canonical_json_bytes(canonical_identity(identity, kind=peer_kind)).decode("utf-8")
        if not filter_kwargs:
            msg = (
                f"No usable destination filter could be derived from the peer identity {readable} of "
                f"kind {peer_kind!r}, referenced by operation {referring_operation_id!r}: every value "
                "the identity supplies is reference-shaped or null. An unfiltered query would list "
                f"every {peer_kind!r} object and could silently bind the wrong one, so the peer is "
                "refused instead."
            )
            raise PeerNotFoundError(
                msg,
                next_action=(
                    f"Whether a {peer_kind!r} peer exists at the destination was never established, so "
                    "do not create one — that would duplicate it. Re-plan so that kind's identity "
                    "carries at least one direct attribute value, or add one to its `identifiers` in "
                    "the schema mapping."
                ),
            )
        results = self._adapter.client.filters(
            kind=peer_kind,
            populate_store=False,
            **filter_kwargs,
        )

        if len(results) == 1:
            return _require_node_id(results[0], context=f"for the single {peer_kind!r} matching {readable}")
        if not results:
            msg = (
                f"No object of kind {peer_kind!r} at the destination matches the peer identity "
                f"{readable}, referenced by operation {referring_operation_id!r}. Queried with: "
                f"{sorted(filter_kwargs)}."
            )
            raise PeerNotFoundError(msg)
        msg = (
            f"{len(results)} objects of kind {peer_kind!r} at the destination match the peer identity "
            f"{readable}, referenced by operation {referring_operation_id!r}, so the peer is ambiguous. "
            f"Queried with: {sorted(filter_kwargs)}."
        )
        raise PeerAmbiguousError(msg)


def diffsync_to_infrahub(
    ids: Mapping[Any, Any],
    attrs: Mapping[Any, Any],
    store: NodeStoreSync,
    node_schema: NodeSchemaAPI,
    schemas: Mapping[str, MainSchemaTypesAPI],
) -> dict[Any, Any]:
    """
    Convert DiffSync IDs and attributes into a format suitable for Infrahub.

    Resolves relationship fields using peer node lookup logic.
    """
    data: dict[Any, Any] = copy.deepcopy(dict(ids))
    data.update(dict(attrs))

    for key in list(data.keys()):
        if key in node_schema.relationship_names:
            for rel_schema in node_schema.relationships:
                peer_schema = schemas.get(rel_schema.peer)
                if key != rel_schema.name or peer_schema is None:
                    continue

                if rel_schema.cardinality == "one":
                    if data[key] is None:
                        del data[key]
                        continue
                    peer_node = resolve_peer_node(
                        key=data[key],
                        rel_schema=rel_schema,
                        peer_schema=peer_schema,
                        store=store,
                    )
                    if not peer_node:
                        logger.warning("Unable to find %s [%s] in the Store - Ignored", rel_schema.peer, data[key])
                        continue
                    data[key] = peer_node.id

                elif rel_schema.cardinality == "many":
                    if data[key] is None:
                        del data[key]
                        continue
                    new_values = []
                    for value in list(data[key]):
                        peer_node = resolve_peer_node(
                            key=value,
                            rel_schema=rel_schema,
                            peer_schema=peer_schema,
                            store=store,
                        )
                        if not peer_node:
                            logger.warning("Unable to find %s [%s] in the Store - Ignored", rel_schema.peer, value)
                            continue
                        new_values.append(peer_node.id)
                    data[key] = new_values
    return data


class PeerIdentifierError(ValueError):
    """Raised when an Infrahub peer node is missing a value required to build its DiffSync identifier.

    Carries enough context (parent kind/id, relationship name, peer kind/id, missing keys,
    identifiers schema, values that were present) for the user to fix the schema_mapping
    or seed the missing data without re-running the failing job.
    """

    def __init__(
        self,
        *,
        parent_kind: str,
        parent_id: str | None,
        rel_name: str,
        peer_kind: str,
        peer_id: str | None,
        identifiers: tuple[str, ...],
        missing_keys: tuple[str, ...],
        present_keys: tuple[str, ...],
    ) -> None:
        self.parent_kind = parent_kind
        self.parent_id = parent_id
        self.rel_name = rel_name
        self.peer_kind = peer_kind
        self.peer_id = peer_id
        self.identifiers = identifiers
        self.missing_keys = missing_keys
        self.present_keys = present_keys
        msg = (
            f"Cannot build unique_id for peer {peer_kind}[{peer_id}] "
            f"(relationship {parent_kind}.{rel_name}, parent id={parent_id}): "
            f"missing identifier key(s) {list(missing_keys)}; "
            f"required identifiers={list(identifiers)}, present keys={list(present_keys)}. "
            "Likely cause: schema_mapping does not declare a 'fields:' entry for the missing "
            "key, or the peer record was not loaded with that field populated. "
            "Re-run with --continue-on-error to skip these peers."
        )
        super().__init__(msg)


class InfrahubAdapter(DiffSyncMixin, Adapter):
    type = "Infrahub"

    continue_on_error: bool = False

    # AD078 — destination kinds whose rendered mutation has already been reported as
    # unkeyed. The report is once per kind, not once per operation, because
    # `apply_planned_operation` is entered once per operation and a four-thousand-operation
    # apply would otherwise emit one line per row. The set is created at the start of an
    # apply and discarded with it, the same lifetime as `PeerResolver`'s memo — which is why
    # `new_peer_resolver` allocates it and `__init__` does not: an adapter instance can serve
    # more than one apply in-process, and a set living for the instance would suppress the
    # second apply's report of a kind the first already named. `None` is the not-in-an-apply
    # state, which the report site allocates into for a caller that dispatches an operation
    # without going through the resolver factory.
    _unkeyed_render_reported: set[str] | None = None

    def __init__(
        self,
        target: str,
        adapter: SyncAdapter,
        config: SyncConfig,
        branch: str | None = None,
        *args,
        **kwargs,
    ) -> None:
        super().__init__(*args, **kwargs)
        self.target = target
        self.config = config

        settings = adapter.settings or {}
        infrahub_url, infrahub_branch = resolved_endpoint(settings, branch)
        infrahub_token = os.environ.get("INFRAHUB_API_TOKEN") or settings.get("token")
        verify_ssl = settings.get("verify_ssl")

        if not infrahub_url or not infrahub_token:
            msg = "Both url and token must be specified!"
            raise ValueError(msg)

        # The effective destination identity a plan of this adapter is bound to — the
        # resolved URL and branch, never the token. The branch falls
        # back to "main" because that is the SDK's `default_branch` when none is set, so
        # the record names the branch actually written to.
        self.destination_binding = DestinationBindingRecord(url=infrahub_url, branch=infrahub_branch or "main")

        sdk_config: dict[str, Any] = {"timeout": 60, "api_token": infrahub_token}
        if infrahub_branch:
            sdk_config["default_branch"] = infrahub_branch
        if verify_ssl is not None:
            sdk_config["tls_insecure"] = not verify_ssl

        self.client = InfrahubClientSync(address=infrahub_url, config=Config(**sdk_config))

        # Resolve source and owner nodes for lineage tracking
        # Default: use CoreAccount matching config.source.name
        # Override: if source/owner specified in destination settings, use CoreAccountGroup
        remote_account = config.source.name
        try:
            default_account = self.client.get(kind="CoreAccount", name__value=remote_account)
        except NodeNotFoundError:
            default_account = None

        # Resolve source - group if specified in settings, else default account
        source_setting = settings.get("source")
        if source_setting:
            try:
                self.source_node = self.client.get(kind="CoreAccountGroup", hfid=[source_setting])
            except NodeNotFoundError:
                logger.warning("CoreAccountGroup '%s' not found for source, falling back to account", source_setting)
                self.source_node = default_account
        else:
            self.source_node = default_account

        # Resolve owner - group if specified in settings, else default account
        owner_setting = settings.get("owner")
        if owner_setting:
            try:
                self.owner_node = self.client.get(kind="CoreAccountGroup", hfid=[owner_setting])
            except NodeNotFoundError:
                logger.warning("CoreAccountGroup '%s' not found for owner, falling back to account", owner_setting)
                self.owner_node = default_account
        else:
            self.owner_node = default_account

        # We will keep a copy of the schema
        self.schema: MutableMapping[str, MainSchemaTypesAPI] = self.client.schema.all(branch=infrahub_branch)

    def cursor_tier_for(self, model_name: str) -> CursorTier:
        """TIMESTAMP for any kind present in the live Infrahub schema.

        Every Infrahub node carries `node_metadata.updated_at`, so the
        `node_metadata__updated_at__after` filter works for any kind the
        destination schema knows about. Kinds absent from `self.schema`
        fall back to NONE — defensive guard so the engine never attempts
        an incremental query for an unknown kind.
        """
        if model_name in self.schema:
            return CursorTier.TIMESTAMP
        return CursorTier.NONE

    def list_changed_since(self, model_name: str, cursor: CursorState) -> Iterator[dict]:
        """Yield Infrahub nodes changed since `cursor.value`.

        Uses the `node_metadata__updated_at__after` GraphQL filter
        (see `_TIMESTAMP_FILTER_KW`).
        """
        if model_name not in self.schema:
            msg = f"Infrahub: model {model_name!r} not in schema; cursor tier NONE"
            raise NotImplementedError(msg)

        filter_kwargs = {_TIMESTAMP_FILTER_KW: cursor.value}
        nodes = self.client.filters(  # ty: ignore[no-matching-overload]
            kind=model_name,
            populate_store=True,
            prefetch_relationships=True,
            **filter_kwargs,
        )
        for node in nodes:
            yield self.infrahub_node_to_diffsync(node=node)

    def list_existing_ids(self, model_name: str) -> Iterator[str]:
        """Yield unique IDs for all Infrahub nodes of `model_name`.

        Used by soft-delete sweeps: timestamp-filtered queries miss DELETEs,
        so an occasional ID-only scan catches removed peers.
        """
        if model_name not in self.schema:
            msg = f"Infrahub: model {model_name!r} not in schema; cursor tier NONE"
            raise NotImplementedError(msg)

        model_cls = getattr(self, model_name, None)
        if model_cls is None:
            msg = f"Infrahub: adapter has no model class for {model_name!r}"
            raise NotImplementedError(msg)

        # `include` is the list of attribute fields the diffsync model
        # treats as identifiers. Pulling just those keeps the GraphQL
        # response small.
        identifiers = list(getattr(model_cls, "_identifiers", ()) or ())
        nodes = self.client.all(
            kind=model_name,
            include=identifiers or None,
            populate_store=False,
        )
        for node in nodes:
            payload = self.infrahub_node_to_diffsync(node=node)
            yield model_cls(**payload).get_unique_id()

    def model_loader(self, model_name: str, model: type[InfrahubModel]) -> None:
        """
        Load and process models using schema mapping filters and transformations.

        This method retrieves data from Infrahub, applies filters and transformations
        as specified in the schema mapping, and loads the processed data into the adapter.
        """
        element = next((el for el in self.config.schema_mapping if el.name == model_name), None)
        if element:
            # Retrieve all nodes corresponding to model_name (list of InfrahubNodeSync)
            nodes = self.client.all(kind=model_name, include=list(model._attributes), populate_store=True)

            # Transform the list of InfrahubNodeSync into a list of (node, dict) tuples
            node_dict_pairs = [(node, self.infrahub_node_to_diffsync(node=node)) for node in nodes]
            total = len(node_dict_pairs)

            # Extract the list of dicts for filtering and transforming
            list_obj = [pair[1] for pair in node_dict_pairs]

            if self.config.source.name.title() == self.type.title():  # ty: ignore[unresolved-attribute]
                # Filter records
                filtered_objs = model.filter_records(records=list_obj, schema_mapping=element)
                logger.info("%s: Loading %d/%d %s", self.type, len(filtered_objs), total, model_name)
                # Transform records
                transformed_objs = model.transform_records(records=filtered_objs, schema_mapping=element)
            else:
                logger.info("%s: Loading all %d %s", self.type, total, model_name)
                transformed_objs = list_obj

            # Create model instances after filtering and transforming
            for transformed_obj in transformed_objs:
                original_node: InfrahubNodeSync = next(node for node, obj in node_dict_pairs if obj == transformed_obj)
                try:
                    item = model(**transformed_obj)
                except ValidationError as exc:
                    if not self.continue_on_error:
                        raise
                    logger.warning(
                        "Skipping %s[%s]: cannot build DiffSync model "
                        "(likely a required peer was skipped earlier). Pydantic errors: %s",
                        model_name,
                        transformed_obj.get("local_id"),
                        exc.errors(include_url=False),
                    )
                    continue
                unique_id = item.get_unique_id()
                self.client.store.set(key=unique_id, node=original_node)
                self.update_or_add_model_instance(item)

    def _resolve_peer_unique_id(
        self,
        *,
        parent_node: InfrahubNodeSync,
        rel_name: str,
        peer_node: InfrahubNodeSync,
    ) -> str | None:
        """Resolve a peer node to its DiffSync unique_id.

        Returns None if the peer cannot be mapped (no DiffSync model, or
        `continue_on_error` is set and the peer is missing identifier values).
        Raises ``PeerIdentifierError`` otherwise so the operator sees actionable
        context instead of a bare ``KeyError``.
        """
        peer_kind = peer_node._schema.kind
        peer_model = getattr(self, peer_kind, None)
        if not peer_model:
            logger.warning("Unable to map '%s' with kind '%s' - Ignored", peer_node, peer_kind)
            return None

        peer_data = self.infrahub_node_to_diffsync(peer_node)
        identifiers = tuple(peer_model._identifiers)
        missing = tuple(k for k in identifiers if k not in peer_data)
        if missing:
            err = PeerIdentifierError(
                parent_kind=parent_node._schema.kind,
                parent_id=str(getattr(parent_node, "id", None)),
                rel_name=rel_name,
                peer_kind=peer_kind,
                peer_id=str(getattr(peer_node, "id", None)),
                identifiers=identifiers,
                missing_keys=missing,
                present_keys=tuple(peer_data.keys()),
            )
            if self.continue_on_error:
                logger.warning("Skipping peer relationship: %s", err)
                return None
            raise err

        unique_id = peer_model.create_unique_id(**{k: peer_data[k] for k in identifiers})
        peer_item = self.store.get(model=peer_kind, identifier=unique_id)
        if not peer_item:
            peer_item = peer_model(**peer_data)
            self.update_or_add_model_instance(peer_item)
            self.client.store.set(key=unique_id, node=peer_node)
        return peer_item.get_unique_id()

    def infrahub_node_to_diffsync(self, node: InfrahubNodeSync) -> dict[str, Any]:
        """
        Convert an Infrahub node into a dictionary suitable for creating a DiffSyncModel.

        Handles attribute conversion and relationship resolution.
        """
        data: dict[str, Any] = {"local_id": str(node.id)}

        for attr_name in node._schema.attribute_names:
            if has_field(config=self.config, name=node._schema.kind, field=attr_name):
                attr = getattr(node, attr_name)
                val = attr.value
                # IP types come back from the Infrahub SDK as ipaddress
                # objects; DiffSync models store them as their string form
                # (e.g. "10.0.0.1/32"), so normalise here. Other non-string
                # kinds — List, Number, Boolean, DateTime — pass through
                # unchanged: stringifying them turns a real list `[]` into
                # the four-character literal `"[]"`, which then fails
                # Pydantic validation on `list[str]`-typed fields.
                if isinstance(
                    val,
                    (ipaddress.IPv4Interface, ipaddress.IPv6Interface, ipaddress.IPv4Network, ipaddress.IPv6Network),
                ):
                    data[attr_name] = str(val)
                else:
                    data[attr_name] = val

        for rel_schema in node._schema.relationships:
            if not has_field(config=self.config, name=node._schema.kind, field=rel_schema.name):
                continue
            peer_schema = self.schema.get(rel_schema.peer)
            if peer_schema is None:
                continue

            if rel_schema.cardinality == "one":
                rel: RelatedNodeSync = getattr(node, rel_schema.name)
                if not rel.id:
                    continue
                peer_node = resolve_peer_node(
                    key=rel.id,
                    rel_schema=rel_schema,
                    peer_schema=peer_schema,
                    store=self.client.store,
                    client=self.client,
                    fallback=True,
                )
                if not peer_node:
                    continue
                unique_id = self._resolve_peer_unique_id(
                    parent_node=node, rel_name=rel_schema.name, peer_node=peer_node
                )
                if unique_id is None:
                    continue
                data[rel_schema.name] = unique_id

            elif rel_schema.cardinality == "many":
                values = []
                rel_manager: RelationshipManagerSync = getattr(node, rel_schema.name)
                if not rel_manager.initialized:
                    rel_manager.fetch()
                for peer in rel_manager.peers:
                    peer_node = resolve_peer_node(
                        key=peer.id,
                        rel_schema=rel_schema,
                        peer_schema=peer_schema,
                        store=self.client.store,
                        client=self.client,
                        fallback=True,
                    )
                    if not peer_node:
                        continue
                    unique_id = self._resolve_peer_unique_id(
                        parent_node=node, rel_name=rel_schema.name, peer_node=peer_node
                    )
                    if unique_id is None:
                        continue
                    values.append(unique_id)
                data[rel_schema.name] = sorted(values)

        return data

    def _report_unkeyed_render(self, *, node: InfrahubNodeSync, node_schema: NodeSchemaAPI) -> None:
        """The keyedness gate: read the rendered mutation input and branch on it (AD066).

        Keyedness is a property of the **rendered mutation**, not of the assembled data: the
        SDK keys the upsert on `data["id"]` if the node has one and otherwise on
        `data["hfid"]`, and `get_human_friendly_id()` returns `None` as soon as any component
        resolves to `None`. All of that is client-side, so the render is readable before the
        write is issued.

        **The render is read two levels deep (AD076).** `_generate_input_data` returns
        `{"data": mutation_payload, "variables": …, "mutation_variables": …}` where
        `mutation_payload` is itself `{"data": data}` — so a check against `…["data"]` alone
        tests a one-key mapping, is true for every operation ever rendered, and would make
        the raising arm below fire on all of them.

        Three arms, by the destination kind's HFID shape:

        - **all components direct** — a render carrying neither key can only mean the payload
          lost its identity components, so it **raises**;
        - **a component crosses a relationship** — the render carries neither key today for a
          reason this outcome does not control (the SDK cannot form the `hfid` from a peer
          supplied as a resolved node id), so it **warns once per kind and proceeds**;
          refusing would withdraw every relationship-bearing kind from what this release
          delivers, and the destination's convergent write may still key server-side;
        - **no HFID declared at all** — unkeyed is a schema fact rather than a defect, and
          FR-024 explicitly permits such a kind and requires the run to survive it, so it
          **warns on the same terms and never raises (AD076)**.

        Raises:
            UnkeyedWriteRefusedError: the render is unkeyed for an all-direct HFID kind.
        """
        rendered = node._generate_input_data(exclude_hfid=False)["data"]["data"]
        if "id" in rendered or "hfid" in rendered:
            return

        kind = node_schema.kind
        components = _hfid_components(node_schema)

        if components and all(len(_component_segments(component)) == 1 for component in components):
            msg = (
                f"The mutation rendered for kind {kind!r} carries neither 'id' nor 'hfid', so the "
                f"convergent write would be unkeyed and a re-apply would duplicate the object. Every "
                f"human-friendly-ID component of that kind ({', '.join(components)}) is a direct "
                f"attribute, so this can only mean the operation's payload lost its identity components."
            )
            raise UnkeyedWriteRefusedError(msg)

        condition = (
            "the kind declares no human-friendly ID, so there is no convergence key to render"
            if not components
            else f"the kind's convergence key crosses a relationship ({', '.join(components)}), which the "
            "client cannot render from a peer supplied as a resolved node id"
        )
        # Allocated by `new_peer_resolver` at an apply's start (AD078). The fallback covers a
        # caller that dispatches without asking for a resolver first: it still deduplicates.
        reported = self._unkeyed_render_reported
        if reported is None:
            reported = self._unkeyed_render_reported = set()
        if kind in reported:
            return
        reported.add(kind)
        logger.warning(
            "Planned write: the mutation rendered for destination kind %s carries neither 'id' nor "
            "'hfid' because %s. The write is issued anyway. Watch for a duplicate object of kind %s at "
            "the destination if it does not key on the identity components as sent.",
            kind,
            condition,
            kind,
        )

    def new_peer_resolver(self) -> PeerResolver:
        """Build the peer resolver for one apply (FR-014, AD086).

        The second member of the planned-write surface
        (`infrahub_sync.plan.write_surface.PlannedWriteDestination`). The engine calls this
        rather than constructing a `PeerResolver` itself: the destination that owns the
        resolver's dependency is the one that builds it.

        One resolver per apply, created at its start and discarded with it — never persisted,
        never shared between applies. The keyedness report's dedup set is allocated here for
        the same reason (AD078): its contract is "once per destination kind **per apply**",
        and on an adapter instance it would silence every disclosure on a second apply.
        """
        self._unkeyed_render_reported = set()
        return PeerResolver(self)

    def apply_planned_operation(self, *, operation: PlannedOperation, peers: PeerResolver) -> str:
        """Execute one planned operation convergently. Returns the destination node id.

        The saved-plan write surface (FR-013). Stored order, payload and identity are taken
        exactly as recorded: nothing is recomputed, and neither side is extracted or loaded
        (FR-012).

        A `delete` raises `SkippedDeleteOperation` and never touches the destination. The raise
        is **defensive**: `Potenda.apply_plan` recognizes a delete in its own loop, records the
        identifier and never dispatches it here (AD055). Recording the skip and completing the
        plan are the **apply loop's** behavior — a caller that dispatches a delete straight to
        this method gets the raise and nothing else.

        A `create` and an `update` both route through the same convergent upsert —
        `client.create(...)` then `save(allow_upsert=True)` — and neither routes through
        `InfrahubModel.update`, whose `local_id` keying needs the destination load FR-012
        forbids. The payload is authoritative for the mapped fields it carries and touches no
        unmapped destination field.

        Two checks sit before the write and they are different checks (AD066): the
        per-component **diagnostic** below, which names *which* human-friendly-ID component
        is unaccounted for, and `_report_unkeyed_render`'s **gate** on the rendered mutation.

        The write is not the last destination interaction: every cardinality-many
        relationship is then written explicitly as a replace-set by a single targeted
        relationship write (AD075, amended by AD085 and AD088), whose surplus-peer removal
        relies on the destination Update mutation's replace semantics.

        Raises:
            SkippedDeleteOperation: the operation is a recorded delete (a designed
                limitation, not a failure), and no skip is recorded — see above.
            UnaccountedIdentityComponentError: a human-friendly-ID component of the
                destination kind is not accounted for by the payload and the operation.
            UnkeyedWriteRefusedError: the rendered mutation is unkeyed for a kind whose
                human-friendly ID is all-direct.
            PeerNotFoundError: a peer identity matches no destination object.
            PeerAmbiguousError: a peer identity matches more than one.
        """
        if operation.action == "delete":
            msg = (
                f"Operation {operation.operation_id!r} is a delete of a {operation.kind!r} object. "
                "Applying deletes is out of scope for this release, so it was not executed and the "
                "destination was not touched."
            )
            raise SkippedDeleteOperation(msg)

        node_schema = self.client.schema.get(kind=operation.kind)
        if not isinstance(node_schema, NodeSchemaAPI):
            msg = f"Expected NodeSchemaAPI for {operation.kind}, got {type(node_schema).__name__}"
            raise TypeError(msg)

        # The payload is `keys` union `source_attrs`, so it already carries the identity
        # components the convergent write keys on (AD042).
        data: dict[str, Any] = dict(operation.payload or {})
        references = list(operation.relationships or ())
        for reference in references:
            peer_ids = [
                peers.resolve(
                    peer_kind=reference.peer_kind,
                    identity=peer,
                    referring_operation_id=operation.operation_id,
                )
                for peer in reference.peers
            ]
            data[reference.field] = peer_ids[0] if reference.cardinality == "one" else peer_ids

        self._assert_identity_components_accounted_for(node_schema=node_schema, data=data, operation=operation)

        source_id = self.source_node.id if self.source_node else None
        owner_id = self.owner_node.id if self.owner_node else None
        create_data = self.client.schema.generate_payload_create(
            schema=node_schema, data=data, source=source_id, owner=owner_id, is_protected=True
        )
        node = self.client.create(kind=operation.kind, data=create_data)
        self._report_unkeyed_render(node=node, node_schema=node_schema)
        node.save(allow_upsert=True)

        # Every cardinality-many relationship is written explicitly as a replace-set rather
        # than left to the upsert alone (PD-005), and `peers: []` means empty the set.
        many_references = [reference for reference in references if reference.cardinality == "many"]
        if many_references:
            # One write per operation, not one per relationship, and targeted rather than a
            # re-render of the node — a re-render nulls every unmapped optional cardinality-one
            # relationship. See `_flush_replaced_relationship_sets` (AD075, AD085, AD088).
            _flush_replaced_relationship_sets(node, [reference.field for reference in many_references])

        node_id = _require_node_id(node, context=f"for operation {operation.operation_id!r}")
        peers.remember(operation.kind, operation.identity, node_id)
        return node_id

    @staticmethod
    def _assert_identity_components_accounted_for(
        *,
        node_schema: NodeSchemaAPI,
        data: Mapping[str, Any],
        operation: PlannedOperation,
    ) -> None:
        """The diagnostic: every HFID component of the kind is accounted for (AD051).

        FR-024 warns about the same condition at plan time; this is what stops it becoming
        silent data duplication at apply time when that warning was ignored or the schema
        changed since. It is the only check that can say *which* component is missing, which
        is why it is kept alongside the rendered-mutation gate rather than replaced by it.

        A kind that declares no human-friendly ID has no components and so passes here; that
        case is the gate's third arm (AD076).

        Raises:
            UnaccountedIdentityComponentError: naming the kind and the missing components.
        """
        components = _hfid_components(node_schema)
        missing = [
            component
            for component in components
            if not _hfid_component_accounted_for(component=component, data=data, operation=operation)
        ]
        if not missing:
            return
        msg = (
            f"Operation {operation.operation_id!r} on destination kind {node_schema.kind!r} does not "
            f"account for every component of that kind's human-friendly ID. Missing: "
            f"{', '.join(missing)}. The convergent write is keyed on those components, so it would be "
            "unkeyed and every re-apply would duplicate the object."
        )
        raise UnaccountedIdentityComponentError(msg)


class InfrahubModel(DiffSyncModelMixin, DiffSyncModel):
    @classmethod
    def create(
        cls,
        adapter: Adapter,
        ids: dict[Any, Any],
        attrs: dict[Any, Any],
    ) -> Self | None:
        if not isinstance(adapter, InfrahubAdapter):
            msg = f"{cls.__name__}.create expected an InfrahubAdapter, got {type(adapter).__name__}"
            raise TypeError(msg)
        node_schema = adapter.client.schema.get(kind=cls.__name__)
        # client.schema.get() returns the wider MainSchemaTypesAPI; diffsync_to_infrahub needs NodeSchemaAPI.
        if not isinstance(node_schema, NodeSchemaAPI):
            msg = f"Expected NodeSchemaAPI for {cls.__name__}, got {type(node_schema).__name__}"
            raise TypeError(msg)
        data = diffsync_to_infrahub(
            ids=ids, attrs=attrs, node_schema=node_schema, store=adapter.client.store, schemas=adapter.schema
        )
        unique_id = cls(**ids, **attrs).get_unique_id()
        source_id = adapter.source_node.id if adapter.source_node else None
        owner_id = adapter.owner_node.id if adapter.owner_node else None
        create_data = adapter.client.schema.generate_payload_create(
            schema=node_schema, data=data, source=source_id, owner=owner_id, is_protected=True
        )
        node = adapter.client.create(kind=cls.__name__, data=create_data)
        node.save(allow_upsert=True)
        adapter.client.store.set(key=unique_id, node=node)

        return super().create(adapter=adapter, ids=ids, attrs=attrs)

    def update(self, attrs: dict) -> Self | None:
        adapter = self.adapter
        if not isinstance(adapter, InfrahubAdapter):
            msg = f"{self.__class__.__name__}.update expected an InfrahubAdapter, got {type(adapter).__name__}"
            raise TypeError(msg)
        node = adapter.client.get(id=self.local_id, kind=self.__class__.__name__)
        source_id = adapter.source_node.id if adapter.source_node else None
        owner_id = adapter.owner_node.id if adapter.owner_node else None
        node = update_node(node=node, attrs=attrs, source=source_id, owner=owner_id)
        node.save(allow_upsert=True)

        return super().update(attrs=attrs)
