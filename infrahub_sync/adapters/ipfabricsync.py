from __future__ import annotations

from typing import TYPE_CHECKING, Any

try:
    from typing import Self
except ImportError:
    from typing_extensions import Self
import json

try:
    from ipfabric import IPFClient
except ImportError as e:
    print(e)

from diffsync import Adapter, DiffSyncModel

from infrahub_sync import (
    DiffSyncMixin,
    DiffSyncModelMixin,
    SchemaMappingModel,
    SyncAdapter,
    SyncConfig,
)
from infrahub_sync.adapters.utils import build_mapping

if TYPE_CHECKING:
    from collections.abc import Mapping

ipf_filters = {
    "tables/inventory/summary/platforms": {"and": [{"platform": ["empty", False]}]},
    "tables/inventory/summary/models": {"and": [{"model": ["empty", False]}]},
    "tables/inventory/pn": {"and": [{"name": ["empty", False]}]},
}


class IpfabricsyncAdapter(DiffSyncMixin, Adapter):
    type = "IPFabricsync"

    def __init__(self, target: str, adapter: SyncAdapter, config: SyncConfig, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)

        self.target = target
        self.client = self._create_ipfabric_client(adapter)
        self.config = config

    def _create_ipfabric_client(self, adapter: SyncAdapter) -> IPFClient:
        settings = adapter.settings or {}

        base_url = settings.get("base_url", None)
        auth = settings.get("auth", None)

        if not base_url or not auth:
            msg = "Both url and auth must be specified!"
            raise ValueError(msg)

        return IPFClient(**settings)

    def model_loader(self, model_name: str, model: IpfabricsyncModel) -> None:
        """
        Load and process models using schema mapping filters and transformations.

        This method retrieves data from IP Fabric, and loads the processed data into the adapter.
        """
        for element in self.config.schema_mapping:
            if element.name != model_name:
                continue

            table = self.client.fetch_all(element.mapping, filters=ipf_filters.get(element.mapping))
            print(f"{self.type}: Loading {len(table)} from `{element.mapping}`")

            total = len(table)

            if self.config.source.name.title() == self.type.title():
                # Filter records
                filtered_objs = model.filter_records(records=table, schema_mapping=element)
                print(f"{self.type}: Loading {len(filtered_objs)}/{total} {element.mapping}")
                # Transform records
                transformed_objs = model.transform_records(records=filtered_objs, schema_mapping=element)
            else:
                print(f"{self.type}: Loading all {total} {element.mapping}")
                transformed_objs = table

            for obj in transformed_objs:
                print(f"Object to load: {obj}")
                data = self.ipfabric_dict_to_diffsync(obj=obj, mapping=element, model=model)
                print(f"Data to load: {data}")
                item = model(**data)
                self.update_or_add_model_instance(item)

    def ipfabric_dict_to_diffsync(self, obj: dict, mapping: SchemaMappingModel, model: IpfabricsyncModel) -> dict:  # pylint: disable=too-many-branches
        data: dict[str, Any] = {"local_id": str(obj["id"])}

        for field in mapping.fields:  # pylint: disable=too-many-nested-blocks
            field_is_list = model.is_list(name=field.name)

            if field.static:
                data[field.name] = field.static
            elif not field_is_list and field.mapping and not field.reference:
                value = obj.get(field.mapping)
                if value is not None:
                    # TODO: Be able to do this in the infrahub-sync mapping file
                    if field.name == "speed":
                        data[field.name] = value / 1000
                    else:
                        data[field.name] = value
            elif field_is_list and field.mapping and not field.reference:
                # Handle list data for attributes like ntp_servers
                list_value = obj.get(field.mapping)
                if list_value is not None:
                    # Ensure we end up with a real Python list.
                    if isinstance(list_value, str):
                        # Try to parse as JSON first.
                        s = list_value.strip()
                        try:
                            parsed = json.loads(s)
                        except (json.JSONDecodeError, TypeError):
                            # Fallbacks: comma-separated -> list; otherwise singleton list
                            parsed = [part.strip() for part in s.split(",")] if "," in s else [s]
                        list_value = parsed
                    # If it's not a list yet, wrap it.
                    if not isinstance(list_value, list):
                        list_value = [list_value]
                    data[field.name] = list_value
                else:
                    data[field.name] = []

            elif field.mapping and field.reference:
                all_nodes_for_reference = self.store.get_all(model=field.reference)

                nodes = [item for item in all_nodes_for_reference]
                if not nodes and all_nodes_for_reference:
                    msg = (
                        f"Unable to get '{field.mapping}' with '{field.reference}' reference from store."
                        f" The available models are {self.store.get_all_model_names()}"
                    )
                    raise IndexError(msg)
                if not field_is_list and (node := obj[field.mapping]):
                    matching_nodes = []
                    node_id = build_mapping(adapter=self, reference=field.reference, obj=obj, field=field)
                    matching_nodes = [item for item in nodes if str(item) == node_id]
                    if len(matching_nodes) == 0:
                        data[field.name] = None
                    else:
                        node = matching_nodes[0]
                        data[field.name] = node.get_unique_id()
        return data


class IpfabricsyncModel(DiffSyncModelMixin, DiffSyncModel):
    @classmethod
    def create(
        cls,
        adapter: Adapter,
        ids: Mapping[Any, Any],
        attrs: Mapping[Any, Any],
    ) -> Self | None:
        # TODO: To Implement
        return super().create(adapter=adapter, ids=ids, attrs=attrs)

    def update(self, attrs: dict) -> Self | None:
        # TODO: To Implement
        return super().update(attrs=attrs)
