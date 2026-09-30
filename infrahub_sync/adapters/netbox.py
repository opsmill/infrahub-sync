from __future__ import annotations

# pylint: disable=R0801
import logging
from typing import TYPE_CHECKING, Any, get_args

import pynetbox  # ty: ignore[unresolved-import]  # optional dep, absent on the Python 3.10 profile
from diffsync import Adapter, DiffSyncModel
from requests import Session

from infrahub_sync import (
    DiffSyncMixin,
    DiffSyncModelMixin,
    SchemaMappingModel,
    SyncAdapter,
    SyncConfig,
)
from infrahub_sync.cache.cursors import CursorState, CursorTier
from infrahub_sync.configuration.credentials import select_runtime_credential

from .utils import get_value

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from collections.abc import Iterator


def _record_ids(records: list[dict]) -> set[str]:
    """Return usable NetBox IDs without treating a missing ID as an excluded peer."""
    return {str(record["id"]) for record in records if record.get("id") is not None}


def _excluded_reference_ids(adapter: NetboxAdapter, reference: str) -> set[str]:
    """Identify peers excluded by declared filters, including on incremental loads."""
    excluded = getattr(adapter, "_filtered_peer_ids", {})
    complete = getattr(adapter, "_complete_filtered_peers", set())
    config = getattr(adapter, "config", None)
    if reference in complete or config is None or getattr(adapter, "target", "source") != "source":
        return excluded.get(reference, set())

    raw_ids: set[str] = set()
    retained_ids: set[str] = set()
    for element in config.schema_mapping:
        if element.name != reference or not element.mapping or not element.filters:
            continue
        model: type[NetboxModel] = getattr(adapter, reference)
        # An owner can load before its peer kind. The first lookup then reads the
        # entire peer endpoint to distinguish filtered IDs from absent IDs; a
        # later model load can read that endpoint again. Direct incremental
        # callers pay the same full-read cost on their first lookup.
        raw = [dict(node) for node in adapter._resolve_endpoint(element.mapping).all()]
        raw_ids.update(_record_ids(raw))
        retained_ids.update(_record_ids(model.filter_records(records=raw, schema_mapping=element)))
    excluded[reference] = raw_ids - retained_ids
    complete.add(reference)
    adapter._filtered_peer_ids = excluded
    adapter._complete_filtered_peers = complete
    return excluded[reference]


def _reference_is_optional(model: type[NetboxModel], field_name: str) -> bool:
    """Read the destination relationship's nullability from its model field."""
    return type(None) in get_args(model.model_fields[field_name].annotation)


class NetboxAdapter(DiffSyncMixin, Adapter):
    type = "Netbox"

    def __init__(self, target: str, adapter: SyncAdapter, config: SyncConfig, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)

        self.target = target
        self.client = self._create_netbox_client(adapter)
        self.config = config
        self._filtered_peer_ids: dict[str, set[str]] = {}
        self._complete_filtered_peers: set[str] = set()

    def _create_netbox_client(self, adapter: SyncAdapter) -> pynetbox.api:
        settings = adapter.settings or {}
        url = select_runtime_credential(settings, "url", ("NETBOX_ADDRESS", "NETBOX_URL"))
        token = select_runtime_credential(settings, "token", ("NETBOX_TOKEN",))
        verify_ssl = settings.get("verify_ssl", True)

        if not url or not token:
            msg = "Both url and token must be specified!"
            raise ValueError(msg)

        client = pynetbox.api(url, token=token)
        # Set SSL verification
        session = Session()
        session.verify = verify_ssl
        client.http_session = session
        return client

    def cursor_tier_for(self, model_name: str) -> CursorTier:
        """Return TIMESTAMP for any kind we have a schema_mapping for.

        pynetbox DCIM/IPAM/Circuits/Tenancy endpoints uniformly support
        `last_updated__gte`. Kinds not in the schema_mapping fall back to
        NONE so the engine never attempts an incremental query for them.
        """
        for element in self.config.schema_mapping:
            if element.name == model_name and element.mapping:
                return CursorTier.TIMESTAMP
        return CursorTier.NONE

    def _resolve_endpoint(self, mapping: str) -> Any:
        """Walk `mapping` (e.g. 'dcim.devices' or 'plugins.foo.bar') to a pynetbox endpoint."""
        parts = mapping.split(".")
        endpoint = self.client
        for part in parts:
            try:
                endpoint = getattr(endpoint, part)
            except AttributeError as exc:
                msg = f"Invalid NetBox mapping path {mapping!r} (missing segment {part!r})"
                raise ValueError(msg) from exc
        return endpoint

    def _records_to_diffsync(
        self,
        *,
        element: SchemaMappingModel,
        model: type[NetboxModel],
        raw_records: list[dict],
        already_filtered: bool = False,
    ) -> Iterator[dict]:
        """Filter+transform NetBox records and yield diffsync-ready dicts.

        Same transformation flow as model_loader, factored out for reuse by
        list_changed_since. Pass `already_filtered=True` when the caller has
        run `filter_records` itself (e.g. to log a filtered count) so records
        aren't filtered twice.
        """
        if self.target == "source":
            filtered = (
                raw_records if already_filtered else model.filter_records(records=raw_records, schema_mapping=element)
            )
            transformed = model.transform_records(records=filtered, schema_mapping=element)
        else:
            transformed = raw_records
        for obj in transformed:
            yield self.netbox_obj_to_diffsync(obj=obj, mapping=element, model=model)

    def list_changed_since(self, model_name: str, cursor: CursorState) -> Iterator[dict]:
        """Yield NetBox records changed since `cursor`. Uses `last_updated__gte` filter."""
        element = next(
            (e for e in self.config.schema_mapping if e.name == model_name),
            None,
        )
        if element is None or not element.mapping:
            msg = f"NetBox: no schema_mapping entry with mapping for {model_name!r}"
            raise NotImplementedError(msg)

        model: type[NetboxModel] = getattr(self, model_name)
        endpoint = self._resolve_endpoint(element.mapping)
        raw = [dict(node) for node in endpoint.filter(last_updated__gte=cursor.value)]
        yield from self._records_to_diffsync(element=element, model=model, raw_records=raw)

    def list_existing_ids(self, model_name: str) -> Iterator[str]:
        """Yield current unique IDs for `model_name` from NetBox.

        The unique ID is computed by the existing diffsync model:
        `model(**netbox_obj_to_diffsync(...)).get_unique_id()`.
        Adapters that override the identifier convention will produce
        correctly-shaped IDs without further work here.
        """
        element = next(
            (e for e in self.config.schema_mapping if e.name == model_name),
            None,
        )
        if element is None or not element.mapping:
            msg = f"NetBox: no schema_mapping entry with mapping for {model_name!r}"
            raise NotImplementedError(msg)

        model: type[NetboxModel] = getattr(self, model_name)
        endpoint = self._resolve_endpoint(element.mapping)
        raw_records = [dict(node) for node in endpoint.all()]
        for payload in self._records_to_diffsync(element=element, model=model, raw_records=raw_records):
            yield model(**payload).get_unique_id()

    def model_loader(self, model_name: str, model: type[NetboxModel]) -> None:
        """
        Load and process models using schema mapping filters and transformations.

        This method retrieves data from Netbox, applies filters and transformations
        as specified in the schema mapping, and loads the processed data into the adapter.
        """
        raw_ids: set[str] = set()
        retained_ids: set[str] = set()
        for element in self.config.schema_mapping:
            if element.name != model_name:
                continue

            if not element.mapping:
                logger.info("No mapping defined for '%s', skipping", element.name)
                continue

            # Supports nested attribute paths (e.g. "plugins.foo.bar") for
            # pynetbox plugin endpoints.
            resource_name = element.mapping.split(".")[-1]
            endpoint = self._resolve_endpoint(element.mapping)

            # Retrieve all objects (RecordSet) and convert to dicts.
            raw_records = [dict(node) for node in endpoint.all()]
            total = len(raw_records)

            if self.target == "source":
                filtered = model.filter_records(records=raw_records, schema_mapping=element)
                if element.filters:
                    raw_ids.update(_record_ids(raw_records))
                    retained_ids.update(_record_ids(filtered))
                logger.info("%s: Loading %d/%d %s", self.type, len(filtered), total, resource_name)
            else:
                filtered = raw_records
                logger.info("%s: Loading all %d %s", self.type, total, resource_name)

            # Create model instances after transforming — records are already
            # filtered above, so `_records_to_diffsync` must not filter again.
            for data in self._records_to_diffsync(
                element=element, model=model, raw_records=filtered, already_filtered=True
            ):
                item = model(**data)
                self.add(item)
        if self.target == "source":
            self._filtered_peer_ids[model_name] = raw_ids - retained_ids
            self._complete_filtered_peers.add(model_name)

    def netbox_obj_to_diffsync(
        self, obj: dict[str, Any], mapping: SchemaMappingModel, model: type[NetboxModel]
    ) -> dict:
        obj_id = obj.get("id")
        data: dict[str, Any] = {"local_id": str(obj_id)}

        if not mapping.fields:
            return data
        for field in mapping.fields:  # pylint: disable=too-many-nested-blocks
            field_is_list = model.is_list(name=field.name)

            if field.static is not None:
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
                if not field_is_list:
                    if node := get_value(obj, field.mapping):
                        if isinstance(node, dict):
                            node_id = node.get("id", None)
                            matching_nodes = [item for item in nodes if item.local_id == str(node_id)]  # ty: ignore[unresolved-attribute]
                            if len(matching_nodes) == 0:
                                if str(node_id) in _excluded_reference_ids(self, field.reference):
                                    if _reference_is_optional(model, field.name):
                                        logger.warning(
                                            "NetBox filter excluded peer from %s record %s field %s "
                                            "(peer kind %s, IDs: %s)",
                                            mapping.name,
                                            obj_id,
                                            field.name,
                                            field.reference,
                                            node_id,
                                        )
                                        continue
                                    msg = (
                                        f"Configured filter excluded all peers for required relationship "
                                        f"{mapping.name} record {obj_id} field {field.name} "
                                        f"(peer kind {field.reference}): {node_id}"
                                    )
                                    raise ValueError(msg)
                                msg = f"Unable to locate the node {field.name} {node_id}"
                                raise IndexError(msg)
                            node = matching_nodes[0]
                            data[field.name] = node.get_unique_id()
                        else:
                            data[field.name] = node
                else:
                    data[field.name] = []
                    excluded_ids: list[str] = []
                    for node in get_value(obj, field.mapping) or []:
                        if not node:
                            continue
                        node_id = node.get("id", None)
                        matching_nodes = [item for item in nodes if item.local_id == str(node_id)]  # ty: ignore[unresolved-attribute]
                        if len(matching_nodes) == 0:
                            if str(node_id) in _excluded_reference_ids(self, field.reference):
                                excluded_ids.append(str(node_id))
                                continue
                            msg = f"Unable to locate the node {field.reference} {node_id}"
                            raise IndexError(msg)
                        data[field.name].append(matching_nodes[0].get_unique_id())
                    if excluded_ids:
                        logger.warning(
                            "NetBox filter excluded peers from %s record %s field %s (peer kind %s, IDs: %s)",
                            mapping.name,
                            obj_id,
                            field.name,
                            field.reference,
                            ", ".join(excluded_ids),
                        )
                    if excluded_ids and not data[field.name] and not _reference_is_optional(model, field.name):
                        msg = (
                            f"Configured filter excluded all peers for required relationship "
                            f"{mapping.name} record {obj_id} field {field.name} "
                            f"(peer kind {field.reference}): {', '.join(excluded_ids)}"
                        )
                        raise ValueError(msg)
                    data[field.name] = sorted(data[field.name])

        return data


class NetboxModel(DiffSyncModelMixin, DiffSyncModel):
    """DiffSync model for netbox records."""
