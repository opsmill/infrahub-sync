from __future__ import annotations

import copy
import ipaddress
import logging
import os
from typing import TYPE_CHECKING, Any

from diffsync import Adapter, Diff, DiffSyncModel
from diffsync.enum import DiffSyncFlags
from infrahub_sdk import (
    Config,
    InfrahubClientSync,
)
from infrahub_sdk.exceptions import NodeNotFoundError
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

logger = logging.getLogger(__name__)

# GraphQL filter kwarg for timestamp-based incremental queries.
# Verified against a live Infrahub via __type introspection — every node
# exposes the metadata-prefixed `node_metadata__updated_at__after` arg.
# Adjust if the server renames this field.
_TIMESTAMP_FILTER_KW = "node_metadata__updated_at__after"

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator, Mapping, MutableMapping

    from infrahub_sdk.node import InfrahubNodeSync, RelatedNodeSync, RelationshipManagerSync
    from infrahub_sdk.schema import MainSchemaTypesAPI
    from infrahub_sdk.store import NodeStoreSync


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


class ConvergenceIdentityError(ValueError):
    """Raised before writes when no destination key covers a mapping identity."""


def _component_field(component: str) -> str:
    """Return the mapping field at the root of an Infrahub schema key path."""
    return component.split("__", 1)[0]


def _destination_upsert_key(node_schema: object) -> frozenset[str]:
    """Return the schema fields Infrahub actually uses to match an upsert."""
    human_friendly_id = getattr(node_schema, "human_friendly_id", None)
    if human_friendly_id:
        return frozenset(_component_field(component) for component in human_friendly_id)

    default_filter = getattr(node_schema, "default_filter", None)
    if default_filter:
        return frozenset({_component_field(default_filter)})

    # Keyless upserts have a separate duplication hazard, but cannot collapse
    # distinct source identities by matching them onto one destination object.
    return frozenset()


class InfrahubAdapter(DiffSyncMixin, Adapter):
    type = "Infrahub"

    continue_on_error: bool = False

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
        infrahub_url = os.environ.get("INFRAHUB_ADDRESS") or os.environ.get("INFRAHUB_URL") or settings.get("url")
        infrahub_token = os.environ.get("INFRAHUB_API_TOKEN") or settings.get("token")
        infrahub_branch = settings.get("branch") or branch
        verify_ssl = settings.get("verify_ssl")

        if not infrahub_url or not infrahub_token:
            msg = "Both url and token must be specified!"
            raise ValueError(msg)

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

    def _validate_convergence_identities(self) -> None:
        """Refuse runtime model identities finer than Infrahub's upsert key."""
        for mapping in self.config.schema_mapping:
            model_class = getattr(self, mapping.name, None)
            identifiers = frozenset(getattr(model_class, "_identifiers", None) or mapping.identifiers or ())
            node_schema = self.schema.get(mapping.name)
            if not identifiers or node_schema is None:
                continue

            upsert_key = _destination_upsert_key(node_schema)
            if not upsert_key or identifiers <= upsert_key:
                continue

            uncovered = ", ".join(sorted(identifiers - upsert_key))
            mapping_identity = ", ".join(sorted(identifiers))
            destination_key = ", ".join(sorted(upsert_key))
            msg = (
                f"Refusing to sync destination kind {mapping.name}: generated model identity "
                f"({mapping_identity}) is finer than its destination upsert key "
                f"({destination_key}); uncovered mapping identifier(s): {uncovered}. "
                "Align the generated model identity with the destination human-friendly ID "
                "(or its default filter when no human-friendly ID exists) before retrying. "
                "A uniqueness constraint alone does not change the upsert match key."
            )
            raise ConvergenceIdentityError(msg)

    def sync_from(  # pylint: disable=too-many-positional-arguments
        self,
        source: Adapter,
        diff_class: type[Diff] = Diff,
        flags: DiffSyncFlags = DiffSyncFlags.NONE,
        callback: Callable[[str, int, int], None] | None = None,
        diff: Diff | None = None,
    ) -> Diff:
        """Validate convergence identities before entering DiffSync's write path."""
        self._validate_convergence_identities()
        return super().sync_from(
            source=source,
            diff_class=diff_class,
            flags=flags,
            callback=callback,
            diff=diff,
        )

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
