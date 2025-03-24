from __future__ import annotations

import os
from typing import TYPE_CHECKING, Any

try:
    from typing import Self
except ImportError:
    from typing_extensions import Self
import requests
from diffsync import Adapter, DiffSyncModel

from infrahub_sync import (
    DiffSyncMixin,
    DiffSyncModelMixin,
    SchemaMappingModel,
    SyncAdapter,
    SyncConfig,
)

from .rest_api_client import RestApiClient
from .utils import derive_identifier_key, get_value

if TYPE_CHECKING:
    from collections.abc import Mapping


class PeeringmanagerAdapter(DiffSyncMixin, Adapter):
    type = "Peeringmanager"

    def __init__(self, target: str, adapter: SyncAdapter, config: SyncConfig, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)

        self.target = target
        self.client = self._create_rest_client(adapter)
        self.config = config
        settings = adapter.settings or {}
        self.params = settings.get("params", {})

    def _create_rest_client(self, adapter: SyncAdapter) -> RestApiClient:
        settings = adapter.settings or {}
        url = os.environ.get("PEERING_MANAGER_ADDRESS") or os.environ.get("PEERING_MANAGER_URL") or settings.get("url")
        api_endpoint = settings.get("api_endpoint", "api")  # Default endpoint, change if necessary
        auth_method = settings.get("auth_method", "token")
        api_token = os.environ.get("PEERING_MANAGER_TOKEN") or settings.get("token")
        timeout = settings.get("timeout", 30)

        if not url:
            msg = "url must be specified!"
            raise ValueError(msg)

        if auth_method != "token" or not api_token:
            msg = "Token-based authentication requires a valid API token!"
            raise ValueError(msg)

        full_base_url = f"{url.rstrip('/')}/{api_endpoint.strip('/')}"
        return RestApiClient(base_url=full_base_url, auth_method=auth_method, api_token=api_token, timeout=timeout)

    def model_loader(self, model_name: str, model: PeeringmanagerModel) -> None:
        """
        Load and process models using schema mapping filters and transformations.

        This method retrieves data from Peering Manager, applies filters and transformations
        as specified in the schema mapping, and loads the processed data into the adapter.
        """
        # Retrieve schema mapping for this model
        for element in self.config.schema_mapping:
            if element.name != model_name:
                continue

            # Use the resource endpoint from the schema mapping
            resource_name = element.mapping

            try:
                # Retrieve all objects
                response_data = self.client.get(endpoint=resource_name, params=self.params)
                objs = response_data.get("results", [])
            except Exception as exc:
                msg = f"Error fetching data from REST API: {exc!s}"
                raise ValueError(msg) from exc

            total = len(objs)
            if self.config.source.name.title() == self.type.title():
                # Filter records
                filtered_objs = model.filter_records(records=objs, schema_mapping=element)
                print(f"{self.type}: Loading {len(filtered_objs)}/{total} {resource_name}")
                # Transform records
                transformed_objs = model.transform_records(records=filtered_objs, schema_mapping=element)
            else:
                print(f"{self.type}: Loading all {total} {resource_name}")
                transformed_objs = objs

            # Create model instances after filtering and transforming
            for obj in transformed_objs:
                data = self.obj_to_diffsync(obj=obj, mapping=element, model=model)
                item = model(**data)
                self.add(item)

    def obj_to_diffsync(
        self,
        obj: dict[str, Any],
        mapping: SchemaMappingModel,
        model: PeeringmanagerModel,
    ) -> dict:
        obj_id = derive_identifier_key(obj=obj)
        data: dict[str, Any] = {"local_id": str(obj_id)}

        for field in mapping.fields:
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
                    values = get_value(obj, field.mapping)
                    # When using another node for mapping, it should be a list
                    if isinstance(values, list):
                        for node in values:
                            if not node:
                                continue
                            node_id = node.get("id", None)
                            if not node_id and isinstance(node, tuple):
                                node_id = node[1] if node[0] == "id" else None
                                if not node_id:
                                    print(f"No ID found for {node} - skipped")
                                    continue
                            matching_nodes = [item for item in nodes if item.local_id == str(node_id)]
                            if len(matching_nodes) == 0:
                                msg = f"Unable to locate the node {field.reference} {node_id}"
                                raise IndexError(msg)
                            data[field.name].append(matching_nodes[0].get_unique_id())
                        data[field.name] = sorted(data[field.name])
                    # If you are using an attribute a mapping
                    elif isinstance(values, str):
                        for item in nodes:
                            tmp = get_value(item, field.mapping)
                            if tmp == values:
                                data[field.name].append(item.get_unique_id())
        return data


class PeeringmanagerModel(DiffSyncModelMixin, DiffSyncModel):
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
        """
        Update an object in the Peering Manager system with new attributes.

        This method maps the given attributes to the corresponding target fields
        based on the schema mapping configuration, and sends an update request
        to the API endpoint of the object.
        """
        # Determine the resource name using the schema mapping
        resource_name = self.__class__.get_resource_name(schema_mapping=self.adapter.config.schema_mapping)

        # Determine the unique identifier for the API request
        unique_identifier = self.local_id if hasattr(self, "local_id") else self.get_unique_id()
        endpoint = f"{resource_name}/{unique_identifier}/"

        # Map incoming attributes to the target attributes based on schema mapping
        mapped_attrs: dict[str, Any] = {}
        for field in self.adapter.config.schema_mapping:
            if field.name == self.__class__.get_type():
                for field_mapping in field.fields:
                    # Map source field name to target field name
                    if field_mapping.name in attrs:
                        target_field_name = field_mapping.mapping
                        value = attrs[field_mapping.name]

                        # Check if the field is a relationship
                        if field_mapping.reference:
                            all_nodes_for_reference = self.adapter.store.get_all(model=field_mapping.reference)

                            if isinstance(value, list):
                                # For lists, filter nodes to match the unique IDs in the attribute value
                                filtered_nodes = [
                                    node for node in all_nodes_for_reference if node.get_unique_id() in value
                                ]
                                mapped_attrs[target_field_name] = [node.local_id for node in filtered_nodes]
                            else:
                                # For single references, find the matching node
                                filtered_node = next(
                                    (node for node in all_nodes_for_reference if node.get_unique_id() == value),
                                    None,
                                )
                                if filtered_node:
                                    mapped_attrs[target_field_name] = filtered_node.local_id
                        else:
                            mapped_attrs[target_field_name] = value

        # Attempt to send the update request to the API
        try:
            self.adapter.client.patch(endpoint, data=mapped_attrs)
            return super().update(attrs)
        except (requests.exceptions.HTTPError, ConnectionError) as exc:
            msg = f"Error during update: {exc!s}"
            raise ValueError(msg) from exc
