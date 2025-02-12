from __future__ import annotations

import asyncio
import ipaddress
from typing import TYPE_CHECKING, Any

try:
    from typing import Any, Self
except ImportError:
    from typing_extensions import Any, Self
import slurpit
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

# Create a new event loop for running async functions synchronously
loop = asyncio.new_event_loop()


class SlurpitsyncAdapter(DiffSyncMixin, Adapter):
    type = "Slurpitsync"

    def __init__(self, target: str, adapter: SyncAdapter, config: SyncConfig, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.target = target
        self.client = self._create_slurpit_client(adapter=adapter)
        self.config = config
        self.filtered_networks = []
        self.skipped = []

    def _create_slurpit_client(self, adapter: SyncAdapter) -> slurpit.api:
        settings = adapter.settings or {}
        client = slurpit.api(**settings)
        try:
            self.run_async(client.device.get_devices())
        except Exception as e:  # noqa: BLE001
            msg = f"Unable to connect to Slurpit API: {e}"
            raise ValueError(msg)
        return client

    def run_async(self, coroutine):
        """Utility to run asynchronous coroutines synchronously"""
        try:
            loop = asyncio.get_event_loop()
            if loop.is_closed():
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
            return loop.run_until_complete(coroutine)
        except RuntimeError:
            # If no event loop exists, create a new one
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            return loop.run_until_complete(coroutine)

    def unique_vendors(self) -> list[dict[str, Any]]:
        devices = self.run_async(self.client.device.get_devices())
        vendors = {device.brand for device in devices}
        return [{"brand": item} for item in vendors]

    def unique_device_type(self) -> list[dict[str, Any]]:
        devices = self.run_async(self.client.device.get_devices())
        device_types = {(device.brand, device.device_type, device.device_os) for device in devices}
        return [{"brand": item[0], "device_type": item[1], "device_os": item[2]} for item in device_types]

    def filter_networks(self) -> list:
        """Filter out networks based on ignore prefixes and normalize network/mask fields."""
        ignore_prefixes = [
            "0.0.0.0/0",
            "0.0.0.0/32",
            "::/0",
            "224.0.0.0/4",
            "255.255.255.255",
            "ff00::/8",
            "169.254.0.0/16",
            "fe80::/10",
            "127.0.0.0/8",
            "::1/128",
        ]

        def normalize_network(entry):
            network = entry.get("Network", "")
            mask = entry.get("Mask", "")
            if "/" in network:
                entry["normalized_prefix"] = network
            elif mask:
                entry["normalized_prefix"] = f"{network}/{mask}"
            else:
                entry["normalized_prefix"] = network
            return entry

        def should_ignore(network) -> bool:
            try:
                net = ipaddress.ip_network(network, strict=False)
                if net.prefixlen in {32, 128}:
                    return True
                return any(net == ipaddress.ip_network(ignore, strict=False) for ignore in ignore_prefixes)
            except ValueError:
                return False

        network_list = self.planning_results("routing-table")
        self.filtered_networks = [
            normalize_network(entry)
            for entry in network_list
            if not should_ignore(normalize_network(entry)["normalized_prefix"])
        ]
        return self.filtered_networks

    async def filter_interfaces(self, interfaces) -> list:
        precomputed_filtered_networks = [
            {
                "network": ipaddress.ip_network(prefix["normalized_prefix"], strict=False),
                "Vrf": prefix.get("Vrf", None),
            }
            for prefix in self.filtered_networks
        ]

        def normalize_and_find_prefix(entry):
            address = entry.get("IP", "")
            if address:
                if isinstance(address, list):
                    address = address[0]
                if "/" not in address:
                    address = f"{address}/32"
            else:
                return None

            try:
                network = ipaddress.ip_network(address, strict=False)
                entry["normalized_address"] = address
            except ValueError:
                return None

            for prefix in precomputed_filtered_networks:
                if network.subnet_of(prefix["network"]):
                    entry["prefix"] = str(prefix["network"])
                    entry["vrf"] = prefix["Vrf"]
                    break

            return entry

        # Concurrent execution of tasks
        tasks = [normalize_and_find_prefix(entry) for entry in interfaces if entry.get("IP")]

        # Run tasks concurrently
        filtered_interfaces = await asyncio.gather(*tasks)

        results = [entry for entry in filtered_interfaces if entry]

        # Filter out None values and return results
        return results

    def planning_results(self, planning_name):
        plannings = self.run_async(self.client.planning.get_plannings())
        planning = next((plan.to_dict() for plan in plannings if plan.slug == planning_name), None)
        if not planning:
            msg = f"No planning found for name: {planning_name}"
            raise IndexError(msg)

        search_data = {"planning_id": planning["id"], "unique_results": True}
        results = self.run_async(self.client.planning.search_plannings(search_data, limit=30000))
        return results or []

    def model_loader(self, model_name: str, model: SlurpitsyncModel) -> None:
        for element in self.config.schema_mapping:
            if element.name != model_name:
                continue

            if element.mapping.startswith("planning_results"):
                planning_name = element.mapping.split(".")[1]
                nodes = self.planning_results(planning_name)
            elif "." in element.mapping:
                app_name, resource_name = element.mapping.split(".")
                slurpit_app = getattr(self.client, app_name)
                slurpit_model = getattr(slurpit_app, resource_name)
                nodes = self.run_async(slurpit_model())
            elif element.mapping == "filter_interfaces":
                interfaces = self.planning_results("interfaces")
                nodes = self.run_async(self.filter_interfaces(interfaces))
            else:
                slurpit_model = getattr(self, element.mapping)
                nodes = slurpit_model()

            list_obj = []
            for node in nodes:
                if hasattr(node, "to_dict"):
                    list_obj.append(node.to_dict())
                else:
                    list_obj.append(node)
            total = len(list_obj)

            if self.config.source.name.title() == self.type.title():
                # Filter records
                filtered_objs = model.filter_records(records=list_obj, schema_mapping=element)
                print(f"{self.type}: Loading {len(filtered_objs)}/{total} {element.mapping}")
                # Transform records
                transformed_objs = model.transform_records(records=filtered_objs, schema_mapping=element)
            else:
                print(f"{self.type}: Loading all {total} {resource_name}")
                transformed_objs = list_obj

            for obj in transformed_objs:
                if data := self.slurpit_obj_to_diffsync(obj=obj, mapping=element, model=model):
                    item = model(**data)
                    try:  # noqa: SIM105
                        self.add(item)
                    except Exception:  # noqa: BLE001, S110
                        pass

        if self.skipped:
            print(f"{self.type}: skipped syncing {len(self.skipped)} models")

    # Reuse mapping from another adapter
    def build_mapping(self, reference, obj):
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

    def slurpit_obj_to_diffsync(
        self, obj: dict[str, Any], mapping: SchemaMappingModel, model: SlurpitsyncModel
    ) -> dict:
        obj_id = obj.get("id")
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
                    if node := obj.get(field.mapping):
                        matching_nodes = []
                        node_id = self.build_mapping(reference=field.reference, obj=obj)
                        matching_nodes = [item for item in nodes if str(item) == node_id]
                        if len(matching_nodes) == 0:
                            self.skipped.append(node)
                            return None
                            # Ideally we should raise an IndexError but there are some instances where Slurpit
                            # data has no dependencies so skipping is required.
                        node = matching_nodes[0]
                        data[field.name] = node.get_unique_id()
                else:
                    data[field.name] = []
                    if node := obj.get(field.mapping):
                        node_id = self.build_mapping(reference=field.reference, obj=obj)
                        matching_nodes = [item for item in nodes if str(item) == node_id]
                        if len(matching_nodes) == 0:
                            self.skipped.append(node)
                            continue
                            # Ideally we should raise an IndexError but there are some instances where Slurpit
                            # data has no dependencies so skipping is required.
                        data[field.name].append(matching_nodes[0].get_unique_id())
                    data[field.name] = sorted(data[field.name])
        return data


class SlurpitsyncModel(DiffSyncModelMixin, DiffSyncModel):
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
