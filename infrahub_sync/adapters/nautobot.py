from __future__ import annotations

# pylint: disable=R0801
import logging
from typing import TYPE_CHECKING, Any

import pynautobot  # ty: ignore[unresolved-import]  # optional dep, absent on the Python 3.10 profile
import pynautobot.core.query  # ty: ignore[unresolved-import]  # optional dep, absent on the Python 3.10 profile
from diffsync import Adapter, DiffSyncModel

from infrahub_sync import (
    DiffSyncMixin,
    DiffSyncModelMixin,
    SchemaMappingModel,
    SyncAdapter,
    SyncConfig,
)
from infrahub_sync.cache.cursors import CursorState, CursorTier
from infrahub_sync.configuration.credentials import select_runtime_credential

from .references import owner_kind, reference_is_required, resolve_peer_list
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


# Nautobot documents `depth` on list endpoints as an integer between 0 and 10.
# 2.4.41 does not enforce the upper bound itself (`core/api/views.py` parses
# `int(...)` and falls back to 0 on a non-integer), so the adapter refuses
# out-of-range values rather than passing or trimming them.
MAX_DEPTH = 10


def _validated_depth(value: Any) -> int | None:
    """Return the `depth` to send, or None when the operator selected none.

    `None` covers both a missing key and an explicit YAML null; both mean "send
    no depth parameter". Anything else must be an integer in the documented
    range — refused here, never clamped or coerced.
    """
    if value is None:
        return None
    # `bool` and `IntEnum` members pass `isinstance(..., int)`, so identity on
    # the type is what keeps a non-integer out.
    if type(value) is not int or not 0 <= value <= MAX_DEPTH:
        msg = f"Invalid Nautobot setting 'depth': {value!r}. It must be an integer between 0 and {MAX_DEPTH}."
        raise ValueError(msg)
    return value


class NautobotAdapter(DiffSyncMixin, Adapter):
    type = "Nautobot"

    def __init__(self, target: str, adapter: SyncAdapter, config: SyncConfig, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)

        # Invariant: `depth` is validated before the client is constructed, so an
        # invalid value is refused ahead of any request.
        settings = adapter.settings or {}
        depth = _validated_depth(settings.get("depth"))
        # `{}` when absent, so every existing request stays byte-identical.
        self._depth_kwargs: dict[str, int] = {} if depth is None else {"depth": depth}

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

        Same transformation flow as the base class's model_loader, for reuse by
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
            yield self.record_to_data(obj, element, model)

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
            raw = [dict(node) for node in endpoint.filter(last_updated__gte=cursor.value, **self._depth_kwargs)]
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
            raw = [dict(node) for node in endpoint.all(**self._depth_kwargs)]
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
        raw_records = [dict(node) for node in endpoint.all(**self._depth_kwargs)]
        for payload in self._records_to_diffsync(element=element, model=model, raw_records=raw_records):
            yield model(**payload).get_unique_id()

    def list_records(self, model_name: str, element: SchemaMappingModel) -> list[dict[str, Any]]:  # noqa: ARG002
        """Return every record of the mapped endpoint as a dict, before any filter."""
        endpoint = self._resolve_endpoint(element.mapping or "")
        raw_records = [dict(node) for node in endpoint.all(**self._depth_kwargs)]
        logger.info("%s: Read %d %s", self.type, len(raw_records), (element.mapping or "").split(".")[-1])
        return raw_records

    def record_to_data(
        self, record: dict[str, Any], element: SchemaMappingModel, model: type[DiffSyncModel]
    ) -> dict[str, Any]:
        """Convert one Nautobot record to model data; references resolve through `peer_unique_id`."""
        obj_id = record.get("id")
        data: dict[str, Any] = {"local_id": str(obj_id)}
        owner = (owner_kind(model, element.name), str(obj_id))

        for field in element.fields:
            field_is_list = model.is_list(name=field.name)

            if field.static is not None:
                data[field.name] = field.static
            elif not field_is_list and field.mapping and not field.reference:
                value = get_value(record, field.mapping)
                if value is not None:
                    data[field.name] = value
            elif field_is_list and field.mapping and not field.reference:
                msg = "it's not supported yet to have an attribute of type list with a simple mapping"
                raise NotImplementedError(msg)

            elif field.mapping and field.reference:
                required = reference_is_required(model, field.name)
                if not field_is_list:
                    node = get_value(record, field.mapping)
                    if not node or not (node_id := node.get("id", None)):
                        continue
                    unique_id = self.peer_unique_id(
                        field.reference, str(node_id), owner=owner, field=field.name, required=required
                    )
                    if unique_id is not None:
                        data[field.name] = unique_id
                else:
                    peer_ids = [node.get("id", None) for node in get_value(record, field.mapping) or [] if node]
                    data[field.name], _ = resolve_peer_list(
                        self,
                        peer_kind=field.reference,
                        peer_source_ids=peer_ids,
                        owner=owner,
                        field=field.name,
                        required=required,
                    )

        return data


class NautobotModel(DiffSyncModelMixin, DiffSyncModel):
    """DiffSync model for nautobot records."""
