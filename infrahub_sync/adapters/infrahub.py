from __future__ import annotations

import copy
import ipaddress
import os
from typing import TYPE_CHECKING, Any

from infrahub_sdk.schema.main import GenericSchemaAPI, NodeSchema, RelationshipSchemaAPI

try:
    from typing import Self
except ImportError:
    from typing_extensions import Self


from diffsync import Adapter, DiffSyncModel
from infrahub_sdk import (
    Config,
    InfrahubClientSync,
)
from infrahub_sdk.exceptions import NodeNotFoundError
from infrahub_sdk.node.property import NodeProperty
from infrahub_sdk.utils import compare_lists

from infrahub_sync import (
    DiffSyncMixin,
    DiffSyncModelMixin,
    SyncAdapter,
    SyncConfig,
)
from infrahub_sync.generator import has_field

if TYPE_CHECKING:
    from collections.abc import Mapping, MutableMapping

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

    if not peer_node and fallback:
        print(f"Unable to find {rel_schema.peer} [{key}] in Store - Fallback to Infrahub")
        peer_node = client.get(id=key, kind=rel_schema.peer, populate_store=True)
        if not peer_node:
            print(f"Unable to find {rel_schema.peer} [{key}] - Ignored")
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
                peer_schema: MainSchemaTypesAPI = schemas.get(rel_schema.peer)
                if attr_name != rel_schema.name:
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
                            print(f"Unable to find {rel_schema.peer} [{attr_value}] in the Store - Ignored")
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
    node_schema: NodeSchema,
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
                peer_schema: MainSchemaTypesAPI = schemas.get(rel_schema.peer)
                if key != rel_schema.name:
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
                        print(f"Unable to find {rel_schema.peer} [{data[key]}] in the Store - Ignored")
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
                            print(f"Unable to find {rel_schema.peer} [{value}] in the Store - Ignored")
                            continue
                        new_values.append(peer_node.id)
                    data[key] = new_values
    return data


class InfrahubAdapter(DiffSyncMixin, Adapter):
    type = "Infrahub"

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
                print(f"Warning: CoreAccountGroup '{source_setting}' not found for source, falling back to account")
                self.source_node = default_account
        else:
            self.source_node = default_account

        # Resolve owner - group if specified in settings, else default account
        owner_setting = settings.get("owner")
        if owner_setting:
            try:
                self.owner_node = self.client.get(kind="CoreAccountGroup", hfid=[owner_setting])
            except NodeNotFoundError:
                print(f"Warning: CoreAccountGroup '{owner_setting}' not found for owner, falling back to account")
                self.owner_node = default_account
        else:
            self.owner_node = default_account

        # We will keep a copy of the schema
        self.schema: MutableMapping[str, MainSchemaTypesAPI] = self.client.schema.all(branch=infrahub_branch)

    def model_loader(self, model_name: str, model: InfrahubModel) -> None:
        """
        Load and process models using schema mapping filters and transformations.

        This method retrieves data from Infrahub, applies filters and transformations
        as specified in the schema mapping, and loads the processed data into the adapter.
        """
        element = next((el for el in self.config.schema_mapping if el.name == model_name), None)
        if element:
            # Retrieve all nodes corresponding to model_name (list of InfrahubNodeSync)
            nodes = self.client.all(kind=model_name, include=model._attributes, populate_store=True)

            # Transform the list of InfrahubNodeSync into a list of (node, dict) tuples
            node_dict_pairs = [(node, self.infrahub_node_to_diffsync(node=node)) for node in nodes]
            total = len(node_dict_pairs)

            # Extract the list of dicts for filtering and transforming
            list_obj = [pair[1] for pair in node_dict_pairs]

            if self.config.source.name.title() == self.type.title():
                # Filter records
                filtered_objs = model.filter_records(records=list_obj, schema_mapping=element)
                print(f"{self.type}: Loading {len(filtered_objs)}/{total} {model_name}")
                # Transform records
                transformed_objs = model.transform_records(records=filtered_objs, schema_mapping=element)
            else:
                print(f"{self.type}: Loading all {total} {model_name}")
                transformed_objs = list_obj

            # Create model instances after filtering and transforming
            for transformed_obj in transformed_objs:
                original_node: InfrahubNodeSync = next(node for node, obj in node_dict_pairs if obj == transformed_obj)
                item = model(**transformed_obj)
                unique_id = item.get_unique_id()
                self.client.store.set(key=unique_id, node=original_node)
                self.update_or_add_model_instance(item)

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
                # Convert IP types and other non-string values to strings for DiffSync models
                if isinstance(
                    val,
                    (ipaddress.IPv4Interface, ipaddress.IPv6Interface, ipaddress.IPv4Network, ipaddress.IPv6Network),
                ) or (val is not None and not isinstance(val, str)):
                    data[attr_name] = str(val)
                else:
                    data[attr_name] = val

        for rel_schema in node._schema.relationships:
            if not has_field(config=self.config, name=node._schema.kind, field=rel_schema.name):
                continue
            peer_schema: MainSchemaTypesAPI = self.schema.get(rel_schema.peer)

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
                peer_model = getattr(self, peer_node._schema.kind, None)
                if not peer_model:
                    print(f"Unable to map '{peer_node}' with kind '{peer_node._schema.kind}'")
                    continue
                peer_data = self.infrahub_node_to_diffsync(peer_node)
                peer_item = peer_model(**peer_data)
                data[rel_schema.name] = peer_item.get_unique_id()

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
                    peer_model = getattr(self, peer_node._schema.kind, None)
                    if not peer_model:
                        print(f"Unable to map '{peer_node}' with kind '{peer_node._schema.kind}' - Ignored")
                        continue
                    peer_data = self.infrahub_node_to_diffsync(peer_node)
                    peer_item = peer_model(**peer_data)
                    values.append(peer_item.get_unique_id())
                data[rel_schema.name] = sorted(values)

        return data


class InfrahubModel(DiffSyncModelMixin, DiffSyncModel):
    @classmethod
    def create(
        cls,
        adapter: InfrahubAdapter,
        ids: Mapping[Any, Any],
        attrs: Mapping[Any, Any],
    ) -> Self | None:
        node_schema = adapter.client.schema.get(kind=cls.__name__)
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
        node = self.adapter.client.get(id=self.local_id, kind=self.__class__.__name__)
        source_id = self.adapter.source_node.id if self.adapter.source_node else None
        owner_id = self.adapter.owner_node.id if self.adapter.owner_node else None
        node = update_node(node=node, attrs=attrs, source=source_id, owner=owner_id)
        node.save(allow_upsert=True)

        return super().update(attrs=attrs)
