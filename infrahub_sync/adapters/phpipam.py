from __future__ import annotations

import os
from typing import TYPE_CHECKING, Any

try:
    from typing import Self
except ImportError:
    from typing_extensions import Self

import phpypam
from diffsync import Adapter, DiffSyncModel

from infrahub_sync import (
    DiffSyncMixin,
    DiffSyncModelMixin,
    SchemaMappingModel,
    SyncAdapter,
    SyncConfig,
)

from .utils import derive_identifier_key, get_value

if TYPE_CHECKING:
    from collections.abc import Mapping


class PhpipamAdapter(DiffSyncMixin, Adapter):
    type = "Phpipam"

    def __init__(self, target: str, adapter: SyncAdapter, config: SyncConfig, *Parameters, **kwParameters) -> None:
        super().__init__(*Parameters, **kwParameters)

        self.target = target
        self.client = self._create_phpipam_client(adapter)
        self.config = config

    def _create_phpipam_client(self, adapter: SyncAdapter) -> phpipamsdk.PhpIpamClient:
        settings = adapter.settings or {}
        url = os.environ.get("PHPIPAM_URL") or settings.get("url")
        app_id = os.environ.get("PHPIPAM_APP_ID") or settings.get("app_id")
        username = os.environ.get("PHPIPAM_USERNAME") or settings.get("username")
        password = os.environ.get("PHPIPAM_PASSWORD") or settings.get("password")
        token = os.environ.get("PHPIPAM_TOKEN") or settings.get("token")
        verify_ssl = settings.get("verify_ssl", True)

        if not url or not app_id:
            msg = "Both url and app_id must be specified!"
            raise ValueError(msg)

        if not ((username and password) or token):
            msg = "Either username/password pair or token must be specified!"
            raise ValueError(msg)

        client = phpypam.api(
            url=url,
            app_id=app_id,
            username=username,
            password=password,
            token=token,
            ssl_verify=verify_ssl,
        )

        # Test connection
        token = client.get_token()
        if not token:
            msg = "Unable to connect to phpIPAM API"
            raise ValueError(msg)

        return client

    def model_loader(self, model_name: str, model: PhpipamModel) -> None:
        """
        Load and process models using schema mapping filters and transformations.

        This method retrieves data from phpIPAM, applies filters and transformations
        as specified in the schema mapping, and loads the processed data into the adapter.
        """
        for element in self.config.schema_mapping:
            if element.name != model_name:
                continue

            # Use the resource endpoint from the schema mapping
            resource_name = element.mapping
            try:
                # Determine which phpIPAM controller to use based on resource_name
                if resource_name == "subnets":
                    objs = self.client.get_entity(controller="subnets")
                elif resource_name == "addresses":
                    objs = self.client.get_entity(controller="addresses")
                elif resource_name == "vlans":
                    objs = self.client.get_entity(controller="vlan")
                elif resource_name == "devices":
                    objs = self.client.get_entity(controller="devices")
                elif resource_name == "sections":
                    objs = self.client.get_entity(controller="sections")
                elif resource_name == "vrfs":
                    objs = self.client.get_entity(controller="vrf")
                # TODO: For other resources, try a generic approach ?
            except Exception as exc:  # noqa: BLE001
                print(f"Failed to get {resource_name} from phpIPAM: {exc}")
                continue

            if not objs or not isinstance(objs, list):
                print(f"No data returned for {resource_name} or invalid format")
                continue

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
        model: PhpipamModel,
    ) -> dict[str, Any]:
        """
        Transform phpIPAM data to DiffSync format.

        Parameters:
            obj: The phpIPAM object data
            mapping: The schema mapping element
            model: The DiffSync model class

        Returns:
            dict: The transformed data
        """
        try:
            obj_id = derive_identifier_key(obj=obj)
        except ValueError:
            # Try to get the ID based on the resource type
            resource_type = mapping.mapping.rstrip("s")
            id_field = f"{resource_type}Id"

            if obj.get(id_field):
                obj_id = obj[id_field]
            else:
                msg = f"No suitable identifier key found in object: {obj}"
                raise ValueError(msg)

        data: dict[str, Any] = {"local_id": str(obj_id)}

        # Check if this is a subnet/prefix object from phpIPAM
        is_subnet_object = "subnet" in obj and "mask" in obj

        for field in mapping.fields:
            field_is_list = model.is_list(name=field.name)

            if field.static:
                data[field.name] = field.static
            elif not field_is_list and field.mapping and not field.reference:
                # For subnet objects in phpIPAM, combine subnet and mask
                if is_subnet_object and field.mapping == "subnet":
                    subnet = obj.get("subnet")
                    mask = obj.get("mask")
                    if subnet and mask:
                        data[field.name] = f"{subnet}/{mask}"
                        continue

                value = get_value(obj, field.mapping)

                # Default to /32 for IPv4 or /128 for IPv6 if no mask is provided
                if field.mapping == "ip" and value and "/" not in value:
                    value = f"{value}/128" if ":" in value else f"{value}/32"

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
                    # Use the build_mapping function to get the reference ID
                    ref_id = self.build_mapping(
                        reference_model=field.reference,
                        field_mapping=field.mapping,
                        obj=obj
                    )
                    if ref_id:
                        matching_nodes = [item for item in nodes if item.local_id == ref_id]
                        if matching_nodes:
                            node = matching_nodes[0]
                            data[field.name] = node.get_unique_id()
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

    def build_mapping(
            self,
            reference_model: str,
            field_mapping: str,
            obj: dict
        ) -> str:
        """
        Build a reference mapping for a related node.

        Parameters:
            reference_model: The model name of the referenced object
            field_mapping: The field mapping in the current object that points to the reference
            obj: The current object data

        Returns:
            A string that can be used to identify the referenced object
        """
        # Get model name from the store
        _, modelname = self.store._get_object_class_and_model(model=reference_model)

        # Find the schema element matching the model name
        schema_element = next(
            (element for element in self.config.schema_mapping if element.name == modelname),
            None,
        )

        if not schema_element:
            return str(obj.get(field_mapping, ""))

        # Get the value from the current object that points to the reference
        ref_value = obj.get(field_mapping)
        if not ref_value:
            return ""

        # For phpIPAM, the reference is usually just an ID
        if isinstance(ref_value, (str, int)):
            return str(ref_value)


class PhpipamModel(DiffSyncModelMixin, DiffSyncModel):
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
