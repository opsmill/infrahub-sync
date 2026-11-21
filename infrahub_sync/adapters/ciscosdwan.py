"""Read-only Cisco Catalyst SD-WAN Manager source built on the shared REST mapping contract."""

from __future__ import annotations

import hashlib
import json
import re
from typing import TYPE_CHECKING, Any

import structlog

from infrahub_sync.adapters.ciscosdwan_client import CiscosdwanClient
from infrahub_sync.adapters.genericrestapi import GenericrestapiAdapter, GenericrestapiModel

if TYPE_CHECKING:
    from infrahub_sync import SyncAdapter, SyncConfig

logger = structlog.get_logger(__name__)

LOCAL_ID_KEY = "_sdwan_id"
LOCAL_ID_FIELDS = ("uuid", "deviceId", "recordId", "vdevice-dataKey")


class CiscosdwanAdapter(GenericrestapiAdapter):
    """Load configured `/dataservice/` GET resources from SD-WAN Manager, as a source only."""

    client: CiscosdwanClient

    def __init__(self, target: str, adapter: SyncAdapter, config: SyncConfig, *args: Any, **kwargs: Any) -> None:
        if target != "source":
            msg = "Cisco SD-WAN: this adapter can only be used as a source"
            raise ValueError(msg)
        super().__init__(target, adapter, config, "CiscoSdwan", *args, **kwargs)
        self._responses: dict[str, list[dict[str, Any]]] = {}

    def _create_rest_client(self, settings: dict) -> CiscosdwanClient:
        return CiscosdwanClient(settings)

    @staticmethod
    def _prepare(record: dict[str, Any], endpoint: str, index: int) -> dict[str, Any]:
        """Add a source identifier when the record has no `id`, and underscore aliases for hyphenated keys.

        Jinja2 transforms cannot name a variable such as `host-name`, so each such key is also
        available as `host_name`. The original keys are kept for `mapping` and `filters`.
        """
        prepared = dict(record)
        if prepared.get("id") is None:
            local_id = next((prepared[key] for key in LOCAL_ID_FIELDS if prepared.get(key)), None)
            if local_id is None:
                local_id = hashlib.sha256(json.dumps([endpoint, index]).encode()).hexdigest()
            prepared = {LOCAL_ID_KEY: str(local_id), **prepared}
        for key in list(prepared):
            alias = re.sub(r"\W", "_", key)
            if alias != key and alias not in prepared:
                prepared[alias] = prepared[key]
        return prepared

    def _records(self, endpoint: str) -> list[dict[str, Any]]:
        """Fetch an endpoint once per run; each mapping receives its own copies of the records."""
        if endpoint not in self._responses:
            self._responses[endpoint] = [
                self._prepare(record, endpoint, index) for index, record in enumerate(self.client.records(endpoint))
            ]
        return [dict(record) for record in self._responses[endpoint]]

    def model_loader(self, model_name: str, model: type[GenericrestapiModel]) -> None:
        """Fetch, filter and transform records, then add one model per distinct identifier."""
        element = next((item for item in self.config.schema_mapping if item.name == model_name), None)
        if element is None:
            msg = f"Cisco SD-WAN: {model_name}: no schema mapping is configured for this model"
            raise ValueError(msg)
        if not element.mapping:
            msg = f"Cisco SD-WAN: {model_name}: mapping must name a GET endpoint under /dataservice/"
            raise ValueError(msg)
        fetched = False
        try:
            records = self._records(element.mapping)
            fetched = True
        finally:
            # One session serves every mapping; log out after the last one, or as soon as a fetch fails.
            if not fetched or not self.top_level or model_name == self.top_level[-1]:
                self.client.close()
        filtered = model.filter_records(records=records, schema_mapping=element)
        transformed = model.transform_records(records=filtered, schema_mapping=element)
        duplicates = 0
        for obj in transformed:
            data = self.obj_to_diffsync(obj=obj, mapping=element, model=model)
            for field in element.fields:
                if field.static is not None:
                    data[field.name] = field.static
            missing = [key for key in model._identifiers if data.get(key) in (None, "")]
            if missing:
                msg = f"Cisco SD-WAN: {model_name}: a record has no value for identifier {', '.join(missing)}"
                raise ValueError(msg)
            item = model(**data)
            existing = self.get_or_none(model, item.get_unique_id())
            if existing is None:
                self.add(item)
                continue
            if existing.get_attrs() != item.get_attrs():
                msg = (
                    f"Cisco SD-WAN: {model_name}: several records share the identifier "
                    f"'{item.get_unique_id()}' with different values; add a filter or change the identifiers"
                )
                raise ValueError(msg)
            duplicates += 1
        logger.info(
            "Cisco SD-WAN models loaded",
            model=model_name,
            fetched=len(records),
            loaded=len(transformed) - duplicates,
            duplicates=duplicates,
        )


class CiscosdwanModel(GenericrestapiModel):
    """Source models share the REST field, filter and transform behavior."""
