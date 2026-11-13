from __future__ import annotations

import json
import logging
import os
from typing import Any, ClassVar

from diffsync import Adapter, DiffSyncModel

from infrahub_sync import (
    DiffSyncMixin,
    DiffSyncModelMixin,
    SchemaMappingModel,
    SyncAdapter,
    SyncConfig,
)
from infrahub_sync.adapters.utils import build_mapping
from infrahub_sync.configuration.credentials import declared_settings, is_registered_context

logger = logging.getLogger(__name__)

try:
    # declared in the `ipfabric` extra, not the base install or the default `service` image
    from ipfabric import IPFClient  # ty: ignore[unresolved-import]
except ImportError:
    logger.exception("Failed to import ipfabric")
    raise

ipf_filters = {
    "tables/inventory/summary/platforms": {"and": [{"platform": ["empty", False]}]},
    "tables/inventory/summary/models": {"and": [{"model": ["empty", False]}]},
    "tables/inventory/pn": {"and": [{"name": ["empty", False]}]},
}


class IpfabricsyncAdapter(DiffSyncMixin, Adapter):
    type: ClassVar[str] = "IPFabricsync"

    def __init__(self, target: str, adapter: SyncAdapter, config: SyncConfig, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)

        self.target = target
        self.client = self._create_ipfabric_client(adapter)
        self.config = config

    def _create_ipfabric_client(self, adapter: SyncAdapter) -> IPFClient:
        registered = is_registered_context(adapter.settings or {})
        settings = declared_settings(adapter.settings or {})

        if registered:
            if not settings.get("base_url") or not settings.get("auth"):
                msg = "Both base_url and auth must be declared in the registered package settings."
                raise ValueError(msg)
        else:
            if not settings.get("base_url"):
                settings["base_url"] = os.environ.get("IPF_URL", None)
            if not settings.get("auth"):
                settings["auth"] = os.environ.get("IPF_TOKEN", None)
            if not settings["base_url"] or not settings["auth"]:
                msg = "Both url and auth must be specified! Please specify in the config or using `IPF_URL` and `IPF_TOKEN` environment variables."
                raise ValueError(msg)

        # Registered settings reach the optional client here. `IPFClient` takes `verify`,
        # not the `verify_ssl` name used by this adapter's registered settings, so rename it;
        # `IPFClient`'s own default for `verify` is `None`, so passing it unset is harmless.
        verify_ssl = settings.pop("verify_ssl", None)
        sdk_defaults: dict[str, object] = {}
        if registered:
            # `IPFClient` reads `IPF_*` variables and a `.env` file for every field left
            # unset, so a registered run sets the ones that decide trust and data: TLS is
            # verified unless declared otherwise, and the snapshot is the SDK default.
            verify_ssl = True if verify_ssl is None else verify_ssl
            sdk_defaults = {"snapshot_id": "$last"}
        return IPFClient(verify=verify_ssl, **sdk_defaults, **settings)

    def list_records(self, model_name: str, element: SchemaMappingModel) -> list[dict[str, Any]]:  # noqa: ARG002
        """Fetch the rows of the mapping entry's IP Fabric table.

        The base-class read loop stores each record with `add`, so two rows with one identity
        are a `duplicate_source_identity` problem; the adapter no longer overwrites the first
        one silently.
        """
        if not element.mapping:
            return []
        return self.client.fetch_all(element.mapping, filters=ipf_filters.get(element.mapping))

    def record_to_data(self, record: dict, element: SchemaMappingModel, model: type[IpfabricsyncModel]) -> dict:  # pylint: disable=too-many-branches
        """Convert an IP Fabric row to model data; a reference resolves by the peer's identity values."""
        obj = record
        mapping = element
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
                if not field_is_list and (node := obj[field.mapping]):
                    node_id = build_mapping(adapter=self, reference=field.reference, obj=obj, field=field)
                    matching_nodes = [item for item in nodes if str(item) == node_id]
                    if len(matching_nodes) == 0:
                        data[field.name] = None
                    else:
                        node = matching_nodes[0]
                        data[field.name] = node.get_unique_id()
        return data


class IpfabricsyncModel(DiffSyncModelMixin, DiffSyncModel):
    """DiffSync model for ipfabricsync records."""
