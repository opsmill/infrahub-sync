from __future__ import annotations

from typing import TYPE_CHECKING, Any

try:
    from typing import Self
except ImportError:
    from typing_extensions import Self

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

        base_url = settings.get("base_url") or None
        auth = settings.get("auth") or None
        timeout = settings.get("timeout", 10)
        verify_ssl = settings.get("verify_ssl", True)

        if not base_url or not auth:
            msg = "Both url and auth must be specified!"
            raise ValueError(msg)

        return IPFClient(base_url=base_url, auth=auth, timeout=timeout, verify=verify_ssl)

    def build_mapping(self, reference, obj) -> str:
        # Get object class and model name from the store
        object_class, modelname = self.store._get_object_class_and_model(model=reference)

        # Find the schema element matching the model name
        schema_element = next(
            (element for element in self.config.schema_mapping if element.name == modelname),
            None,
        )

        # Collect all relevant field mappings for identifiers
        new_identifiers = []

        # Convert schema_element.fields to a dictionary for fast lookup
        field_dict = {field.name: field.mapping for field in schema_element.fields}

        # Loop through object_class._identifiers to find corresponding field mappings
        for identifier in object_class._identifiers:
            if identifier in field_dict:
                new_identifiers.append(field_dict[identifier])

        # Construct the unique identifier, using a fallback if a key isn't found
        unique_id = "__".join(str(obj.get(key, "")) for key in new_identifiers)
        return unique_id

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
                data = self.ipfabric_dict_to_diffsync(obj=obj, mapping=element, model=model)
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
                if not field_is_list and (node := obj[field.mapping]):
                    matching_nodes = []
                    node_id = self.build_mapping(reference=field.reference, obj=obj)
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
