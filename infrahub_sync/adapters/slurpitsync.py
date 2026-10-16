from __future__ import annotations

import asyncio
import contextlib
import ipaddress
import logging
import weakref
from concurrent.futures import ThreadPoolExecutor
from typing import TYPE_CHECKING, Any, TypeVar

import httpx
import slurpit  # ty: ignore[unresolved-import]  # declared as `slurpit-sdk` in the optional `slurpit` extra
from diffsync import Adapter, DiffSyncModel
from diffsync.exceptions import ObjectNotFound

from infrahub_sync import (
    DiffSyncMixin,
    DiffSyncModelMixin,
    SchemaMappingModel,
    SyncAdapter,
    SyncConfig,
)
from infrahub_sync.adapters.references import owner_kind, reference_is_required, resolve_peer_list
from infrahub_sync.adapters.utils import build_mapping, get_value
from infrahub_sync.configuration.credentials import declared_settings

logger = logging.getLogger(__name__)
T = TypeVar("T")
READ_ONLY_SDK_MAPPINGS = frozenset({"device.get_devices", "site.get_sites"})

if TYPE_CHECKING:
    from collections.abc import Coroutine


async def _close_sdk_clients(client: slurpit.api) -> None:
    """Close each per-API HTTP client owned by the Slurp'it SDK."""
    clients = {
        id(http_client): http_client
        for api in vars(client).values()
        if isinstance(http_client := getattr(api, "client", None), httpx.AsyncClient)
    }
    results = await asyncio.gather(*(http_client.aclose() for http_client in clients.values()), return_exceptions=True)
    for result in results:
        if isinstance(result, BaseException):
            raise result


def _close_resources(loop: asyncio.AbstractEventLoop, client: slurpit.api | None) -> None:
    """Release SDK transports on their loop, then release the loop itself."""
    if loop.is_closed():
        return

    def close_on_adapter_loop() -> None:
        """Finish cleanup where the adapter loop can run independently."""
        try:
            if client is not None:
                loop.run_until_complete(_close_sdk_clients(client))
        finally:
            loop.close()

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        close_on_adapter_loop()
    else:
        with ThreadPoolExecutor(max_workers=1) as executor:
            executor.submit(close_on_adapter_loop).result()


class SlurpitsyncAdapter(DiffSyncMixin, Adapter):
    type = "Slurpitsync"

    def __init__(self, target: str, adapter: SyncAdapter, config: SyncConfig, *args, **kwargs) -> None:
        """Initialize the adapter and its private event loop."""
        super().__init__(*args, **kwargs)
        self.target = target
        self._loop = asyncio.new_event_loop()
        try:
            self.client = self._create_slurpit_client(adapter=adapter)
        except BaseException:
            self.close()
            raise
        self._finalizer = weakref.finalize(self, _close_resources, self._loop, self.client)
        self.config = config
        self.filtered_networks = []

    def _create_slurpit_client(self, adapter: SyncAdapter) -> slurpit.api:
        """Create and check the configured Slurp'it client."""
        # `slurpit.api` has a fixed signature, so the registered-context marker stays behind.
        settings = declared_settings(adapter.settings or {})
        verify = settings.pop("verify_ssl", True)
        client = slurpit.api(verify=verify, **settings)
        self.client = client
        try:
            self.run_async(client.device.get_devices())
        except Exception as e:
            msg = f"Unable to connect to Slurpit API: {e}"
            raise ValueError(msg) from e
        return client

    def run_async(self, coroutine: Coroutine[Any, Any, T]) -> T:
        """Run an SDK coroutine on the loop shared by this adapter's client."""
        return self._loop.run_until_complete(coroutine)

    def load(self) -> None:
        """Load Slurp'it models and release the SDK connection afterward."""
        try:
            super().load()
        finally:
            self.close()

    def close(self) -> None:
        """Close SDK clients and the loop once, even for an unloaded adapter."""
        finalizer = getattr(self, "_finalizer", None)
        if finalizer is not None:
            finalizer()
        else:
            _close_resources(self._loop, getattr(self, "client", None))

    def unique_vendors(self) -> list[dict[str, Any]]:
        """Return each vendor found among Slurp'it devices."""
        devices = self.run_async(self.client.device.get_devices())
        vendors = {device.brand for device in devices}
        return [{"brand": item} for item in vendors]

    def unique_device_type(self) -> list[dict[str, Any]]:
        """Return distinct device brand, type, and OS combinations."""
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
                prefix = network
            elif mask:
                prefix = f"{network}/{mask}"
            else:
                prefix = network
            # Use the network address so it matches the prefix computed for IP addresses.
            with contextlib.suppress(ValueError):
                prefix = str(ipaddress.ip_network(prefix, strict=False))
            entry["normalized_prefix"] = prefix
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

        # Process interfaces synchronously (normalize_and_find_prefix is not async)
        filtered_interfaces = [normalize_and_find_prefix(entry) for entry in interfaces if entry.get("IP")]

        results = [entry for entry in filtered_interfaces if entry]

        # Filter out None values and return results
        return results

    def planning_results(self, planning_name):
        """Fetch the rows for a planning slug."""
        plannings = self.run_async(self.client.planning.get_plannings())
        planning = next((plan.to_dict() for plan in plannings if plan.slug == planning_name), None)
        if not planning:
            msg = f"No planning found for name: {planning_name}"
            raise IndexError(msg)

        search_data = {"planning_id": planning["id"], "unique_results": True}
        results = self.run_async(self.client.planning.search_plannings(search_data, limit=30000))
        return results or []

    def list_records(self, model_name: str, element: SchemaMappingModel) -> list[Any]:  # noqa: ARG002
        """Fetch the rows of `element.mapping` from Slurp'it, as dicts, before any filter."""
        mapping = element.mapping or ""
        if mapping.startswith("planning_results"):
            planning_name = mapping.split(".")[1]
            nodes = self.planning_results(planning_name)
        elif "." in mapping:
            if mapping not in READ_ONLY_SDK_MAPPINGS:
                msg = f"Unsupported Slurp'it SDK mapping: {mapping}"
                raise ValueError(msg)
            app_name, resource_name = mapping.split(".")
            slurpit_app = getattr(self.client, app_name)
            slurpit_model = getattr(slurpit_app, resource_name)
            nodes = self.run_async(slurpit_model())
        elif mapping == "filter_interfaces":
            interfaces = self.planning_results("interfaces")
            nodes = self.run_async(self.filter_interfaces(interfaces))
        else:
            slurpit_model = getattr(self, mapping)
            nodes = slurpit_model()

        list_obj = [node.to_dict() if hasattr(node, "to_dict") else node for node in nodes]
        logger.info("%s: Read %d %s", self.type, len(list_obj), mapping)
        return list_obj

    def _peer_by_unique_id(
        self, peer_kind: str, unique_id: str, *, owner: tuple[str, str | None], field: str, required: bool
    ) -> str | None:
        """Resolve a reference Slurp'it states as the peer's unique ID.

        A peer the store does not hold resolves through `peer_unique_id`, with the unique ID
        as the peer's source value.
        """
        try:
            return self.store.get(model=peer_kind, identifier=unique_id).get_unique_id()
        except ObjectNotFound:
            return self.peer_unique_id(peer_kind, unique_id, owner=owner, field=field, required=required)

    def record_to_data(
        self, record: dict[str, Any], element: SchemaMappingModel, model: type[DiffSyncModel]
    ) -> dict[str, Any]:
        """Convert a Slurp'it dict to a DiffSync-ready payload; references resolve through the base class."""
        obj_id = record.get("id")
        data: dict[str, Any] = {"local_id": str(obj_id)}
        owner = (owner_kind(model, element.name), str(obj_id))

        for field in element.fields:
            field_is_list = model.is_list(name=field.name)

            if field.static:
                data[field.name] = field.static
            elif not field_is_list and field.mapping and not field.reference:
                value = get_value(record, field.mapping)
                if value is not None:
                    data[field.name] = value
            elif field_is_list and field.mapping and not field.reference:
                msg = "It's not supported yet to have an attribute of type list with a simple mapping"
                raise NotImplementedError(msg)
            elif field.mapping and field.reference:
                required = reference_is_required(model, field.name)
                if not field_is_list:
                    if node := record.get(field.mapping):
                        # If node is a simple string/value, use it directly; otherwise build from nested object
                        if isinstance(node, (str, int)):
                            node_id = str(node)
                        else:
                            node_id = build_mapping(adapter=self, reference=field.reference, obj=record, field=field)
                        unique_id = self._peer_by_unique_id(
                            field.reference, node_id, owner=owner, field=field.name, required=required
                        )
                        if unique_id is not None:
                            data[field.name] = unique_id
                else:
                    data[field.name] = []
                    if record.get(field.mapping):
                        node_id = build_mapping(adapter=self, reference=field.reference, obj=record, field=field)
                        try:
                            data[field.name] = [
                                self.store.get(model=field.reference, identifier=node_id).get_unique_id()
                            ]
                        except ObjectNotFound:
                            data[field.name], _ = resolve_peer_list(
                                self,
                                peer_kind=field.reference,
                                peer_source_ids=[node_id],
                                owner=owner,
                                field=field.name,
                                required=required,
                            )
        return data


class SlurpitsyncModel(DiffSyncModelMixin, DiffSyncModel):
    """DiffSync model for slurpitsync records."""
