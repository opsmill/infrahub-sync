"""Configuration-driven Webex source using the shared REST mapping contract."""

from __future__ import annotations

import hashlib
import json
import re
from typing import TYPE_CHECKING, Any
from urllib.parse import quote

import structlog
from diffsync.enum import DiffSyncModelFlags

from infrahub_sync.adapters.genericrestapi import GenericrestapiAdapter, GenericrestapiModel
from infrahub_sync.adapters.utils import get_value
from infrahub_sync.adapters.webex_client import WebexClient, WebexHTTPError, WebexResource

if TYPE_CHECKING:
    from infrahub_sync import SchemaMappingModel, SyncAdapter, SyncConfig

logger = structlog.get_logger(__name__)


class _MissingParentValueError(ValueError):
    """A request cannot be expanded because a parent field is empty."""


class WebexAdapter(GenericrestapiAdapter):
    """Load any configured JSON GET resource from Webex, as a source only."""

    client: WebexClient

    def __init__(self, target: str, adapter: SyncAdapter, config: SyncConfig, *args: Any, **kwargs: Any) -> None:
        if target != "source":
            msg = "Webex: this adapter can only be used as a source"
            raise ValueError(msg)
        super().__init__(target, adapter, config, "Webex", *args, **kwargs)
        self._records: dict[str, list[dict[str, Any]]] = {}
        names = {item.name for item in config.schema_mapping}
        if self.client.options.resources.keys() - names:
            msg = "Webex: resource options must name a configured schema mapping"
            raise ValueError(msg)

    def _create_rest_client(self, settings: dict) -> WebexClient:
        return WebexClient(settings)

    @staticmethod
    def _field(record: dict[str, Any], name: str) -> Any:
        value = get_value(record, name)
        if value is None or (isinstance(value, str) and not value.strip()):
            raise _MissingParentValueError
        return value

    @classmethod
    def _path(cls, template: str, record: dict[str, Any]) -> str:
        return re.sub(r"\{([^{}]+)\}", lambda match: quote(str(cls._field(record, match[1])), safe=""), template)

    def _params(self, options: WebexResource, parent: dict[str, Any]) -> dict[str, str]:
        values = dict(self.client.options.params)
        if self.client.options.org_id is not None:
            values["orgId"] = self.client.options.org_id
        values.update(options.params)
        values.setdefault("max", options.page_size or self.client.options.page_size)
        if options.page_size is not None:
            values["max"] = options.page_size
        result: dict[str, str] = {}
        for key, raw_value in values.items():
            value = raw_value
            if isinstance(value, dict):
                if set(value) != {"parent"} or not isinstance(value["parent"], str):
                    msg = "Webex: query references must use env or parent with a field name"
                    raise ValueError(msg)
                value = self._field(parent, value["parent"])
            if value is None:
                continue
            result[key] = (
                ",".join(self._query_scalar(item) for item in value)
                if isinstance(value, list)
                else self._query_scalar(value)
            )
        return result

    @staticmethod
    def _query_scalar(value: Any) -> str:
        if isinstance(value, bool):
            return str(value).lower()
        if not isinstance(value, (str, int, float)):
            msg = "Webex: query values must be scalar values, lists or explicit references"
            raise TypeError(msg)
        return str(value)

    @staticmethod
    def _extract(payload: Any, response_key: str | None, name: str) -> list[dict[str, Any]]:
        if response_key is not None:
            if not isinstance(payload, dict) or get_value(payload, response_key) is None:
                msg = f"Webex: {name}: missing configured response key"
                raise ValueError(msg)
            payload = get_value(payload, response_key)
        if isinstance(payload, dict):
            return [payload]
        if not isinstance(payload, list) or any(not isinstance(item, dict) for item in payload):
            msg = f"Webex: {name}: response must contain JSON records"
            raise ValueError(msg)
        return payload

    def _fetch(self, element: SchemaMappingModel, options: WebexResource) -> list[dict[str, Any]]:
        if not element.mapping:
            msg = f"Webex: {element.name}: mapping must name a GET endpoint"
            raise ValueError(msg)
        if options.parent and options.parent not in self._records:
            msg = f"Webex: {element.name}: parent mapping must be loaded first; set explicit order"
            raise ValueError(msg)
        parents = self._records[options.parent] if options.parent else [{}]
        records: list[dict[str, Any]] = []
        for parent in parents:
            try:
                path = self._path(element.mapping, parent)
                params = self._params(options, parent)
            except _MissingParentValueError:
                if not options.parent:
                    msg = f"Webex: {element.name}: parent field reference requires a parent mapping"
                    raise ValueError(msg) from None
                logger.warning("Webex skipped parent with an empty request field", model=element.name)
                continue
            try:
                pages = self.client.pages(path, params)
            except WebexHTTPError as exc:
                if options.parent and exc.status == 404:
                    logger.info("Webex parent resource absent", model=element.name)
                    continue
                raise
            for page in pages:
                page_records = self._extract(page, options.response_key, element.name)
                for index, record in enumerate(page_records, start=len(records)):
                    if record.get("id") is None and not any(key.endswith("_id") for key in record):
                        # Singleton settings endpoints need not expose their own ID.
                        identity = json.dumps([path, params, index], sort_keys=True)
                        record["_webex_id"] = hashlib.sha256(identity.encode()).hexdigest()
                self._load_details(page_records, options, params, element.name)
                records.extend(page_records)
        return records

    def _load_details(
        self, records: list[dict[str, Any]], options: WebexResource, params: dict[str, str], name: str
    ) -> None:
        if options.details:
            detail_params = {key: value for key, value in params.items() if key != "max"}
            for record in records:
                try:
                    path = self._path(options.details, record)
                except _MissingParentValueError:
                    msg = f"Webex: {name}: detail endpoint requires a missing record field"
                    raise ValueError(msg) from None
                details = self.client.get(path, params=detail_params)
                if not isinstance(details, dict):
                    msg = f"Webex: {name}: detail response must be a JSON object"
                    raise TypeError(msg)
                record.update(details)

    def model_loader(self, model_name: str, model: type[GenericrestapiModel]) -> None:
        """Fetch, filter and transform records, then use the shared field/peer mapping."""
        element = next(item for item in self.config.schema_mapping if item.name == model_name)
        options = self.client.options.resources.get(model_name, WebexResource())
        records = self._fetch(element, options)
        filtered = model.filter_records(records=records, schema_mapping=element)
        transformed = model.transform_records(records=filtered, schema_mapping=element)
        self._records[model_name] = transformed
        logger.info("Webex models loaded", model=model_name, count=len(transformed), fetched=len(records))
        for obj in transformed:
            data = self.obj_to_diffsync(obj=obj, mapping=element, model=model)
            for field in element.fields:
                if field.static is not None:
                    data[field.name] = field.static
            if any(
                data.get(key) is None or (isinstance(data[key], str) and not data[key].strip())
                for key in model._identifiers
            ):
                msg = f"Webex: {model_name}: mapped identifiers must have nonempty values"
                raise ValueError(msg)
            item = model(**data)
            if options.read_only:
                item.model_flags |= DiffSyncModelFlags.IGNORE
            self.add(item)


class WebexModel(GenericrestapiModel):
    """Source models share the existing REST field, filter and transform behavior."""
