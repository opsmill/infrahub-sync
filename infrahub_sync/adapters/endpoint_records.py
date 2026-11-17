"""Record reads the NetBox and Nautobot adapters share.

Both adapters read records from a client whose endpoints are attributes (`client.dcim.devices`)
and convert them the same way. The functions here hold the parts that are the same; each
adapter keeps its own requests, its own `record_to_data` and its own incremental overrides.
This module is not an adapter and defines no class.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Iterator

    from infrahub_sync import SchemaMappingModel, SyncConfig


def resolve_endpoint(client: Any, mapping: str, product: str) -> Any:
    """Walk `mapping` (e.g. 'dcim.devices' or 'plugins.foo.bar') from `client` to an endpoint.

    Raises:
        ValueError: a segment of `mapping` is not an attribute; the message names `product`.
    """
    endpoint = client
    for part in mapping.split("."):
        try:
            endpoint = getattr(endpoint, part)
        except AttributeError as exc:
            msg = f"Invalid {product} mapping path {mapping!r} (missing segment {part!r})"
            raise ValueError(msg) from exc
    return endpoint


def mapped_element(config: SyncConfig, model_name: str, product: str) -> tuple[SchemaMappingModel, str]:
    """Return the first schema mapping entry named `model_name` and its mapping, which it must have.

    Raises:
        NotImplementedError: no entry has that name, or the first one has no mapping.
    """
    element = next(
        (e for e in config.schema_mapping if e.name == model_name),
        None,
    )
    if element is None or not element.mapping:
        msg = f"{product}: no schema_mapping entry with mapping for {model_name!r}"
        raise NotImplementedError(msg)
    return element, element.mapping


def convert_records(
    adapter: Any, *, element: SchemaMappingModel, model: Any, raw_records: list[dict]
) -> Iterator[dict]:
    """Filter and transform raw records and yield model data, for the incremental reads.

    A full read goes through the base-class read loop instead (`model_loader`).
    """
    if adapter.target == "source":
        filtered = model.filter_records(records=raw_records, schema_mapping=element)
        transformed = model.transform_records(records=filtered, schema_mapping=element)
    else:
        transformed = raw_records
    for obj in transformed:
        data = adapter.record_to_data(obj, element, model)
        if data is not None:
            yield data


def unique_ids(payloads: Iterator[dict], *, adapter: Any, model: Any) -> Iterator[str]:
    """Yield the unique ID of each converted record, then drop the load report entries added.

    This sweep only computes IDs: the load report entries its conversion writes describe
    no record the read admits, so they are dropped.
    """
    entries = adapter.load_report.entries
    start = len(entries)
    try:
        for payload in payloads:
            yield model(**payload).get_unique_id()
    finally:
        del entries[start:]
