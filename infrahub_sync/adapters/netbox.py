from __future__ import annotations

# pylint: disable=R0801
import os
from typing import TYPE_CHECKING, Any

from requests import Session

try:
    from typing import Self
except ImportError:
    from typing_extensions import Self
import pynetbox
from diffsync import Adapter, DiffSyncModel

from infrahub_sync import (
    DiffSyncMixin,
    DiffSyncModelMixin,
    SchemaMappingModel,
    SyncAdapter,
    SyncConfig,
)

from .utils import get_value

if TYPE_CHECKING:
    from collections.abc import Mapping


class NetboxAdapter(DiffSyncMixin, Adapter):
    type = "Netbox"

    def __init__(self, target: str, adapter: SyncAdapter, config: SyncConfig, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)

        self.target = target
        self.client = self._create_netbox_client(adapter)
        self.config = config

    def _create_netbox_client(self, adapter: SyncAdapter) -> pynetbox.api:
        settings = adapter.settings or {}
        url = os.environ.get("NETBOX_ADDRESS") or os.environ.get("NETBOX_URL") or settings.get("url")
        token = os.environ.get("NETBOX_TOKEN") or settings.get("token")
        verify_ssl = settings.get("verify_ssl", True)

        if not url or not token:
            msg = "Both url and token must be specified!"
            raise ValueError(msg)

        client = pynetbox.api(url, token=token)
        # Set SSL verification
        session = Session()
        session.verify = verify_ssl
        client.http_session = session
        return client

    def model_loader(self, model_name: str, model: NetboxModel) -> None:
        """
        Load and process models using schema mapping filters and transformations.

        This method retrieves data from Netbox, applies filters and transformations
        as specified in the schema mapping, and loads the processed data into the adapter.
        """
        for element in self.config.schema_mapping:
            if element.name != model_name:
                continue

            # Use the resource endpoint from the schema mapping
            app_name, resource_name = element.mapping.split(".")
            netbox_app = getattr(self.client, app_name)
            netbox_model = getattr(netbox_app, resource_name)

            # Retrieve all objects (RecordSet)
            nodes = netbox_model.all()

            # Transform the RecordSet into a list of Dict
            list_obj = []
            for node in nodes:
                list_obj.append(dict(node))

            total = len(list_obj)
            if self.config.source.name.title() == self.type.title():
                # Filter records
                filtered_objs = model.filter_records(records=list_obj, schema_mapping=element)
                print(f"{self.type}: Loading {len(filtered_objs)}/{total} {resource_name}")
                # Transform records
                transformed_objs = model.transform_records(records=filtered_objs, schema_mapping=element)
            else:
                print(f"{self.type}: Loading all {total} {resource_name}")
                transformed_objs = list_obj

            # Create model instances after filtering and transforming
            for obj in transformed_objs:
                data = self.netbox_obj_to_diffsync(obj=obj, mapping=element, model=model)
                item = model(**data)
                self.add(item)

    def netbox_obj_to_diffsync(self, obj: dict[str, Any], mapping: SchemaMappingModel, model: NetboxModel) -> dict:
        obj_id = obj.get("id")
        data: dict[str, Any] = {"local_id": str(obj_id)}

        for field in mapping.fields:  # pylint: disable=too-many-nested-blocks
            field_is_list = model.is_list(name=field.name)

            if field.static:
                data[field.name] = field.static
            elif not field_is_list and field.mapping and not field.reference:
                value = get_value(obj, field.mapping)
                if value is not None:
                    data[field.name] = value
            elif field_is_list and field.mapping and not field.reference:
                msg = "It's not supported yet to have an attribute of type list with a simple mapping"
                raise NotImplementedError(msg)
            elif field.mapping and field.reference:
                all_nodes_for_reference = self.store.get_all(model=field.reference)
                nodes = [item for item in all_nodes_for_reference]
                if not nodes and all_nodes_for_reference:
                    msg = (
                        f"Unable to get '{field.mapping}' with '{field.reference}' reference from store."
                        f" The available models are {self.store.get_all_model_names()}"
                    )
                    raise IndexError(msg)
                if not field_is_list:
                    if node := get_value(obj, field.mapping):
                        if isinstance(node, dict):
                            matching_nodes = []
                            node_id = node.get("id", None)
                            matching_nodes = [item for item in nodes if item.local_id == str(node_id)]
                            if len(matching_nodes) == 0:
                                msg = f"Unable to locate the node {field.name} {node_id}"
                                raise IndexError(msg)
                            node = matching_nodes[0]
                            data[field.name] = node.get_unique_id()
                        else:
                            data[field.name] = node
                else:
                    data[field.name] = []
                    for node in get_value(obj, field.mapping):
                        if not node:
                            continue
                        node_id = node.get("id", None)
                        if not node_id and isinstance(node, tuple):
                            node_id = node[1] if node[0] == "id" else None
                            if not node_id:
                                continue
                        matching_nodes = [item for item in nodes if item.local_id == str(node_id)]
                        if len(matching_nodes) == 0:
                            msg = f"Unable to locate the node {field.reference} {node_id}"
                            raise IndexError(msg)
                        data[field.name].append(matching_nodes[0].get_unique_id())
                    data[field.name] = sorted(data[field.name])

        return data


class NetboxModel(DiffSyncModelMixin, DiffSyncModel):
    @classmethod
    def create(
        cls,
        adapter: Adapter,
        ids: Mapping[Any, Any],
        attrs: Mapping[Any, Any],
    ) -> Self | None:
        # TODO: To implement
        return super().create(adapter=adapter, ids=ids, attrs=attrs)

    def update(self, attrs: dict) -> Self | None:
        # TODO: To implement
        return super().update(attrs)
