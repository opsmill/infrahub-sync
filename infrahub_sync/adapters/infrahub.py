from __future__ import annotations

import ipaddress
import logging
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from diffsync import Adapter, DiffSyncModel
from diffsync.exceptions import ObjectNotFound
from infrahub_sdk import (
    Config,
    InfrahubClientSync,
)
from infrahub_sdk.exceptions import AuthenticationError, GraphQLError, NodeNotFoundError, SchemaNotFoundError
from infrahub_sdk.schema.main import GenericSchemaAPI, NodeSchemaAPI, RelationshipSchemaAPI
from packaging.version import InvalidVersion, Version
from pydantic import ValidationError

from infrahub_sync import (
    DiffSyncMixin,
    DiffSyncModelMixin,
    SyncAdapter,
    SyncConfig,
)
from infrahub_sync.cache.cursors import CursorState, CursorTier
from infrahub_sync.configuration.capabilities import (
    InfrahubLineageSettings,
    NamedLineageNode,
    parse_infrahub_lineage_settings,
)
from infrahub_sync.configuration.credentials import (
    is_registered_context,
    pin_infrahub_sdk_authority,
    select_runtime_credential,
)
from infrahub_sync.generator import has_field
from infrahub_sync.infrahub_errors import classify_infrahub_error
from infrahub_sync.plan.canonical import canonical_json_bytes
from infrahub_sync.plan.errors import (
    ImportAccountMismatchError,
    NamedLineageNodeNotFoundError,
    NullRelationshipValueError,
    PeerAmbiguousError,
    PeerNotFoundError,
    PlanLineageMissingError,
    ReviewedPayloadFieldMissingError,
    SkippedDeleteOperation,
    StaleDestinationIdError,
    UnaccountedIdentityComponentError,
    UnkeyedCreateRefusedError,
)
from infrahub_sync.plan.identity import canonical_identity
from infrahub_sync.plan.keying import (
    is_usable_component_value,
    refuse_unkeyed_create_coverage,
    writable_convergence_reason,
)
from infrahub_sync.plan.last_changes import NOT_RECORDED, LastChange
from infrahub_sync.plan.models import (
    DestinationBindingRecord,
    LineageAccountRecord,
    LineageNodeRecord,
    PlanLineageRecord,
    PlanManifest,
    RecordedAccountRecord,
)

logger = logging.getLogger(__name__)


def resolved_endpoint(settings: Mapping[str, Any], branch: str | None) -> tuple[str | None, str | None]:
    """The effective `(url, branch)` this adapter connects with — env vars before settings.

    One resolution for both consumers: the SDK client's construction and the plan's
    destination binding. The environment-over-settings precedence is
    the whole point of recording the *effective* values — the config-version digest covers
    the parsed YAML only, and the repo's own guidance keeps credentials and addresses in
    environment variables, exactly where that digest is blind.
    """
    url = select_runtime_credential(settings, "url", ("INFRAHUB_ADDRESS", "INFRAHUB_URL"))
    return url, settings.get("branch") or branch


# GraphQL filter kwarg for timestamp-based incremental queries.
# Verified against a live Infrahub via __type introspection — every node
# exposes the metadata-prefixed `node_metadata__updated_at__after` arg.
# Adjust if the server renames this field.
_TIMESTAMP_FILTER_KW = "node_metadata__updated_at__after"

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator, MutableMapping, Sequence

    from infrahub_sdk.node import InfrahubNodeSync, RelatedNodeSync, RelationshipManagerSync
    from infrahub_sdk.node.property import NodeProperty
    from infrahub_sdk.schema import MainSchemaTypesAPI
    from infrahub_sdk.store import NodeStoreSync

    from infrahub_sync.plan.models import PlannedOperation, RelationshipReference


def _node_has_complete_attributes(node: InfrahubNodeSync, node_schema: MainSchemaTypesAPI) -> bool:
    """Check if a node has all its non-optional attributes populated."""
    for attr_schema in node_schema.attributes:
        if attr_schema.optional:
            continue
        attr = getattr(node, attr_schema.name, None)
        if attr is None or attr.value is None:
            return False
    return True


def identifier_and_attribute_fields(model: type[DiffSyncModel]) -> list[str]:
    """Return the fields a loaded node must carry: identifiers first, then attributes.

    `infrahub_node_to_diffsync` reads both lists, so both belong in the GraphQL
    `include`. A server that returns only the requested fields would otherwise drop
    the identifiers the DiffSync model is keyed on.
    """
    return list(dict.fromkeys([*model._identifiers, *model._attributes]))


def resolve_peer_node(
    key: str,
    rel_schema: RelationshipSchemaAPI,
    peer_schema: MainSchemaTypesAPI,
    store: NodeStoreSync,
    client: InfrahubClientSync | None = None,
    fallback: bool | None = False,
    schemas: Mapping[str, MainSchemaTypesAPI] | None = None,
) -> InfrahubNodeSync | None:
    """
    Resolve a peer node given a key.

    Resolution logic:
      - If peer_schema is not a GenericSchemaAPI, try fetching the node from the store using rel_schema.peer.
      - If it is a GenericSchemaAPI, iterate over its `used_by` list and return the first matching node.
      - If not found and fallback is enabled, use the client to fetch the node.
      - If node is found but has incomplete attributes, re-fetch from Infrahub.

    `schemas` is the caller's already-loaded kind-to-schema mapping and the only schema
    source this function consults. Completeness is judged against the peer's own kind; a
    kind absent from `schemas` leaves the stored peer as it is, so resolving a peer issues
    no schema request.

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
    if peer_node and fallback and client and schemas is not None:
        peer_node_schema = schemas.get(peer_node.get_kind())
        if peer_node_schema is not None and not _node_has_complete_attributes(peer_node, peer_node_schema):
            peer_node = client.get(id=key, kind=peer_node.get_kind(), populate_store=True)

    if not peer_node and fallback and client is not None:
        logger.warning("Unable to find %s [%s] in Store - Fallback to Infrahub", rel_schema.peer, key)
        peer_node = client.get(id=key, kind=rel_schema.peer, populate_store=True)
        if not peer_node:
            logger.warning("Unable to find %s [%s] - Ignored", rel_schema.peer, key)
    return peer_node


# The destination's answer to an upsert whose `id` matches no object, as measured on both
# SDK versions. Both values, in one error's extensions, are what prove the server created
# nothing; the HTTP transport status is 200 here and settles nothing.
_NODE_NOT_FOUND_CODE = "NODE_NOT_FOUND"
_NODE_NOT_FOUND_HTTP_STATUS = 404


def _graphql_extensions(exc: GraphQLError) -> Iterator[Mapping[str, Any]]:
    """The `extensions` mapping of each error the destination returned.

    The **only** place a server verdict is read from. The HTTP transport status is 200 even
    for a refused mutation, so it settles nothing; `extensions.http_status` is what carries
    the server's own answer.
    """
    for error in getattr(exc, "errors", ()) or ():
        if isinstance(error, Mapping):
            extensions = error.get("extensions")
            if isinstance(extensions, Mapping):
                yield extensions


def _refuse_stale_destination_id(exc: GraphQLError, *, operation: PlannedOperation) -> None:
    """Re-raise a not-found on an id-keyed upsert as the named, proven-not-written refusal.

    An upsert carrying an `id` that matches no object is answered with
    `extensions.code` `NODE_NOT_FOUND` **and** `extensions.http_status` 404, and the server
    creates nothing on that path — measured on both SDK versions. So this is raised *after*
    the write was attempted and is still proven not to have written, which is what lets it
    carry `wrote = False` where an ordinary transport failure cannot.

    **Both halves are required, in the same error's extensions.** The code alone does not
    prove the id path was the one that ran, and `wrote = False` is a claim that suppresses
    reconciliation: made without the server's own status it would send an operator past a
    destination that may have been written. Anything short of the full measured signature —
    an absent status, a different status, or the two halves arriving in different errors —
    returns here and leaves the caller to re-raise the original, ambiguous failure.

    Returns without raising for anything else, leaving the caller to re-raise the original.
    """
    if operation.destination_id is None:
        return
    proven_not_found = any(
        extensions.get("code") == _NODE_NOT_FOUND_CODE and extensions.get("http_status") == _NODE_NOT_FOUND_HTTP_STATUS
        for extensions in _graphql_extensions(exc)
    )
    if not proven_not_found:
        return
    msg = (
        f"Operation {operation.operation_id!r} updates {operation.kind!r} object "
        f"{operation.identity!r} by its recorded destination id {operation.destination_id!r}, which "
        "matches no object at the destination. The destination refused the write and created "
        "nothing."
    )
    raise StaleDestinationIdError(msg) from exc


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
    """Find the peer identity for a single relationship used by a destination key.

    A planned identity may carry a nested peer reference, while a hand-built operation
    may supply it only in its relationship reference. Check both so a relationship
    component is accepted only when its peer identity supplies the value to be written.
    A cardinality-many reference supplies no key component, so it yields no identity here.
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

    - a **direct** component (`<attr>` / `<attr>__value`): `data` carries `<attr>` with a
      usable value;
    - a **relationship-crossing** component (`<rel>__<attr>__value`): `data` carries `<rel>`
      non-null **and** the operation's nested `{peer_kind, identity}` for `<rel>` supplies
      `<attr>` with a usable value, recursing through the nested identity for deeper paths.

    "Usable" rather than "present" is the whole point, and it is the same predicate plan
    derivation applies (`plan.keying.is_usable_component_value`): a component that arrives as
    `""` is present and keys nothing, so the destination would match no existing object and
    create a second one — the case this check exists to make impossible.
    """
    segments = _component_segments(component)
    if not segments:
        return False
    field = segments[0]
    value = data.get(field)
    if len(segments) == 1:
        return is_usable_component_value(value)
    # A relationship slot holds a resolved node id by now, so only its presence is checkable
    # here; the component's own value comes from the operation's nested peer identity.
    if value is None:
        return False
    nested = _operation_peer_identity(operation, field)
    if nested is None:
        return False
    resolved = _identity_path_value(nested, segments[1:])
    return resolved is not _UNRESOLVED and is_usable_component_value(resolved)


def _refuse_partial_key_peer_filter(operation: PlannedOperation, schemas: Mapping[str, MainSchemaTypesAPI]) -> None:
    """Refuse partial peer keys before lookup, since one loose match can bind the wrong peer."""
    for reference in operation.relationships or ():
        if not reference.peers:
            continue
        peer_schema = schemas.get(reference.peer_kind)
        if peer_schema is None:
            msg = (
                f"The destination schema declares no kind {reference.peer_kind!r}, so no peer of that kind "
                "can be resolved. The plan was derived against a configuration or schema this "
                "destination does not carry"
            )
            raise ValueError(msg)
        if not isinstance(peer_schema, NodeSchemaAPI):
            msg = f"Expected NodeSchemaAPI for {reference.peer_kind}, got {type(peer_schema).__name__}"
            raise TypeError(msg)
        for peer in reference.peers:
            # A destination id identifies the peer directly; its HFID need not be present.
            if isinstance(peer.get("id"), str) and peer["id"].strip():
                continue
            missing = []
            for component in _hfid_components(peer_schema):
                value = _identity_path_value(peer, _component_segments(component))
                if (
                    value is _UNRESOLVED
                    or not is_usable_component_value(value)
                    or isinstance(value, (Mapping, list, tuple))
                ):
                    missing.append(component)
            if missing:
                msg = (
                    f"Operation {operation.operation_id!r} cannot resolve relationship {reference.field!r} "
                    f"through a partial {reference.peer_kind!r} peer filter; missing: {', '.join(missing)}. "
                    "No destination write was attempted."
                )
                raise UnaccountedIdentityComponentError(msg)


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

        An explicit destination id is already a complete peer key and takes precedence.
        An `<attr>__value` path takes its value from the identity's scalar under `<attr>`;
        an `<rel>__<attr>__value` path takes its value from the nested
        `{peer_kind, identity}` pair the identity records under `<rel>`, read at
        `identity[<attr>]` and recursing for deeper nesting (AD043). The **schema path** is
        split; the **data value** never is.

        A component whose value the identity does not supply is skipped here, which would leave
        a too-loose query that can match **exactly one** wrong node and bind it. Planned apply
        never reaches that case: `apply_planned_operation` calls
        `_refuse_partial_key_peer_filter` before `resolve`, and that raises
        `UnaccountedIdentityComponentError` naming the missing components, so every peer
        filter built for a planned write carries a complete peer key: the kind's full
        human-friendly ID, or only the destination id when the plan records one (that id
        bypasses the HFID check). The skip
        stays only so a direct caller of this resolver still gets a filter. The `<rel>__ids`
        fallback (resolve the reference component's own peer first, then filter
        `<rel>__ids=[<id>]`) is not implemented by planned apply.

        A kind that declares no usable HFID component at all falls back to the identity's
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
        if isinstance(identity.get("id"), str) and identity["id"].strip():
            return {"ids": [identity["id"]]}
        components = _hfid_components(node_schema)

        kwargs: dict[str, Any] = {}
        for component in components:
            value = _identity_path_value(identity, _component_segments(component))
            if value is _UNRESOLVED or isinstance(value, (Mapping, list, tuple)):
                continue
            kwargs[_filter_kwarg_name(component)] = value

        if not kwargs:
            for name, value in identity.items():
                if value is None or isinstance(value, (Mapping, list, tuple)):
                    continue
                kwargs[_filter_kwarg_name(name)] = value

        return kwargs

    def _query(self, *, peer_kind: str, identity: Mapping[str, Any], referring_operation_id: str) -> str:
        """Query the destination for one peer, refusing on zero and on more than one.

        The refusals belong to **this** resolver only (AD048). Destination loading
        resolves peers from the SDK store, with a `client.get` fallback when the
        store misses or holds an incomplete node.

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
            f"To fix it, either populate the missing key on the {peer_kind} peer record in "
            "Infrahub and run the diff or sync command again, or add the key to the "
            f"'fields:' entry for {peer_kind} in the schema_mapping, create a new "
            "configuration version with 'infrahub-sync configs version', and run the "
            "command with the new --version. A registered version cannot be edited."
        )
        super().__init__(msg)


def _sdk_node_has_identifiers(
    node: InfrahubNodeSync, identifiers: tuple[str, ...], schema: MainSchemaTypesAPI | None
) -> bool:
    """Return whether an SDK node carries every DiffSync identifier value.

    `schema` is the node's kind schema, or None when the caller holds none for that kind.
    A None schema declares no attributes and no relationships, so a non-empty `identifiers`
    fails closed.
    """
    attributes = {attribute.name for attribute in getattr(schema, "attributes", ())}
    relationships = {relationship.name: relationship for relationship in getattr(schema, "relationships", ())}
    for identifier in identifiers:
        if identifier in attributes:
            attribute = getattr(node, identifier, None)
            if attribute is None or getattr(attribute, "value", None) is None:
                return False
            continue

        relationship = relationships.get(identifier)
        # Fail closed for unknown fields and cardinality-many relationship
        # identifiers instead of guessing an identity from an ambiguous value.
        if relationship is None or relationship.cardinality != "one":
            return False
        related_node = getattr(node, identifier, None)
        if related_node is None or getattr(related_node, "id", None) is None:
            return False
    return True


def _unresolved_peer_identifiers(
    peer_data: Mapping[str, Any],
    identifiers: tuple[str, ...],
    *,
    verified_null_identifiers: frozenset[str],
) -> tuple[str, ...]:
    """Return identifiers absent from peer data or carrying an unverified null."""
    return tuple(
        identifier
        for identifier in identifiers
        if identifier not in peer_data
        or (peer_data[identifier] is None and identifier not in verified_null_identifiers)
    )


class InfrahubLineageSettingsError(ValueError):
    """The destination's `source`, `owner` or `protect` setting is refused.

    The message lists one refusal per setting, each naming the setting.
    """


class ImportAccountUnreadableError(RuntimeError):
    """Infrahub answered the import account query without an account id."""


class LineageNotResolvedError(RuntimeError):
    """A planned write was attempted before `resolve_lineage` set the source, owner and protection.

    Without a resolved lineage the write would carry no source, no owner and no protection, so
    the operation is refused before any destination write.
    """


@dataclass(frozen=True, slots=True)
class ImportAccount:
    """The account behind the token the import uses, as its `AccountProfile` query returns it."""

    id: str
    name: str


@dataclass(frozen=True, slots=True)
class ResolvedLineageNode:
    """A source or owner node, with the id Infrahub gave it."""

    kind: str
    name: str
    id: str


@dataclass(frozen=True, slots=True)
class ResolvedLineage:
    """The lineage one run writes, resolved against the destination before any source read.

    `source` is the named source node, or the import account when no source is named.
    `owner` is the named owner node, or `None` when no owner is named.
    """

    import_account: ImportAccount
    source: ResolvedLineageNode
    owner: ResolvedLineageNode | None
    protect: bool

    def plan_record(self) -> PlanLineageRecord:
        """The manifest's `lineage` field: the import account by name and id, source and owner by kind and name."""
        return PlanLineageRecord(
            import_account=LineageAccountRecord(name=self.import_account.name, id=self.import_account.id),
            source=LineageNodeRecord(kind=self.source.kind, name=self.source.name),
            owner=None if self.owner is None else LineageNodeRecord(kind=self.owner.kind, name=self.owner.name),
            protect=self.protect,
        )


def require_recorded_import_account(saved_plan: PlanManifest) -> LineageAccountRecord:
    """Return the import account the saved plan recorded, or refuse a plan that records none (FR-012)."""
    if saved_plan.lineage is None:
        logger.warning(
            "Apply refused: saved plan %s records no import account",
            saved_plan.run_id,
            extra={"run_id": saved_plan.run_id},
        )
        msg = (
            f"The saved plan for run {saved_plan.run_id!r} records no import account, so this apply cannot "
            "check that it runs with the account the plan was made with. Nothing was written to the destination."
        )
        raise PlanLineageMissingError(msg)
    return saved_plan.lineage.import_account


def refuse_other_import_account(
    *, saved_plan: PlanManifest, recorded: LineageAccountRecord, live: ImportAccount
) -> None:
    """Refuse an apply whose import account is not the one the saved plan recorded, naming both (FR-012)."""
    if recorded.id == live.id:
        return
    logger.warning(
        "Apply refused: plan import account %s (%s), apply import account %s (%s)",
        recorded.name,
        recorded.id,
        live.name,
        live.id,
        extra={
            "run_id": saved_plan.run_id,
            "plan_import_account": recorded.name,
            "plan_import_account_id": recorded.id,
            "apply_import_account": live.name,
            "apply_import_account_id": live.id,
        },
    )
    msg = (
        f"The saved plan for run {saved_plan.run_id!r} was made with the import account {recorded.name!r} "
        f"(id {recorded.id}), and this apply runs with the import account {live.name!r} (id {live.id}). "
        "Nothing was written to the destination."
    )
    raise ImportAccountMismatchError(msg)


def require_lineage_settings(settings: Mapping[str, Any]) -> InfrahubLineageSettings:
    """Return the typed lineage settings, or raise `InfrahubLineageSettingsError` naming each refusal."""
    parsed = parse_infrahub_lineage_settings(settings)
    if parsed.settings is None:
        msg = "; ".join(finding.message for finding in parsed.findings)
        raise InfrahubLineageSettingsError(msg)
    return parsed.settings


# The caller's own account, as the `AccountProfile` query of `client.get_user()` reads it, with
# the account's `name` selected as well. `client.get_user()` selects only `display_label`, which
# Infrahub 1.10 renders from the `label` attribute: it fills `label` from the name in title case,
# so the account `netbox` is labelled `Netbox`. `AccountProfile` needs no permission on accounts.
IMPORT_ACCOUNT_QUERY = "query IMPORT_ACCOUNT { AccountProfile { id name { value } } }"


def _import_account_from_profile(response: Mapping[str, Any]) -> ImportAccount:
    """Read the account id and name out of the `AccountProfile` answer of `IMPORT_ACCOUNT_QUERY`."""
    profile = response.get("AccountProfile")
    if not isinstance(profile, Mapping):
        profile = {}
    account_id = profile.get("id")
    if not isinstance(account_id, str) or not account_id:
        msg = "Infrahub returned no account id for the import account's token."
        raise ImportAccountUnreadableError(msg)
    name_attribute = profile.get("name")
    name = name_attribute.get("value") if isinstance(name_attribute, Mapping) else None
    return ImportAccount(id=account_id, name=name if isinstance(name, str) and name else account_id)


# Infrahub records `updated_by` and `updated_at` on attributes and relationship edges from 1.7.0.
# An older server's GraphQL schema has no such fields, so a query that asks for them is refused
# whole.
METADATA_MIN_VERSION = (1, 7, 0)


def metadata_read_supported(version: str) -> bool:
    """Whether a server of this version records `updated_by` and `updated_at`: 1.7.0 or later.

    A version that cannot be parsed counts as older, so no read asks for fields the server may
    not have. Only the release numbers are compared, so a pre-release of 1.7.0 counts as 1.7.0.
    """
    try:
        release = Version(version).release
    except InvalidVersion:
        return False
    return (*release, 0, 0, 0)[:3] >= METADATA_MIN_VERSION


def _recorded_account(prop: NodeProperty | None) -> RecordedAccountRecord | None:
    if prop is None or not prop.id:
        return None
    return RecordedAccountRecord(id=prop.id, display_label=prop.display_label)


def _edge_last_change(edge: RelatedNodeSync) -> LastChange:
    """The `relationship_metadata` of one edge, read with `include_metadata=True`."""
    metadata = edge.get_relationship_metadata()
    if metadata is None:
        return NOT_RECORDED
    return LastChange(updated_by=_recorded_account(metadata.updated_by), updated_at=metadata.updated_at)


# The fractional seconds of an ISO 8601 time. Python 3.10's `datetime.fromisoformat` reads only
# exactly 3 or 6 digits, and a timestamp can carry any number of them, up to nanoseconds.
_FRACTIONAL_SECONDS = re.compile(r"(?<=:\d\d)\.(\d+)")


def _change_time(change: LastChange) -> tuple[datetime, int]:
    """`updated_at` as a key to compare edges by; a missing or unparsable value sorts first.

    The key is the time to the microsecond and the nanoseconds below it. `datetime` holds only
    microseconds, and Python 3.10's `fromisoformat` reads only 3 or 6 fractional digits, so the
    fraction is cut to microseconds for the `datetime` and its next 3 digits are kept apart. Two
    edges changed within one microsecond then still compare by their full precision.
    """
    earliest = (datetime.min.replace(tzinfo=timezone.utc), 0)
    if change.updated_at is None:
        return earliest
    text = change.updated_at.replace("Z", "+00:00")
    match = _FRACTIONAL_SECONDS.search(text)
    digits = match.group(1) if match is not None else ""
    if match is not None:
        text = text[: match.start()] + "." + digits[:6].ljust(6, "0") + text[match.end() :]
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return earliest
    nanoseconds = int(digits[6:9].ljust(3, "0"))
    return (parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=timezone.utc), nanoseconds)


def _latest_change(changes: Iterable[LastChange]) -> LastChange:
    """The last change of a cardinality-many relationship: its most recently changed edge.

    Infrahub records metadata on each edge, not on the relationship. An edge that a person removed
    leaves no edge to read, so that removal is not part of the result.
    """
    recorded = [change for change in changes if change != NOT_RECORDED]
    if not recorded:
        return NOT_RECORDED
    return max(recorded, key=_change_time)


class InfrahubAdapter(DiffSyncMixin, Adapter):
    type = "Infrahub"

    continue_on_error: bool = False
    # Class defaults so an adapter built without `__init__` (tests of the write path) has
    # no lineage until `resolve_lineage` runs or a caller sets it.
    lineage_settings: InfrahubLineageSettings = InfrahubLineageSettings()
    lineage: ResolvedLineage | None = None
    # Whether this plan's destination reads ask for `updated_by` and `updated_at`: set by
    # `resolve_metadata_read` from the server version, false until then and during an apply.
    metadata_read: bool = False
    # Whether `resolve_metadata_read` still has to read the server version: set by a plan's
    # `resolve_lineage`, cleared by the read, and false for an apply.
    _metadata_read_pending: bool = False
    # What those reads returned, keyed by (destination node id, field name). Kept here and not
    # in the DiffSync models, so a value a person changed is compared by its value only and two
    # plans over the same values give the same operations. Set by `__init__` and reset by `resolve_lineage`.
    last_changes: dict[tuple[str, str], LastChange]
    # Cache state: absent means hydration has not been attempted; None means
    # one attempt was exhausted; a string is the resolved DiffSync identity.
    _peer_unique_ids: dict[tuple[str, str], str | None]

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
        self._peer_unique_ids = {}

        settings = adapter.settings or {}
        infrahub_url, infrahub_branch = resolved_endpoint(settings, branch)
        infrahub_token = select_runtime_credential(settings, "token", ("INFRAHUB_API_TOKEN",))
        verify_ssl = settings.get("verify_ssl")

        if not infrahub_url or not infrahub_token:
            msg = "Both url and token must be specified!"
            raise ValueError(msg)

        # One branch for the SDK default, explicit schema read, and saved-plan binding.
        infrahub_branch = infrahub_branch or "main"
        self.destination_binding = DestinationBindingRecord(url=infrahub_url, branch=infrahub_branch)

        sdk_config: dict[str, Any] = {
            "address": infrahub_url,
            "timeout": 60,
            "api_token": infrahub_token,
            "default_branch": infrahub_branch,
            "default_branch_from_git": False,
        }
        if verify_ssl is not None:
            sdk_config["tls_insecure"] = not verify_ssl
        if is_registered_context(settings):
            # Direct use keeps the SDK's `INFRAHUB_*` fallbacks; a registered package does not.
            sdk_config = pin_infrahub_sdk_authority(sdk_config)

        self.client = InfrahubClientSync(address=infrahub_url, config=Config(**sdk_config))

        # Parsed here, resolved against the destination by `resolve_lineage` at the start of
        # each plan and each apply. Nothing is read from the destination for lineage now.
        # Only a destination writes lineage, and registration checks these settings for the
        # destination role alone, so an Infrahub source keeps the defaults and is not refused.
        self.lineage_settings = (
            require_lineage_settings(
                {"source": settings.get("source"), "owner": settings.get("owner"), "protect": settings.get("protect")}
            )
            if target == "destination"
            else InfrahubLineageSettings()
        )
        self.lineage = None
        self.metadata_read = False
        self.last_changes = {}

        # We will keep a copy of the schema
        self.schema: MutableMapping[str, MainSchemaTypesAPI] = self.client.schema.all(branch=infrahub_branch)

    @property
    def lineage_record(self) -> PlanLineageRecord | None:
        """The lineage a plan records in its manifest, or `None` before `resolve_lineage` runs."""
        return None if self.lineage is None else self.lineage.plan_record()

    def resolve_lineage(self, *, saved_plan: PlanManifest | None = None) -> ResolvedLineage:
        """Resolve the import account, the source and the owner this run writes, and keep them.

        The engine calls this once at the start of a plan, before either side is loaded, and
        once at the start of an apply, before the first write. Each call reads the destination
        again, so an apply does not reuse what the plan resolved.

        The import account is the account behind the token, from one `AccountProfile` query
        (`IMPORT_ACCOUNT_QUERY`), which needs no permission on accounts. It is the source
        unless the `source` setting names a node. There is no lookup of an account named after
        the source system and no fallback: a named node that the destination does not have
        stops the run.

        `saved_plan` is the manifest an apply is about to write. It must record an import
        account, and that account's id must equal the id that query returns now;
        otherwise the apply is refused before any named node is read and before any write
        (FR-012). A plan passes no manifest.

        The server version is not read here: a plan reads it in `resolve_metadata_read`, when
        the destination load starts, so the read runs beside the source load. This call keeps
        `metadata_read` false until then. An apply loads nothing, so it reads no version and
        keeps `metadata_read` false.
        """
        settings = self.lineage_settings
        recorded = None if saved_plan is None else require_recorded_import_account(saved_plan)
        import_account = self._read_import_account()
        if saved_plan is not None and recorded is not None:
            refuse_other_import_account(saved_plan=saved_plan, recorded=recorded, live=import_account)
        if settings.source is None:
            source = ResolvedLineageNode(kind="CoreAccount", name=import_account.name, id=import_account.id)
        else:
            source = self._resolve_named_lineage_node("source", settings.source)
        if settings.owner is None:
            owner = None
        elif settings.owner == settings.source:
            # The same node: it was read once as the source, so it is not read again.
            owner = source
        else:
            owner = self._resolve_named_lineage_node("owner", settings.owner)
        self.lineage = ResolvedLineage(
            import_account=import_account, source=source, owner=owner, protect=settings.protect
        )
        logger.info(
            "Resolved lineage: import account %s, source %s %s, owner %s",
            import_account.name,
            source.kind,
            source.name,
            "none" if owner is None else f"{owner.kind} {owner.name}",
        )
        self.metadata_read = False
        self._metadata_read_pending = saved_plan is None
        self.last_changes = {}
        return self.lineage

    def resolve_metadata_read(self) -> None:
        """Read the server version once for a plan, before the destination load reads anything.

        The engine calls this at the start of the destination load, so the read runs beside
        the source load and before the cursors and the reads that depend on `metadata_read`.
        `metadata_read` is true from Infrahub 1.7.0, the first version that records
        `updated_by` and `updated_at`. After an apply's `resolve_lineage`, or a second call,
        it reads nothing and leaves `metadata_read` as it is.
        """
        if not self._metadata_read_pending:
            return
        self._metadata_read_pending = False
        self.metadata_read = self._read_metadata_support()

    def _read_metadata_support(self) -> bool:
        """Read the server version once and return whether the destination reads ask for metadata.

        A server that refuses the version query is treated like one whose version cannot be
        parsed: the plan continues and every last change shows as not recorded.
        A refusal is a GraphQL error, or an HTTP 403 the SDK raises as `AuthenticationError`.
        An HTTP 401 still stops the plan: the token is refused, and every later read would be
        refused too.

        The warning names the error class, or the classification of a missing permission. It
        never includes the error's text, which can hold the whole request or response.
        """
        try:
            version = self.client.get_version()
        except (GraphQLError, AuthenticationError) as exc:
            classification = classify_infrahub_error(exc)
            if isinstance(exc, AuthenticationError) and (
                classification is None or classification.problem != "missing_permission"
            ):
                raise
            logger.warning(
                "Infrahub refused the server version query, so this plan does not read updated_by and updated_at: %s",
                type(exc).__name__ if classification is None else classification.describe(),
                extra={"infrahub_version": None, "metadata_read": False},
            )
            return False
        supported = metadata_read_supported(version)
        logger.info(
            "Infrahub server version %s: this plan %s updated_by and updated_at",
            version or "unknown",
            "reads" if supported else "does not read",
            extra={"infrahub_version": version, "metadata_read": supported},
        )
        return supported

    def _read_import_account(self) -> ImportAccount:
        """Return the account behind the token, read with one `AccountProfile` query.

        No `operation_name` is passed: `execute_graphql` takes it from infrahub-sdk 1.22 only,
        and the project supports 1.18. The query names its one operation, so none is needed.
        """
        return _import_account_from_profile(self.client.execute_graphql(query=IMPORT_ACCOUNT_QUERY))

    def _resolve_named_lineage_node(self, setting: str, named: NamedLineageNode) -> ResolvedLineageNode:
        """Return the id of the node a `source` or `owner` setting names.

        One `client.get(kind=..., name__value=...)` that selects the name and excludes every
        relationship of the kind. With the SDK's default fields the read also needs `view` on
        the peer kinds of those relationships (`CoreAccountRole` and `CoreGroup` for a group,
        credentials and tags for a repository); with the name only it needs `view` on the
        named kind only. The kind's relationships come from the schema the SDK
        has cached, which `get` reads anyway.

        A named node that does not exist raises `NamedLineageNodeNotFoundError`, so the run
        stops with no fallback node. So does a kind the destination schema does not have on the
        branch the run reads, with a message that says so (FR-004).
        """
        extra = {"lineage_setting": setting, "lineage_kind": named.kind, "lineage_name": named.name}
        try:
            schema = self.client.schema.get(kind=named.kind)
        except SchemaNotFoundError as exc:
            logger.warning(
                "Lineage refused: the setting %s names %s %s, and the destination schema has no kind %s",
                setting,
                named.kind,
                named.name,
                named.kind,
                extra=extra,
            )
            raise NamedLineageNodeNotFoundError(
                setting=setting, kind=named.kind, name=named.name, kind_missing=True
            ) from exc
        relationships = [relationship.name for relationship in schema.relationships]
        try:
            node = self.client.get(kind=named.kind, name__value=named.name, include=["name"], exclude=relationships)
        except NodeNotFoundError as exc:
            logger.warning(
                "Lineage refused: the setting %s names %s %s, which does not exist",
                setting,
                named.kind,
                named.name,
                extra=extra,
            )
            raise NamedLineageNodeNotFoundError(setting=setting, kind=named.kind, name=named.name) from exc
        node_id = _require_node_id(node, context=f"for the {named.kind} named {named.name!r}")
        return ResolvedLineageNode(kind=named.kind, name=named.name, id=node_id)

    def cursor_tier_for(self, model_name: str) -> CursorTier:
        """TIMESTAMP for any kind present in the live Infrahub schema.

        Every Infrahub node carries `node_metadata.updated_at`, so the
        `node_metadata__updated_at__after` filter works for any kind the
        destination schema knows about. Kinds absent from `self.schema`
        fall back to NONE — defensive guard so the engine never attempts
        an incremental query for an unknown kind.

        NONE for every kind while this plan reads metadata (`metadata_read`). An incremental
        load takes unchanged nodes from the previous snapshot, which holds no `updated_by` or
        `updated_at`, so a plan would show "not recorded" for values Infrahub holds (FR-007).
        With no cursor tier, the engine loads each kind in full through `model_loader`, whose
        one read carries the metadata. The engine calls `resolve_metadata_read` before it asks
        for the destination's cursor tiers, so the answer reflects the server version.
        """
        if self.metadata_read:
            return CursorTier.NONE
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
            include_metadata=self.metadata_read,
            **filter_kwargs,
        )
        for node in nodes:
            payload = self.infrahub_node_to_diffsync(node=node)
            if self.metadata_read:
                self._record_last_changes(node)
            yield payload

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

        fields = identifier_and_attribute_fields(model_cls)
        nodes = self.client.all(
            kind=model_name,
            include=fields or None,
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
            # With `include_metadata`, the same read also returns `updated_by` and `updated_at` for
            # each attribute and each edge, cardinality-many edges included, because they are named
            # in `include`. No request is added.
            nodes = self.client.all(
                kind=model_name,
                include=identifier_and_attribute_fields(model),
                populate_store=True,
                include_metadata=self.metadata_read,
            )

            # Transform the list of InfrahubNodeSync into a list of (node, dict) tuples
            node_dict_pairs = [(node, self.infrahub_node_to_diffsync(node=node)) for node in nodes]
            if self.metadata_read:
                for node in nodes:
                    self._record_last_changes(node)
            total = len(node_dict_pairs)

            # Extract the list of dicts for filtering and transforming
            list_obj = [pair[1] for pair in node_dict_pairs]

            if self.target == "source":
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
        peer_kind = peer_node.get_kind()
        peer_model = getattr(self, peer_kind, None)
        if not peer_model:
            logger.warning("Unable to map '%s' with kind '%s' - Ignored", peer_node, peer_kind)
            return None

        peer_id = str(peer_node.id)
        cache_key = (peer_kind, peer_id)
        hydration_attempted = cache_key in self._peer_unique_ids
        identifiers = tuple(peer_model._identifiers)
        if hydration_attempted:
            cached_unique_id = self._peer_unique_ids[cache_key]
            if cached_unique_id is not None:
                self._reconcile_peer_sdk_alias(
                    peer_kind=peer_kind,
                    peer_id=peer_id,
                    unique_id=cached_unique_id,
                    identifiers=identifiers,
                )
                return cached_unique_id

        peer_data = self.infrahub_node_to_diffsync(peer_node)
        verified_null_identifiers: frozenset[str] = frozenset()
        missing = _unresolved_peer_identifiers(
            peer_data,
            identifiers,
            verified_null_identifiers=verified_null_identifiers,
        )
        hydrated = False
        if missing and not hydration_attempted:
            try:
                hydrated_peer = self.client.get(
                    id=peer_node.id,
                    kind=peer_kind,
                    include=list(identifiers),
                    populate_store=False,
                )
            except NodeNotFoundError:
                hydrated_peer = None
            if hydrated_peer is not None:
                hydrated = True
                hydrated_peer_data = self.infrahub_node_to_diffsync(hydrated_peer)
                hydrated_peer_schema = self.schema.get(hydrated_peer.get_kind())
                attribute_names = {attribute.name for attribute in getattr(hydrated_peer_schema, "attributes", ())}
                # Top-level model loads are full fetches. Peer payloads may be shallow,
                # so only this successful hydration can verify a nullable attribute.
                verified_null_identifiers = frozenset(
                    identifier
                    for identifier in missing
                    if identifier in attribute_names
                    and identifier in hydrated_peer_data
                    and hydrated_peer_data[identifier] is None
                )
                hydrated_identifiers = {
                    identifier: hydrated_peer_data[identifier]
                    for identifier in identifiers
                    if hydrated_peer_data.get(identifier) is not None or identifier in verified_null_identifiers
                }
                peer_data = {**peer_data, **hydrated_identifiers}
            missing = _unresolved_peer_identifiers(
                peer_data,
                identifiers,
                verified_null_identifiers=verified_null_identifiers,
            )
        if missing:
            err = PeerIdentifierError(
                parent_kind=parent_node.get_kind(),
                parent_id=str(getattr(parent_node, "id", None)),
                rel_name=rel_name,
                peer_kind=peer_kind,
                peer_id=str(getattr(peer_node, "id", None)),
                identifiers=identifiers,
                missing_keys=missing,
                present_keys=tuple(peer_data.keys()),
            )
            self._peer_unique_ids[cache_key] = None
            if self.continue_on_error:
                if not hydration_attempted:
                    logger.warning("Skipping peer relationship: %s", err)
                return None
            raise err

        unique_id = peer_model.create_unique_id(**{k: peer_data[k] for k in identifiers})
        try:
            peer_item = self.store.get(model=peer_kind, identifier=unique_id)
        except ObjectNotFound:
            peer_item = peer_model(**peer_data)
            self.update_or_add_model_instance(peer_item)

        # Identifier-only hydration is deliberately adapter-local: inserting its
        # narrow result into the shared SDK store can replace a fuller node that
        # was loaded earlier. Reconciliation chooses only nodes that carry the
        # complete DiffSync identity.
        self._reconcile_peer_sdk_alias(
            peer_kind=peer_kind,
            peer_id=peer_id,
            unique_id=unique_id,
            identifiers=identifiers,
            fallback_node=None if hydrated else peer_node,
        )
        resolved_unique_id = peer_item.get_unique_id()
        self._peer_unique_ids[cache_key] = resolved_unique_id
        if verified_null_identifiers:
            logger.warning(
                "Resolved peer %s[%s] with verified null attribute identifier(s): %s",
                peer_kind,
                peer_id,
                sorted(verified_null_identifiers),
            )
        return resolved_unique_id

    def _reconcile_peer_sdk_alias(
        self,
        *,
        peer_kind: str,
        peer_id: str,
        unique_id: str,
        identifiers: tuple[str, ...],
        fallback_node: InfrahubNodeSync | None = None,
    ) -> None:
        """Point SDK UUID and identity lookups at a complete cached peer."""
        sdk_peer_by_uuid = self.client.store.get(key=peer_id, kind=peer_kind, raise_when_missing=False)
        sdk_peer_by_identity = self.client.store.get(key=unique_id, kind=peer_kind, raise_when_missing=False)
        sdk_peer = next(
            (
                peer
                for peer in (sdk_peer_by_uuid, sdk_peer_by_identity, fallback_node)
                if peer is not None and _sdk_node_has_identifiers(peer, identifiers, self.schema.get(peer.get_kind()))
            ),
            None,
        )
        if sdk_peer is None:
            # This is the hydration-only path: a non-hydrated success supplies
            # an identity-complete fallback. Leave two shallow store entries untouched.
            return
        if sdk_peer_by_uuid is sdk_peer and sdk_peer_by_identity is sdk_peer:
            return
        self.client.store.set(key=unique_id, node=sdk_peer)

    def infrahub_node_to_diffsync(self, node: InfrahubNodeSync) -> dict[str, Any]:
        """
        Convert an Infrahub node into a dictionary suitable for creating a DiffSyncModel.

        Handles attribute conversion and relationship resolution.
        """
        data: dict[str, Any] = {"local_id": str(node.id)}
        node_kind = node.get_kind()
        # Schema comes from the adapter's loaded mapping; the SDK schema manager is not
        # consulted because a node built with an explicit schema is absent from its cache
        # and a lookup there could fetch.
        node_schema = self.schema[node_kind]

        for attr_name in self._mapped_attribute_names(node_kind, node_schema):
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

        for rel_schema, peer_schema in self._mapped_relationships(node_kind, node_schema):
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
                    schemas=self.schema,
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
                        schemas=self.schema,
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

    def _mapped_attribute_names(self, node_kind: str, node_schema: MainSchemaTypesAPI) -> Iterator[str]:
        """The attributes of `node_kind` the configuration maps."""
        for attr_name in node_schema.attribute_names:
            if has_field(config=self.config, name=node_kind, field=attr_name):
                yield attr_name

    def _mapped_relationships(
        self, node_kind: str, node_schema: MainSchemaTypesAPI
    ) -> Iterator[tuple[RelationshipSchemaAPI, MainSchemaTypesAPI]]:
        """The relationships of `node_kind` the configuration maps and whose peer kind the schema has, with that peer's schema."""
        for rel_schema in node_schema.relationships:
            if not has_field(config=self.config, name=node_kind, field=rel_schema.name):
                continue
            peer_schema = self.schema.get(rel_schema.peer)
            if peer_schema is not None:
                yield rel_schema, peer_schema

    def _record_last_changes(self, node: InfrahubNodeSync) -> None:
        """Keep the `updated_by` and `updated_at` of each mapped field of a node read with metadata.

        The fields are those `infrahub_node_to_diffsync` converts: mapped attributes, and mapped
        relationships whose peer kind the schema has. An attribute's values come from
        `node.<attribute>`; a cardinality-one relationship's from its edge; a cardinality-many
        relationship's from its most recently changed edge. A field Infrahub returned nothing
        for is kept as `NOT_RECORDED`. Nothing here reaches the DiffSync models.
        """
        node_id = str(node.id)
        node_kind = node.get_kind()
        node_schema = self.schema[node_kind]
        for attr_name in self._mapped_attribute_names(node_kind, node_schema):
            attr = getattr(node, attr_name)
            self.last_changes[node_id, attr_name] = LastChange(
                updated_by=_recorded_account(attr.updated_by), updated_at=attr.updated_at
            )
        for rel_schema, _ in self._mapped_relationships(node_kind, node_schema):
            if rel_schema.cardinality == "one":
                change = _edge_last_change(getattr(node, rel_schema.name))
            elif rel_schema.cardinality == "many":
                rel_manager: RelationshipManagerSync = getattr(node, rel_schema.name)
                change = _latest_change(_edge_last_change(peer) for peer in rel_manager.peers)
            else:
                continue
            self.last_changes[node_id, rel_schema.name] = change

    def new_peer_resolver(self) -> PeerResolver:
        """Build the peer resolver for one apply (FR-014, AD086).

        The second member of the planned-write surface
        (`infrahub_sync.plan.write_surface.PlannedWriteDestination`). The engine calls this
        rather than constructing a `PeerResolver` itself: the destination that owns the
        resolver's dependency is the one that builds it.

        One resolver per apply, created at its start and discarded with it — never persisted,
        never shared between applies.
        """
        return PeerResolver(self)

    def validate_planned_payload_fields(self, operations: Sequence[PlannedOperation]) -> None:
        """Check every reviewed direct field and SDK input before the first write."""
        from infrahub_sync.plan.keying import refuse_destination_identity_collisions

        if not any(operation.action != "delete" for operation in operations):
            return
        schemas = self.client.schema.all(refresh=True)
        missing_by_kind: dict[str, set[str]] = {}
        for operation in operations:
            if operation.action == "delete":
                continue
            schema = schemas.get(operation.kind)
            if isinstance(schema, NodeSchemaAPI):
                missing = self._unwritable_reviewed_fields(operation=operation, node_schema=schema)
                if not missing:
                    missing = self._omitted_rendered_fields(operation=operation, node_schema=schema)
            else:
                missing = set(operation.payload or {})
            if missing:
                missing_by_kind.setdefault(operation.kind, set()).update(missing)
        if missing_by_kind:
            details = "; ".join(
                f"{kind!r}: {', '.join(repr(field) for field in sorted(fields))}"
                for kind, fields in sorted(missing_by_kind.items())
            )
            msg = (
                f"The live destination schema cannot represent reviewed direct payload field(s) for "
                f"destination kind(s) {details}. The whole plan was refused before its first write; "
                "no destination write was attempted."
            )
            raise ReviewedPayloadFieldMissingError(msg)
        refuse_destination_identity_collisions(schema=schemas, operations=operations)

    @staticmethod
    def _reviewed_direct_fields(*, operation: PlannedOperation, node_schema: NodeSchemaAPI) -> set[str]:
        """Return direct payload fields, including nulls whose names became relationships.

        Derived plans put relationships in ``operation.relationships``. The legacy
        mandatory null relationship check remains at the operation boundary.
        """
        payload = operation.payload or {}
        mandatory_null_relationships = {
            relationship.name
            for relationship in node_schema.relationships
            if relationship.cardinality == "one"
            and not relationship.optional
            and relationship.name in payload
            and payload[relationship.name] is None
        }
        return set(payload) - mandatory_null_relationships

    @classmethod
    def _unwritable_reviewed_fields(cls, *, operation: PlannedOperation, node_schema: NodeSchemaAPI) -> set[str]:
        """Find direct fields that are absent or read-only in the current schema."""
        writable = {attribute.name for attribute in node_schema.attributes if not attribute.read_only}
        return cls._reviewed_direct_fields(operation=operation, node_schema=node_schema) - writable

    def _omitted_rendered_fields(self, *, operation: PlannedOperation, node_schema: NodeSchemaAPI) -> set[str]:
        """Find reviewed direct values the SDK would leave out of its mutation input."""
        payload = operation.payload or {}
        data = self.client.schema.generate_payload_create(schema=node_schema, data=payload)
        node = self.client.create(kind=operation.kind, data=data)
        if operation.action == "update":
            node.id = operation.destination_id
        self._set_reviewed_null_attributes(node=node, operation=operation, node_schema=node_schema)
        rendered_data = node._generate_input_data(exclude_hfid=True)["data"]["data"]
        return {
            field
            for field in self._reviewed_direct_fields(operation=operation, node_schema=node_schema)
            if field not in rendered_data
        }

    @staticmethod
    def _set_reviewed_null_attributes(
        *, node: InfrahubNodeSync, operation: PlannedOperation, node_schema: NodeSchemaAPI
    ) -> None:
        """Mark reviewed null attributes as writes in the SDK mutation input."""
        payload = operation.payload or {}
        for attribute in node_schema.attributes:
            if attribute.name in payload and payload[attribute.name] is None:
                getattr(node, attribute.name).value = None

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
        a destination load, which saved-plan apply does not perform. The payload is
        authoritative for the mapped fields it carries and touches no
        unmapped destination field.

        An **update** carries the destination `id` recorded at plan time. A **create**
        is keyed by the destination kind's human-friendly-ID components in its payload,
        which the server matches on. Each create is checked before mutation so a missing
        component cannot become an unkeyed write.

        That upsert is the **only** destination write the operation makes. It carries every
        cardinality-many relationship as the plan's peer list, and `peers: []` means empty the
        set rather than leave it alone. Surplus peers go because the destination's Upsert
        mutation replaces a relationship list rather than merging it — a property of the
        server, pinned by the live shrink test
        (`tests/integration/test_infrahub_replace_set_shrink_integration.py`) and recorded in
        ADR 0012.

        Raises:
            SkippedDeleteOperation: the operation is a recorded delete (a designed
                limitation, not a failure), and no skip is recorded — see above.
            LineageNotResolvedError: `resolve_lineage` has not set this run's source, owner
                and protection, so no write is made for this operation.
            ReviewedPayloadFieldMissingError: the live schema or SDK mutation would omit
                a reviewed direct payload field, so no write is made for this operation.
            UnaccountedIdentityComponentError: a human-friendly-ID component of the
                destination kind is not accounted for by the payload and the operation.
            StaleDestinationIdError: an update's recorded destination id matches no object
                at the destination, which the destination proved by refusing the write and
                creating nothing.
            NullRelationshipValueError: a mandatory cardinality-one relationship is null
                in the planned payload.
            PeerNotFoundError: a peer identity matches no destination object.
            PeerAmbiguousError: a peer identity matches more than one.
        """
        if operation.action == "delete":
            msg = (
                f"Operation {operation.operation_id!r} is a delete of a {operation.kind!r} object. "
                "Applying deletes is not supported, so the delete was not executed and the "
                "destination was not touched."
            )
            raise SkippedDeleteOperation(msg)

        # Checked before the schema read and the peer lookups, so a refused operation reads and
        # writes nothing.
        lineage = self.lineage
        if lineage is None:
            msg = (
                f"Operation {operation.operation_id!r} for destination kind {operation.kind!r} was refused: "
                "the import's source, owner and protection were not resolved before the write. "
                "No destination write was attempted."
            )
            raise LineageNotResolvedError(msg)

        # Preflight refreshed this cache once for the whole plan. Reuse the schema that
        # the SDK will render with; a fresh full-branch download per operation is costly
        # and cannot prevent a server-side schema change between this check and save.
        node_schema = self.client.schema.all().get(operation.kind)
        if node_schema is None and operation.payload:
            fields = ", ".join(repr(field) for field in sorted(operation.payload))
            msg = (
                f"Operation {operation.operation_id!r} for destination kind {operation.kind!r} "
                f"would omit reviewed direct payload field(s) {fields} because the live schema "
                "no longer declares the kind. No write was made for this operation."
            )
            raise ReviewedPayloadFieldMissingError(msg)
        if not isinstance(node_schema, NodeSchemaAPI):
            msg = f"Expected NodeSchemaAPI for {operation.kind}, got {type(node_schema).__name__}"
            raise TypeError(msg)

        # The payload is `keys` union `source_attrs`, so it already carries the identity
        # components the convergent write keys on (AD042). Derived plans put relationships
        # in `operation.relationships`; a null payload field is reviewed as an attribute.
        # Keep the legacy mandatory null relationship error for hand-built artifacts.
        payload = operation.payload or {}
        missing_fields = self._unwritable_reviewed_fields(operation=operation, node_schema=node_schema)
        if missing_fields:
            fields = ", ".join(repr(field) for field in sorted(missing_fields))
            msg = (
                f"Operation {operation.operation_id!r} for destination kind {operation.kind!r} "
                f"would omit reviewed direct payload field(s) {fields} because the live schema "
                "does not declare them as writable attributes. No write was made for this operation."
            )
            raise ReviewedPayloadFieldMissingError(msg)
        null_to_one_relationships = [
            relationship
            for relationship in node_schema.relationships
            if relationship.cardinality == "one" and relationship.name in payload and payload[relationship.name] is None
        ]
        mandatory_null_fields = sorted(
            relationship.name for relationship in null_to_one_relationships if not relationship.optional
        )
        if mandatory_null_fields:
            fields = ", ".join(repr(field) for field in mandatory_null_fields)
            msg = (
                f"Operation {operation.operation_id!r} for destination kind {operation.kind!r} carries "
                f"null for mandatory cardinality-one relationship field(s) {fields}. The operation "
                "was refused before SDK rendering and no destination write was attempted."
            )
            raise NullRelationshipValueError(msg)

        data: dict[str, Any] = dict(payload)
        references = list(operation.relationships or ())
        _refuse_partial_key_peer_filter(operation, self.schema)
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

        if operation.action == "create":
            # Creates only. A create is matched by the destination on the human-friendly-ID
            # components it carries, so both checks are conditions of the write; an update is
            # keyed by its recorded id and those components key nothing for it. Coverage
            # first, from the same function plan derivation uses; then AD051 for values,
            # because it is the check that can name the missing component. Both run before
            # `client.create` and read the cached schema and the operation alone.
            reason = writable_convergence_reason(
                node=node_schema,
                identity_fields=operation.identity,
                mapped_fields=data,
                schemas=self.schema,
                identity=operation.identity,
                check_values=_has_unwritable_hfid_component(node_schema),
            )
            if reason:
                msg = (
                    f"Operation {operation.operation_id!r} for destination kind {operation.kind!r}: {reason}. "
                    "No destination write was attempted."
                )
                raise UnkeyedCreateRefusedError(msg)
            refuse_unkeyed_create_coverage(operation, node=node_schema, schemas=self.schema)
            if not _has_unwritable_hfid_component(node_schema):
                self._assert_identity_components_accounted_for(node_schema=node_schema, data=data, operation=operation)

        source_id = lineage.source.id
        owner_id = None if lineage.owner is None else lineage.owner.id
        # `None` leaves the `is_protected` key out of the payload, so a value protected earlier
        # keeps its flag; the flag is sent only when the package turns `protect` on (FR-006).
        is_protected = True if lineage.protect else None
        # The owner goes on every value a create writes. An update sends it on no attribute,
        # so the server keeps the owner each value holds, including an empty one (FR-002);
        # it goes only on the relationship edges the update adds, because an edge the update
        # sends again keeps the owner it holds.
        create_data = self.client.schema.generate_payload_create(
            schema=node_schema,
            data=data,
            source=source_id,
            owner=owner_id if operation.action == "create" else None,
            is_protected=is_protected,
        )
        if operation.action == "update" and owner_id is not None and references:
            self._set_owner_on_added_edges(
                create_data=create_data,
                operation=operation,
                node_schema=node_schema,
                references=references,
                owner_id=owner_id,
            )
        clear_lineage_on_pool_edges(create_data, node_schema)
        node = self.client.create(kind=operation.kind, data=create_data)
        if operation.action == "update":
            # The recorded id, set on the node rather than put into `data`. The SDK renders
            # `data["id"] = self.id` when the node carries one and only otherwise considers
            # `hfid` (`infrahub_sdk/node/node.py:295-298`), so this is what makes the upsert
            # a keyed update of that exact object. Putting `id` into `data` instead would
            # render it as the attribute-shaped `id: {}` and key nothing.
            node.id = operation.destination_id
        self._set_reviewed_null_attributes(node=node, operation=operation, node_schema=node_schema)
        # generate_payload_create retains unknown keys as empty dictionaries. The node
        # drops them while building the input to the mutation, so check that final input.
        rendered_data = node._generate_input_data(exclude_hfid=True)["data"]["data"]
        missing_fields = {
            field
            for field in self._reviewed_direct_fields(operation=operation, node_schema=node_schema)
            if field not in rendered_data
        }
        if missing_fields:
            fields = ", ".join(repr(field) for field in sorted(missing_fields))
            msg = (
                f"Operation {operation.operation_id!r} for destination kind {operation.kind!r} "
                f"would omit reviewed direct payload field(s) {fields} from the SDK mutation. "
                "No write was made for this operation."
            )
            raise ReviewedPayloadFieldMissingError(msg)
        try:
            node.save(allow_upsert=True)
        except GraphQLError as exc:
            _refuse_stale_destination_id(exc, operation=operation)
            raise

        node_id = _require_node_id(node, context=f"for operation {operation.operation_id!r}")
        peers.remember(operation.kind, operation.identity, node_id)
        return node_id

    def _current_peer_ids(
        self,
        *,
        operation: PlannedOperation,
        node_schema: NodeSchemaAPI,
        references: Sequence[RelationshipReference],
    ) -> dict[str, set[str]] | None:
        """Return the peer ids the destination node has now, for each relationship the update writes.

        One `client.get` by the recorded id, in the request shape `RelationshipManager.fetch()`
        uses: the cardinality-many relationships the update writes are included, and every
        attribute and every other relationship is excluded, so no attribute value is selected.
        Cardinality-one relationships come back without being included, because the SDK selects
        every one that is not excluded. `None` when the node does not exist: the upsert keyed by
        that id is then refused as a stale id, and no edge is treated as added.
        """
        written = {reference.field for reference in references}
        include = [reference.field for reference in references if reference.cardinality == "many"]
        exclude = [
            *node_schema.attribute_names,
            *(name for name in node_schema.relationship_names if name not in written),
        ]
        node = self.client.get(
            kind=operation.kind,
            id=operation.destination_id,
            include=include,
            exclude=exclude,
            populate_store=False,
            raise_when_missing=False,
        )
        if node is None:
            return None
        current: dict[str, set[str]] = {}
        for reference in references:
            related = getattr(node, reference.field)
            if reference.cardinality == "many":
                current[reference.field] = set(related.peer_ids)
            else:
                current[reference.field] = {related.id} if related.id else set()
        return current

    def _set_owner_on_added_edges(
        self,
        *,
        create_data: dict[str, Any],
        operation: PlannedOperation,
        node_schema: NodeSchemaAPI,
        references: Sequence[RelationshipReference],
        owner_id: str,
    ) -> None:
        """Put the owner on the relationship edges an update adds, and on no other edge.

        An edge is added when its peer is not among the node's current peers (cardinality
        many), or differs from the current peer (cardinality one). Infrahub replaces an
        existing edge's owner whenever one is sent, so every other edge is left without the
        `owner` key and keeps the owner it has.
        """
        current = self._current_peer_ids(operation=operation, node_schema=node_schema, references=references)
        if current is None:
            logger.debug(
                "Owner not set on operation %s: the node %s was not found",
                operation.operation_id,
                operation.destination_id,
            )
            return
        for reference in references:
            for edge in _edge_inputs(create_data.get(reference.field)):
                if isinstance(edge, dict) and edge.get("id") not in current[reference.field]:
                    edge["owner"] = owner_id

    @staticmethod
    def _assert_identity_components_accounted_for(
        *,
        node_schema: NodeSchemaAPI,
        data: Mapping[str, Any],
        operation: PlannedOperation,
    ) -> None:
        """The diagnostic: every HFID component of a **create** is accounted for (AD051).

        FR-024 refuses the same condition at plan time; this is what stops it becoming silent
        data duplication at apply time when the plan was hand-built or the schema changed
        since. It is the only check that can say *which* component is missing, which is why it
        is the **value** arm of the create guard rather than being folded into the coverage
        check that runs just before it.

        Creates only, which the caller enforces: a recorded-id update may omit, blank or fail
        to resolve a component, because the id is what keys it.

        A kind that declares no human-friendly ID has no components and so passes here;
        `refuse_unkeyed_create_coverage` is what refuses it, on its uniqueness constraints.

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


_LINEAGE_EDGE_PROPERTIES = ("source", "owner", "is_protected")


def _edge_inputs(value: Any) -> Iterator[Any]:
    """Yield each edge of a relationship's input: the items of a list, or the value itself."""
    yield from value if isinstance(value, list) else [value]


def clear_lineage_on_pool_edges(create_data: dict[str, Any], node_schema: NodeSchemaAPI) -> None:
    """Remove `source`, `owner` and `is_protected` from each relationship edge allocated from a resource pool.

    Infrahub sets the pool as the source of an edge it allocates, so that edge must carry none
    of the import's lineage properties, and the pool stays its source. No V3 write path
    allocates from a pool today; this keeps the rule in place for the write path that does.
    An edge is allocated from a pool when its input carries the `from_pool` key.
    """
    for name in node_schema.relationship_names:
        for edge in _edge_inputs(create_data.get(name)):
            if isinstance(edge, dict) and "from_pool" in edge:
                for prop in _LINEAGE_EDGE_PROPERTIES:
                    edge.pop(prop, None)


def _has_unwritable_hfid_component(node_schema: NodeSchemaAPI) -> bool:
    """Whether an HFID member is omitted or computed by the destination."""
    hfid = set(node_schema.human_friendly_id or ())
    return any(
        f"{attribute.name}__value" in hfid
        and (getattr(attribute, "read_only", False) or getattr(attribute, "computed_attribute", None) is not None)
        for attribute in node_schema.attributes
    ) or any(
        getattr(relationship, "read_only", False)
        and any(component == relationship.name or component.startswith(f"{relationship.name}__") for component in hfid)
        for relationship in node_schema.relationships
    )


class InfrahubModel(DiffSyncModelMixin, DiffSyncModel):
    """DiffSync model for Infrahub records loaded during planning."""
