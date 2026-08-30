from __future__ import annotations

# pylint: disable=R0801
import logging
from typing import TYPE_CHECKING, Any

import pynautobot  # ty: ignore[unresolved-import]  # optional dep, see pyproject extras
import pynautobot.core.query  # ty: ignore[unresolved-import]  # optional dep, see pyproject extras
from diffsync import Adapter, DiffSyncModel
from pydantic import ValidationError
from typing_extensions import Self

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


def _is_unknown_filter_error(exc: pynautobot.core.query.RequestError, field: str) -> bool:
    """True if `exc` is a 400 rejecting `field` as an unknown filter.

    Prefers the response JSON, where Nautobot reports the offending filter
    as a top-level key (e.g. ``{"last_updated__gte": ["Unknown filter field"]}``),
    so the predicate survives wording tweaks in the error string. Only when the
    body isn't JSON do we fall back to a substring match, and even then we
    require both the field name *and* an unknown-filter phrase so an unrelated
    400 that merely happens to mention the field can't trigger a false positive.
    """
    req = getattr(exc, "req", None)
    if req is None or getattr(req, "status_code", None) != 400:
        return False
    try:
        payload = req.json()
    except (ValueError, AttributeError):
        payload = None
    if isinstance(payload, dict):
        # Authoritative signal: the rejected filter appears as a key in the body.
        return field in payload
    # No JSON body — require the field name and filter-rejection wording.
    text = str(exc)
    return field in text and "filter" in text.lower()


class NautobotAdapter(DiffSyncMixin, Adapter):
    type = "Nautobot"

    def __init__(self, target: str, adapter: SyncAdapter, config: SyncConfig, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)

        self.target = target
        self.client = self._create_nautobot_client(adapter)
        self.config = config

    def _create_nautobot_client(self, adapter: SyncAdapter) -> pynautobot.api:
        settings = adapter.settings or {}
        url = select_runtime_credential(settings, "url", ("NAUTOBOT_ADDRESS", "NAUTOBOT_URL"))
        token = select_runtime_credential(settings, "token", ("NAUTOBOT_TOKEN",))
        verify_ssl = settings.get("verify_ssl", True)

        if not url or not token:
            msg = "Both url and token must be specified!"
            raise ValueError(msg)

        client = pynautobot.api(url=url, token=token, threading=True, max_workers=5, retries=3, verify=verify_ssl)
        return client

    def cursor_tier_for(self, model_name: str) -> CursorTier:
        """Return TIMESTAMP for any kind we have a schema_mapping for.

        Most pynautobot endpoints accept ``last_updated__gte`` but a few
        (e.g. dcim.front-ports / rear-ports) return 400 "Unknown filter
        field" — ``list_changed_since`` falls back to a full extract for
        those. Kinds not in the schema_mapping return NONE so the engine
        never attempts an incremental query for them.
        """
        for element in self.config.schema_mapping:
            if element.name == model_name and element.mapping:
                return CursorTier.TIMESTAMP
        return CursorTier.NONE

    def _resolve_endpoint(self, mapping: str) -> Any:
        """Walk `mapping` (e.g. 'dcim.devices' or 'plugins.foo.bar') to a pynautobot endpoint."""
        parts = mapping.split(".")
        endpoint = self.client
        for part in parts:
            try:
                endpoint = getattr(endpoint, part)
            except AttributeError as exc:
                msg = f"Invalid Nautobot mapping path {mapping!r} (missing segment {part!r})"
                raise ValueError(msg) from exc
        return endpoint

    def _records_to_diffsync(
        self,
        *,
        element: SchemaMappingModel,
        model: type[NautobotModel],
        raw_records: list[dict],
        already_filtered: bool = False,
    ) -> Iterator[dict]:
        """Filter+transform Nautobot records and yield diffsync-ready dicts.

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
            yield self.nautobot_obj_to_diffsync(obj=obj, mapping=element, model=model)

    def list_changed_since(self, model_name: str, cursor: CursorState) -> Iterator[dict]:
        """Yield Nautobot records changed since `cursor`. Uses `last_updated__gte` filter."""
        element = next(
            (e for e in self.config.schema_mapping if e.name == model_name),
            None,
        )
        if element is None or not element.mapping:
            msg = f"Nautobot: no schema_mapping entry with mapping for {model_name!r}"
            raise NotImplementedError(msg)

        model: type[NautobotModel] = getattr(self, model_name)
        endpoint = self._resolve_endpoint(element.mapping)
        try:
            raw = [dict(node) for node in endpoint.filter(last_updated__gte=cursor.value)]
        except pynautobot.core.query.RequestError as exc:
            # Not every Nautobot endpoint exposes `last_updated__gte` (e.g.
            # dcim.front-ports / dcim.rear-ports return 400 with a body like
            # `{"last_updated__gte": ["Unknown filter field"]}`). Fall back
            # to a full extract for any 400 that mentions the filter key.
            if not _is_unknown_filter_error(exc, "last_updated__gte"):
                raise
            logger.warning(
                "Nautobot %s (%s) does not support last_updated__gte; falling back to full extract for this kind.",
                model_name,
                element.mapping,
            )
            raw = [dict(node) for node in endpoint.all()]
        yield from self._records_to_diffsync(element=element, model=model, raw_records=raw)

    def list_existing_ids(self, model_name: str) -> Iterator[str]:
        """Yield current unique IDs for `model_name` from Nautobot.

        Used by soft-delete sweeps: timestamp-filtered queries miss DELETEs.
        """
        element = next(
            (e for e in self.config.schema_mapping if e.name == model_name),
            None,
        )
        if element is None or not element.mapping:
            msg = f"Nautobot: no schema_mapping entry with mapping for {model_name!r}"
            raise NotImplementedError(msg)

        model: type[NautobotModel] = getattr(self, model_name)
        endpoint = self._resolve_endpoint(element.mapping)
        raw_records = [dict(node) for node in endpoint.all()]
        for payload in self._records_to_diffsync(element=element, model=model, raw_records=raw_records):
            yield model(**payload).get_unique_id()

    def model_loader(self, model_name: str, model: type[NautobotModel]) -> None:
        """
        Load and process models using schema mapping filters and transformations.

        This method retrieves data from Nautobot, applies filters and transformations
        as specified in the schema mapping, and loads the processed data into the adapter.
        """
        for element in self.config.schema_mapping:
            if element.name != model_name:
                continue

            if not element.mapping:
                logger.info("No mapping defined for '%s', skipping", element.name)
                continue

            endpoint = self._resolve_endpoint(element.mapping)
            raw_records = [dict(node) for node in endpoint.all()]
            total = len(raw_records)
            resource_name = element.mapping.split(".")[-1]
            if self.config.source.name.title() == self.type.title():  # ty: ignore[unresolved-attribute]
                filtered = model.filter_records(records=raw_records, schema_mapping=element)
                # Mirror the NetBox adapter's filtered/total log so operators see
                # the same detail regardless of source system.
                logger.info("%s: Loading %d/%d %s", self.type, len(filtered), total, resource_name)
            else:
                filtered = raw_records
                logger.info("%s: Loading all %d %s", self.type, total, resource_name)

            continue_on_error = getattr(self, "continue_on_error", False)
            # Records are already filtered above; don't filter again.
            for data in self._records_to_diffsync(
                element=element, model=model, raw_records=filtered, already_filtered=True
            ):
                try:
                    item = model(**data)
                except ValidationError as exc:
                    if not continue_on_error:
                        raise
                    logger.warning(
                        "Skipping %s[%s]: cannot build DiffSync model "
                        "(likely a required peer was skipped earlier). Pydantic errors: %s",
                        model_name,
                        data.get("local_id"),
                        exc.errors(include_url=False),
                    )
                    continue
                self.add(item)

    def nautobot_obj_to_diffsync(
        self, obj: dict[str, Any], mapping: SchemaMappingModel, model: type[NautobotModel]
    ) -> dict:
        obj_id = obj.get("id")
        data: dict[str, Any] = {"local_id": str(obj_id)}

        for field in mapping.fields:  # pylint: disable=too-many-nested-blocks
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
                    if node := get_value(obj, field.mapping):
                        matching_nodes = []
                        node_id = node.get("id", None)
                        if node_id:
                            matching_nodes = [item for item in nodes if item.local_id == str(node_id)]  # ty: ignore[unresolved-attribute]
                            if len(matching_nodes) == 0:
                                # TODO: If the peer is a Node we are filtering, we could end up not finding it
                                logger.warning("Unable to locate the node %s %s", field.name, node_id)
                                continue
                            node = matching_nodes[0]
                            data[field.name] = node.get_unique_id()

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
                            # TODO: If the peer is a Node we are filtering, we could end up not finding it
                            logger.warning("Unable to locate the node %s %s", field.name, node_id)
                            continue
                        data[field.name].append(matching_nodes[0].get_unique_id())
                    data[field.name] = sorted(data[field.name])

        return data


class NautobotModel(DiffSyncModelMixin, DiffSyncModel):
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
        return super().update(attrs=attrs)
