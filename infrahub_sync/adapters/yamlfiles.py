from __future__ import annotations

from typing import TYPE_CHECKING, Any

from diffsync import Adapter, DiffSyncModel
import uuid
import hashlib
import yaml
from .utils import get_value

from collections.abc import Mapping

import itertools

if TYPE_CHECKING:
    from collections.abc import Mapping

from pathlib import Path

try:
    from typing import Self
except ImportError:
    from typing_extensions import Self

from infrahub_sync import (
    DiffSyncMixin,
    DiffSyncModelMixin,
    SchemaMappingModel,
    SyncAdapter,
    SyncConfig,
)
from collections import defaultdict


class YamlfilesAdapter(DiffSyncMixin, Adapter):
    type = "yamlfiles"

    def __init__(self, target: str, adapter: SyncAdapter, config: SyncConfig, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)

        self.target = target
        self.config = config
        self.path = Path(Path.cwd() / self.config.source.settings.get("source"))
        self.data = self.load_and_merge_yaml_files()

    def load_yaml_files(self):
        """Loads individual YAML files into a list of dictionaries."""
        data = []

        for yaml_file in itertools.chain(self.path.glob("*.yml"), self.path.glob("*.yaml")):
            try:
                with open(yaml_file, "r", encoding="utf-8") as file:
                    parsed_data = yaml.safe_load(file)
                    if isinstance(parsed_data, dict):
                        data.append(parsed_data)
                    else:
                        print(f"Skipping {yaml_file}: Expected a dictionary, got {type(parsed_data)}")
            except yaml.YAMLError as e:
                print(f"Error loading {yaml_file}: {e}")

        return data

    def merge_yaml_data(self, yaml_data_list):
        """Merges multiple dictionaries, combining list values into a single key."""
        merged_data = defaultdict(list)

        for yaml_data in yaml_data_list:
            for key, value in yaml_data.items():
                if isinstance(value, list):
                    merged_data[key].extend(value)
                else:
                    merged_data[key] = value  # Keep non-list values as they are

        return dict(merged_data)

    def load_and_merge_yaml_files(self):
        """Loads YAML files and merges their content."""
        yaml_data_list = self.load_yaml_files()
        return self.merge_yaml_data(yaml_data_list)

    def model_loader(self, model_name: str, model: YamlfilesModel) -> None:
        for element in self.config.schema_mapping:
            if element.name != model_name:
                continue

            resource_name = element.mapping

            objs = self.data.get(resource_name, [])

            if resource_name not in self.data:
                raise SystemExit(f"Mapping value: {resource_name} not in data.")

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

            for obj in transformed_objs:
                data = self.obj_to_diffsync(obj=obj, mapping=element, model=model)
                item = model(**data)
                self.add(item)

    def get_identifier(self, obj, mapping):
        values = [obj.get(identifier, "") for identifier in mapping.identifiers]
        return values

    def obj_to_diffsync(self, obj: dict[str, Any], mapping: SchemaMappingModel, model: YamlfilesModel) -> dict:
        obj_id = self.generate_uuid_from_list(self.get_identifier(obj, mapping))
        data: dict[str, Any] = {"local_id": obj_id}
        for field in mapping.fields:
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
                    breakpoint()
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
        # breakpoint()
        return data

    def generate_uuid_from_list(self, values):
        """Generates a UUID based on a list of values."""
        combined_string = "-".join(map(str, values))  # Convert values to string and join
        hashed_value = hashlib.md5(combined_string.encode()).hexdigest()  # Hash the string
        return str(uuid.UUID(hashed_value[:32]))  # Use first 32 chars to create a UUID


class YamlfilesModel(DiffSyncModelMixin, DiffSyncModel):
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
