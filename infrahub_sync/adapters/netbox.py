from __future__ import annotations

# pylint: disable=R0801
import logging
import os
from typing import TYPE_CHECKING, Any

import pynetbox  # ty: ignore[unresolved-import]  # optional dep, see pyproject extras
from diffsync import Adapter, DiffSyncModel
from requests import Session
from typing_extensions import Self

from infrahub_sync import (
    DiffSyncMixin,
    DiffSyncModelMixin,
    SchemaMappingModel,
    SyncAdapter,
    SyncConfig,
)
from infrahub_sync.cache.cursors import CursorState, CursorTier

from .utils import get_value

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from collections.abc import Iterator


class NetboxAdapter(DiffSyncMixin, Adapter):
    type = "Netbox"

    def __init__(self, target: str, adapter: SyncAdapter, config: SyncConfig, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)

        self.target = target
        self.client = self._create_netbox_client(adapter)
        self.config = config

    def _create_netbox_client(self, adapter: SyncAdapter) -> pynetbox.api:
        settings = adapter.settings or {}
        url = os.environ.get("NETBOX_ADDRESS") or os.environ.get("NETBOX_URL") or settings.get("url")
        token = os.environ.get("NETBOX_TOKEN") or settings.get("token")
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
        if self.config.source.name.title() == self.type.title():  # ty: ignore[unresolved-attribute]
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

            if self.config.source.name.title() == self.type.title():  # ty: ignore[unresolved-attribute]
                filtered = model.filter_records(records=raw_records, schema_mapping=element)
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

    def netbox_obj_to_diffsync(
        self, obj: dict[str, Any], mapping: SchemaMappingModel, model: type[NetboxModel]
    ) -> dict:
        obj_id = obj.get("id")
        data: dict[str, Any] = {"local_id": str(obj_id)}

        if not mapping.fields:
            return data
        for field in mapping.fields:  # pylint: disable=too-many-nested-blocks
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
                    if node := get_value(obj, field.mapping):
                        if isinstance(node, dict):
                            matching_nodes = []
                            node_id = node.get("id", None)
                            matching_nodes = [item for item in nodes if item.local_id == str(node_id)]  # ty: ignore[unresolved-attribute]
                            if len(matching_nodes) == 0:
                                msg = f"Unable to locate the node {field.name} {node_id}"
                                raise IndexError(msg)
                            node = matching_nodes[0]
                            data[field.name] = node.get_unique_id()
                        else:
                            data[field.name] = node
                else:
                    data[field.name] = []
                    for node in get_value(obj, field.mapping) or []:
                        if not node:
                            continue
                        node_id = node.get("id", None)
                        if not node_id and isinstance(node, tuple):
                            node_id = node[1] if node[0] == "id" else None
                            if not node_id:
                                continue
                        matching_nodes = [item for item in nodes if item.local_id == str(node_id)]  # ty: ignore[unresolved-attribute]
                        if len(matching_nodes) == 0:
                            msg = f"Unable to locate the node {field.reference} {node_id}"
                            raise IndexError(msg)
                        data[field.name].append(matching_nodes[0].get_unique_id())
                    data[field.name] = sorted(data[field.name])

        return data


class NetboxModel(DiffSyncModelMixin, DiffSyncModel):
    @classmethod
    def create(
        cls,
        adapter: Adapter,
        ids: dict[Any, Any],
        attrs: dict[Any, Any],
    ) -> Self | None:
        # TODO: To implement
        return super().create(adapter=adapter, ids=ids, attrs=attrs)

    def update(self, attrs: dict) -> Self | None:
        # TODO: To implement
        return super().update(attrs)
