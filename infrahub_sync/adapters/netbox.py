from __future__ import annotations

# pylint: disable=R0801
import logging
from typing import TYPE_CHECKING, Any

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

from .references import peer_unique_id, peer_unique_ids, reference_is_required
from .utils import get_value, keep_certificate_checks_off

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
        client.http_session = keep_certificate_checks_off(session)
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

    def _convert_records(
        self, *, element: SchemaMappingModel, model: type[NetboxModel], raw_records: list[dict]
    ) -> Iterator[dict]:
        """Filter and transform NetBox records and yield model data, for the incremental reads.

        A full read goes through the base-class read loop instead (`model_loader`).
        """
        if self.target == "source":
            filtered = model.filter_records(records=raw_records, schema_mapping=element)
            transformed = model.transform_records(records=filtered, schema_mapping=element)
        else:
            transformed = raw_records
        for obj in transformed:
            data = self.record_to_data(obj, element, model)
            if data is not None:
                yield data

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
        yield from self._convert_records(element=element, model=model, raw_records=raw)

    def list_existing_ids(self, model_name: str) -> Iterator[str]:
        """Yield current unique IDs for `model_name` from NetBox.

        The unique ID is computed by the existing diffsync model:
        `model(**record_to_data(...)).get_unique_id()`.
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
        # This sweep only computes IDs: the load report entries its conversion writes describe
        # no record the read admits, so they are dropped.
        entries = self.load_report.entries
        start = len(entries)
        try:
            for payload in self._convert_records(element=element, model=model, raw_records=raw_records):
                yield model(**payload).get_unique_id()
        finally:
            del entries[start:]

    def list_records(self, model_name: str, element: SchemaMappingModel) -> list[dict]:  # noqa: ARG002
        """Return every NetBox record of the mapping entry's endpoint, as dicts.

        `mapping` supports nested attribute paths (e.g. "plugins.foo.bar") for pynetbox plugin
        endpoints.
        """
        if not element.mapping:
            return []
        return [dict(node) for node in self._resolve_endpoint(element.mapping).all()]

    def filtered_peer_position(self, peer_kind: str, peer_source_id: str) -> tuple[bool, int | None]:
        """Tell whether a configured filter removed a peer, reading the peer kind first when needed.

        An owner can be read before its peer kind, and an incremental read reads no peer kind
        at all. The first lookup then reads the whole peer endpoint to tell filtered IDs from
        absent IDs; a later read of that kind can read the endpoint again.
        """
        self._read_filtered_peers(peer_kind)
        return super().filtered_peer_position(peer_kind, peer_source_id)

    def _read_filtered_peers(self, peer_kind: str) -> None:
        state = self._read_state
        complete: set[str] = self.__dict__.setdefault("_complete_filtered_peers", set())
        if self.target != "source" or peer_kind in state.read_kinds or peer_kind in complete:
            return
        complete.add(peer_kind)
        elements = [element for element in self.config.schema_mapping if element.name == peer_kind and element.mapping]
        if not any(element.filters for element in elements):
            return
        # Every entry is read, so a source ID that an unfiltered entry keeps is not taken as filtered.
        for element in elements:
            model: type[NetboxModel] = getattr(self, peer_kind)
            self.filter_source_records(model, element, self.list_records(peer_kind, element))

    def record_to_data(self, record: dict[str, Any], element: SchemaMappingModel, model: type[NetboxModel]) -> dict:
        """Convert one NetBox record to model data; references resolve through `peer_unique_id`."""
        obj_id = record.get("id")
        source_id = str(obj_id)
        data: dict[str, Any] = {"local_id": source_id}
        owner = (element.name, source_id)

        for field in element.fields:
            field_is_list = model.is_list(name=field.name)

            if field.static is not None:
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
                    node = get_value(record, field.mapping)
                    if not node:
                        continue
                    if not isinstance(node, dict):
                        data[field.name] = node
                        continue
                    unique_id = peer_unique_id(
                        self, field.reference, node.get("id"), owner=owner, field=field.name, required=required
                    )
                    if unique_id is not None:
                        data[field.name] = unique_id
                else:
                    peer_ids = [node.get("id") for node in get_value(record, field.mapping) or [] if node]
                    data[field.name] = peer_unique_ids(
                        self, field.reference, peer_ids, owner=owner, field=field.name, required=required
                    )

        return data


class NetboxModel(DiffSyncModelMixin, DiffSyncModel):
    """DiffSync model for netbox records."""
