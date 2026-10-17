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
from infrahub_sync.load_report import removed_by_filters, transform_records

from .references import owner_kind, reference_is_required, resolve_peer_list
from .utils import get_value

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator


def _record_ids(records: list[dict]) -> set[str]:
    """Return usable NetBox IDs without treating a missing ID as an excluded peer."""
    return {str(record["id"]) for record in records if record.get("id") is not None}


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

    def _transformed_records(
        self,
        *,
        element: SchemaMappingModel,
        model: type[NetboxModel],
        raw_records: list[dict],
        already_filtered: bool = False,
        record_failures: bool = False,
    ) -> list[dict]:
        """Filter and transform NetBox records as the base class's `model_loader` does.

        Pass `already_filtered=True` when the caller has run `filter_records` itself (e.g. to
        log a filtered count) so records aren't filtered twice. With `record_failures=True`, a
        record whose transform fails is recorded in the load report and left out, as in `model_loader`.
        """
        if self.target != "source":
            return raw_records
        filtered = (
            raw_records if already_filtered else model.filter_records(records=raw_records, schema_mapping=element)
        )
        if record_failures:
            return transform_records(model, filtered, element, self.record_transform_failure)
        return model.transform_records(records=filtered, schema_mapping=element)

    def _records_to_diffsync(
        self,
        *,
        element: SchemaMappingModel,
        model: type[NetboxModel],
        raw_records: list[dict],
        already_filtered: bool = False,
    ) -> Iterator[dict]:
        """Filter+transform NetBox records and yield diffsync-ready dicts."""
        for obj in self._transformed_records(
            element=element, model=model, raw_records=raw_records, already_filtered=already_filtered
        ):
            yield self.record_to_data(obj, element, model)

    def _changed_records(self, model_name: str, cursor: CursorState) -> tuple[SchemaMappingModel, list[dict]]:
        """Return the mapping entry and the transformed NetBox records changed since `cursor`."""
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
        return element, self._transformed_records(element=element, model=model, raw_records=raw, record_failures=True)

    def list_changed_since(self, model_name: str, cursor: CursorState) -> Iterator[dict]:
        """Yield NetBox records changed since `cursor`. Uses `last_updated__gte` filter."""
        element, records = self._changed_records(model_name, cursor)
        model: type[NetboxModel] = getattr(self, model_name)
        for obj in records:
            yield self.record_to_data(obj, element, model)

    def load_changed_since(
        self,
        model_name: str,
        model: type[DiffSyncModel],
        cursor: CursorState,
        *,
        store_item: Callable[[DiffSyncModel], DiffSyncModel],
    ) -> None:
        """Admit each NetBox record changed since `cursor` through `load_record`, as a full load does."""
        element, records = self._changed_records(model_name, cursor)
        for obj in records:
            self.load_record(model, element, obj, store_item=store_item)

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
        for payload in self._records_to_diffsync(element=element, model=model, raw_records=raw_records):
            yield model(**payload).get_unique_id()

    def list_records(self, model_name: str, element: SchemaMappingModel) -> list[dict[str, Any]]:  # noqa: ARG002
        """Return every record of the mapped endpoint as a dict, before any filter.

        `element.mapping` supports nested attribute paths (e.g. "plugins.foo.bar") for
        pynetbox plugin endpoints.
        """
        endpoint = self._resolve_endpoint(element.mapping or "")
        raw_records = [dict(node) for node in endpoint.all()]
        logger.info("%s: Read %d %s", self.type, len(raw_records), (element.mapping or "").split(".")[-1])
        return raw_records

    def excluded_peer_ids(self, peer_kind: str) -> set[str]:
        """Return the IDs of `peer_kind` a configured filter removed, reading its endpoint if needed.

        An owner can load before its peer kind, and an incremental load does not load the peer
        kind at all. The first lookup then reads the whole peer endpoint to tell filtered IDs
        apart from IDs the source does not return; a later model load can read it again.
        """
        known = self._admission.filtered_ids
        if peer_kind in known or self.target != "source":
            return set(known.get(peer_kind, {}))
        removed: dict[str, int | None] = {}
        retained_ids: set[str] = set()
        for element in self.config.schema_mapping:
            if element.name != peer_kind or not element.mapping or not element.filters:
                continue
            model: type[NetboxModel] = getattr(self, peer_kind)
            raw = [dict(node) for node in self._resolve_endpoint(element.mapping).all()]
            kept = _record_ids(model.filter_records(records=raw, schema_mapping=element))
            retained_ids.update(kept)
            for source_id, position in removed_by_filters(
                model, raw, element, kept_ids=kept, source_id=self.record_source_id
            ).items():
                removed.setdefault(source_id, position)
        known[peer_kind] = {
            source_id: position for source_id, position in removed.items() if source_id not in retained_ids
        }
        return set(known[peer_kind])

    def record_to_data(
        self, record: dict[str, Any], element: SchemaMappingModel, model: type[DiffSyncModel]
    ) -> dict[str, Any]:
        """Convert one NetBox record to model data; references resolve through `peer_unique_id`."""
        obj_id = record.get("id")
        data: dict[str, Any] = {"local_id": str(obj_id)}
        owner = (owner_kind(model, element.name), str(obj_id))

        if not element.fields:
            return data
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
                    node_id = str(node.get("id", None))
                    unique_id = self.peer_unique_id(
                        field.reference, node_id, owner=owner, field=field.name, required=required
                    )
                    if unique_id is None:
                        # No value also means a peer that was not loaded (kept without the link).
                        if node_id in self.excluded_peer_ids(field.reference):
                            self._log_excluded(element.name, obj_id, field.name, field.reference, [node_id])
                        continue
                    data[field.name] = unique_id
                else:
                    peer_ids = [node.get("id", None) for node in get_value(record, field.mapping) or [] if node]
                    data[field.name], excluded_ids = resolve_peer_list(
                        self,
                        peer_kind=field.reference,
                        peer_source_ids=peer_ids,
                        owner=owner,
                        field=field.name,
                        required=required,
                    )
                    if excluded_ids:
                        self._log_excluded(element.name, obj_id, field.name, field.reference, excluded_ids)

        return data

    @staticmethod
    def _log_excluded(kind: str, obj_id: object, field_name: str, peer_kind: str, peer_ids: list[str]) -> None:
        logger.warning(
            "NetBox filter excluded peers from %s record %s field %s (peer kind %s, IDs: %s)",
            kind,
            obj_id,
            field_name,
            peer_kind,
            ", ".join(peer_ids),
        )


class NetboxModel(DiffSyncModelMixin, DiffSyncModel):
    """DiffSync model for netbox records."""
