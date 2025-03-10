from __future__ import annotations

import os
from typing import TYPE_CHECKING, Any

try:
    from typing import Self
except ImportError:
    from typing_extensions import Self
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


class ObserviumAdapter(DiffSyncMixin, Adapter):
    type = "Observium"

    def __init__(self, target: str, adapter: SyncAdapter, config: SyncConfig, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)

        self.target = target
        self.client = self._create_rest_client(adapter)
        self.config = config
        settings = adapter.settings or {}
        self.params = settings.get("params", {})

    def _create_rest_client(self, adapter: SyncAdapter) -> RestApiClient:
        settings = adapter.settings or {}
        url = os.environ.get("OBSERVIUM_ADDRESS") or os.environ.get("OBSERVIUM_URL") or settings.get("url")
        api_endpoint = settings.get("api_endpoint", "/api/v0")
        auth_method = settings.get("auth_method", "basic")
        api_token = os.environ.get("OBSERVIUM_TOKEN") or settings.get("token")
        username = os.environ.get("OBSERVIUM_USERNAME") or settings.get("username")
        password = os.environ.get("OBSERVIUM_PASSWORD") or settings.get("password")
        timeout = settings.get("timeout")

        if not url:
            msg = "url must be specified!"
            raise ValueError(msg)

        base_url = f"{url.rstrip('/')}/{api_endpoint.strip('/')}"
        return RestApiClient(
            base_url=base_url,
            auth_method=auth_method,
            api_token=api_token,
            username=username,
            password=password,
            timeout=timeout,
        )

    def model_loader(self, model_name: str, model: ObserviumModel) -> None:
        """
        Load and process models using schema mapping filters and transformations.

        This method retrieves data from Observium, applies filters and transformations
        as specified in the schema mapping, and loads the processed data into the adapter.
        """
        for element in self.config.schema_mapping:
            if element.name != model_name:
                continue

            # Use the resource endpoint from the schema mapping
            resource_name = element.mapping

            try:
                # Fetch data from the specified resource endpoint
                response_data = self.client.get(endpoint=resource_name, params=self.params)
                objs = response_data.get(resource_name, {})
            except Exception as exc:
                msg = f"Error fetching data from REST API: {exc!s}"
                raise ValueError(msg) from exc

            if isinstance(objs, dict):
                objs = list(objs.values())

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

    def obj_to_diffsync(self, obj: dict[str, Any], mapping: SchemaMappingModel, model: ObserviumModel) -> dict:
        obj_id = derive_identifier_key(obj=obj)
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
                msg = "it's not supported yet to have an attribute of type list with a simple mapping"
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
                                msg = f"Unable to locate the node {model} {node_id}"
                                raise IndexError(msg)
                            node = matching_nodes[0]
                            data[field.name] = node.get_unique_id()
                        else:
                            # Some link are referencing the node identifier directly without the id (i.e location in device)
                            data[field.name] = node

                else:
                    data[field.name] = []
                    for node in get_value(obj, field.mapping):
                        if not node:
                            continue
                        node_id = getattr(node, "id", None)
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


class ObserviumModel(DiffSyncModelMixin, DiffSyncModel):
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
