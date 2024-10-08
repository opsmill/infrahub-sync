from __future__ import annotations

import asyncio
import ipaddress
from typing import Any, Mapping

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

# Create a new event loop for running async functions synchronously
loop = asyncio.new_event_loop()


class SlurpitsyncAdapter(DiffSyncMixin, Adapter):
    type = "Slurpitsync"

    # Constructor to initialize the adapter with the target, slurpit client, and config
    def __init__(self, *args, target: str, adapter: SyncAdapter, config: SyncConfig, **kwargs):
        super().__init__(*args, **kwargs)
        self.target = target
        self.client = self._create_slurpit_client(adapter)
        self.config = config
        self.filtered_networks = []

    # Create a slurpit client based on the adapter's settings
    def _create_slurpit_client(self, adapter):
        settings = adapter.settings or {}
        client = slurpit.api(**settings)
        return client

    # Utility to run asynchronous coroutines synchronously
    def run_async(self, coroutine):
        data = loop.run_until_complete(coroutine)
        return data

    # Retrieve unique device vendors
    def unique_vendors(self):
        devices = self.run_async(self.client.device.get_devices())
        vendors = set()  # Use a set to eliminate duplicates
        for device in devices:
            vendors.add(device.brand)
        return [{"brand": item} for item in vendors]

    # Retrieve unique device types based on brand, device type, and OS
    def unique_device_type(self):
        devices = self.run_async(self.client.device.get_devices())
        device_types = set()
        for device in devices:
            device_types.add((device.brand, device.device_type, device.device_os))
        return [{"brand": item[0], "device_type": item[1], "device_os": item[2]} for item in device_types]

    # Filter and normalize network entries while ignoring certain network prefixes
    def filter_networks(self):
        """Filter out networks based on ignore prefixes and normalize network/mask fields."""
        # Prefixes to be ignored (reserved, broadcast, loopback, etc.)
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

        # Normalize network and mask fields into CIDR notation
        def normalize_network(entry):
            """Normalize the network/mask fields and add a 'normalized_prefix' key to the entry."""
            network = entry.get("Network", "")
            mask = entry.get("Mask", "")

            # If network already has CIDR notation, use it
            if "/" in network:
                entry["normalized_prefix"] = network
            elif mask:
                # Combine network and mask if both are available
                entry["normalized_prefix"] = f"{network}/{mask}"
            else:
                # Otherwise, just use the network as is
                entry["normalized_prefix"] = network

            return entry

        # Check if a network is in the ignore list or is a /32 (IPv4) or /128 (IPv6) network
        def should_ignore(network):
            """Check if the given network is in the ignore list or is a /32 (IPv4) or /128 (IPv6) network."""
            try:
                net = ipaddress.ip_network(network, strict=False)

                # Ignore single-host networks (IPv4 /32 and IPv6 /128)
                if net.prefixlen in {32, 128}:
                    return True

                # Check if the network exactly matches any prefix in the ignore list
                return any(net == ipaddress.ip_network(ignore, strict=False) for ignore in ignore_prefixes)
            except ValueError:
                # If the network is invalid, skip it
                return False

        # Get the list of networks from the planning results
        network_list = self.planning_results("routing-table")

        # Initialize an empty list for filtered networks
        filtered_networks = []

        # Iterate over the network list, normalize, and filter each entry
        for entry in network_list:
            normalized_data = normalize_network(entry)  # Normalize network and mask

            # Only include networks that should not be ignored
            if not should_ignore(normalized_data["normalized_prefix"]):
                filtered_networks.append(normalized_data)

        self.filtered_networks = filtered_networks
        return filtered_networks

    def filter_interfaces(self):
        # Get the list of interfaces from the planning results
        interfaces = self.planning_results("interfaces")

        # Normalize IP addresses into CIDR notation
        def normalize_address(entry):
            """Normalize the IP field and add a 'normalized_address' key to the entry."""
            address = entry.get("IP", "")

            if address:
                if isinstance(address, list):
                    address = address[0]
                # If the IP already has CIDR notation, keep it as is
                if "/" in address:
                    entry["normalized_address"] = address
                else:
                    # Otherwise, treat it as a /32 host address
                    entry["normalized_address"] = f"{address}/32"
            else:
                # If no IP field is present, return None (optional error handling)
                return None

            return entry

        # Get Prefix for IP Address
        def get_prefix(address):
            """
            Check if the given address falls within any prefix from filtered_networks.
            :param address: The IP address in CIDR format.
            :return: The matching prefix or None if no match is found.
            """
            try:
                # Convert the address to an ip_network object
                network = ipaddress.ip_network(address, strict=False)

                # Iterate over the filtered networks to find the matching prefix
                for prefix in self.filtered_networks:
                    prefix_network = ipaddress.ip_network(prefix["normalized_prefix"], strict=False)
                    if network.subnet_of(prefix_network):
                        return prefix

            except ValueError as e:
                print(f"Invalid IP address or prefix: {e}")
                return None

            return None

        # Normalize addresses for all interfaces
        interfaces = [normalize_address(entry) for entry in interfaces if normalize_address(entry)]
        # Initialize an empty list for filtered interfaces
        filtered_interfaces = []

        # Iterate over the interfaces, normalize, and filter based on prefixes
        for entry in interfaces:
            if prefix := get_prefix(entry["normalized_address"]):
                entry["prefix"] = prefix["normalized_prefix"]
                entry["vrf"] = prefix.get("Vrf", None)
            filtered_interfaces.append(entry)

        return filtered_interfaces

    # Retrieve planning results for a specific planning name
    def planning_results(self, planning_name):
        # Convert the planning name to match the format used by the API
        plannings = self.run_async(self.client.planning.get_plannings())
        planning = None
        for plan in plannings:
            if plan.slug == planning_name:
                planning = plan.to_dict()
        if not planning:
            raise IndexError(f"No planning found for name: {planning_name}")

        # Search for results using the planning ID
        search_data = {"planning_id": planning["id"], "unique_results": True, "offset": 0, "limit": 1000}

        results = self.run_async(self.client.planning.search_plannings(search_data))
        if not results:
            return []

        return results

    def model_loader(self, model_name: str, model: SlurpitsyncModel):
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
                data = self.slurpit_obj_to_diffsync(obj=obj, mapping=element, model=model)
                item = model(**data)
                try:
                    self.add(item)
                except Exception:  # noqa: S110
                    pass

    def slurpit_obj_to_diffsync(
        self, obj: dict[str, Any], mapping: SchemaMappingModel, model: SlurpitsyncModel
    ) -> dict:  # noqa: C901
        obj_id = obj.get("id", None)
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
                raise NotImplementedError(
                    "It's not supported yet to have an attribute of type list with a simple mapping"
                )
            elif field.mapping and field.reference:
                all_nodes_for_reference = self.store.get_all(model=field.reference)
                nodes = [item for item in all_nodes_for_reference]
                if not nodes and all_nodes_for_reference:
                    raise IndexError(
                        f"Unable to get '{field.mapping}' with '{field.reference}' reference from store."
                        f" The available models are {self.store.get_all_model_names()}"
                    )
                if not field_is_list:
                    if node := get_value(obj, field.mapping):
                        if isinstance(node, dict):
                            matching_nodes = []
                            node_id = node.get("id", None)
                            matching_nodes = [item for item in nodes if item.local_id == str(node_id)]
                            if len(matching_nodes) == 0:
                                raise IndexError(f"Unable to locate the node {field.name} {node_id}")
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
                        if not node_id:
                            if isinstance(node, tuple):
                                node_id = node[1] if node[0] == "id" else None
                                if not node_id:
                                    continue
                        matching_nodes = [item for item in nodes if item.local_id == str(node_id)]
                        if len(matching_nodes) == 0:
                            raise IndexError(f"Unable to locate the node {field.reference} {node_id}")
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
    ):
        # TODO
        return super().create(adapter=adapter, ids=ids, attrs=attrs)

    def update(self, attrs):
        # TODO
        return super().update(attrs)
