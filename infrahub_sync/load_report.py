"""The load report of the shared loader step (spec 009 research R3, data-model.md "Load report").

The admission step of `DiffSyncMixin` records here each source record that it cannot build:
the record's kind, source record ID and identity, the reason, and the named fields of that
reason. No entry holds the text of an exception.

`transform_each` applies the transforms of one mapping entry record by record, so a transform
that fails for one record leaves that record out instead of failing the load.
`removed_by_filters` names the filter that removed each record a mapping entry's filters
removed, by its position in the entry's `filters` list.
"""

from __future__ import annotations

import dataclasses
import inspect
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, Literal

import pydantic

from infrahub_sync.load_errors import TransformError

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable

    from infrahub_sync import SchemaMappingModel

LoadReportReason = Literal[
    "wrong_value_type",
    "duplicate_source_identity",
    "required_peer_excluded_by_filter",
    "peer_not_found_in_source",
    "peer_not_loaded",
    "transform_failed",
]
"""Why the admission step recorded a source record in the load report (data-model.md)."""

LOAD_REPORT_VALUE_LIMIT = 256
"""The longest string a load report entry keeps of a refused value."""


class SourceRecordReference(pydantic.BaseModel):
    """Names a source record by kind, identity and source record ID (data-model.md)."""

    model_config = pydantic.ConfigDict(frozen=True)

    kind: str
    identity: dict[str, Any] | None = None
    source_id: str | None = None


class LoadReportEntry(pydantic.BaseModel):
    """One source record that the admission step recorded (data-model.md "Load report").

    `config_entry` names the entry of the mapping configuration the reason comes from: the
    failed transform (`transforms[1]`) of a `transform_failed` entry, or the filter that
    removed the peer (`filters[0]`) of a `required_peer_excluded_by_filter` entry.
    """

    model_config = pydantic.ConfigDict(frozen=True)

    kind: str
    source_id: str | None = None
    identity: dict[str, Any] | None = None
    reason: LoadReportReason
    field: str | None = None
    error_type: str | None = None
    value: Any = None
    peer: SourceRecordReference | None = None
    loaded: bool = False
    existing: SourceRecordReference | None = None
    config_entry: str | None = None


def limited_value(value: Any) -> Any:  # a refused value can be any JSON value
    """Return `value` with every string limited to `LOAD_REPORT_VALUE_LIMIT` characters."""
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return value[:LOAD_REPORT_VALUE_LIMIT]
    if isinstance(value, Mapping):
        return {str(key)[:LOAD_REPORT_VALUE_LIMIT]: limited_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [limited_value(item) for item in value]
    return str(value)[:LOAD_REPORT_VALUE_LIMIT]


@dataclasses.dataclass
class AdmissionState:
    """Per-adapter bookkeeping of the shared loader step.

    `filtered_ids` maps each kind to the source IDs its filters removed, each with the
    position of the first filter that removed it, or None when that is not known.
    """

    load_report: list[LoadReportEntry] = dataclasses.field(default_factory=list)
    peer_index: dict[tuple[str, str], str] = dataclasses.field(default_factory=dict)
    not_loaded: dict[tuple[str, str], LoadReportEntry] = dataclasses.field(default_factory=dict)
    filtered_ids: dict[str, dict[str, int | None]] = dataclasses.field(default_factory=dict)


def filter_entry(position: int | None) -> str | None:
    """The name of the filter at `position` of a mapping entry's `filters` list."""
    return None if position is None else f"filters[{position}]"


def transform_entry(position: int | None) -> str | None:
    """The name of the transform at `position` of a mapping entry's `transforms` list."""
    return None if position is None else f"transforms[{position}]"


def removed_by_filters(
    model: Any,  # a DiffSyncModelMixin subclass
    records: Iterable[Any],
    element: SchemaMappingModel,
    *,
    kept_ids: set[str],
    source_id: Callable[[Any], str | None],
) -> dict[str, int | None]:
    """Map each record the filters of `element` removed to the position of the first filter that removed it.

    `kept_ids` are the source IDs of the records the filters kept. The position is None when
    the model filters its records its own way. Only the configured filter and its position
    are named, never the record's value.
    """
    removed: dict[str, int | None] = {}
    filters = list(element.filters or ())
    apply_filters = getattr(model, "apply_filters", None)
    for record in records:
        identifier = source_id(record)
        if identifier is None or identifier in kept_ids:
            continue
        position = None
        if apply_filters is not None:
            position = next(
                (index for index, item in enumerate(filters) if not apply_filters(item=record, filters=[item])), None
            )
        removed.setdefault(identifier, position)
    return removed


def transform_failure_entry(error: TransformError) -> LoadReportEntry:
    """The load report entry of a record whose transform failed: kind, record, field and transform only."""
    return LoadReportEntry(
        kind=error.kind or "",
        source_id=error.source_id,
        reason="transform_failed",
        field=error.field,
        config_entry=transform_entry(error.position),
    )


def transform_records(
    model: Any,  # a DiffSyncModelMixin subclass, or a model with its own `transform_records`
    records: list[dict[str, Any]],
    element: SchemaMappingModel,
    on_failure: Callable[[TransformError], None],
) -> list[dict[str, Any]]:
    """Call the model's `transform_records`, passing `on_failure` when the model accepts it.

    A model that overrides `transform_records` without `on_failure` keeps its own contract: a
    failed transform raises.
    """
    if "on_failure" in inspect.signature(model.transform_records).parameters:
        return model.transform_records(records=records, schema_mapping=element, on_failure=on_failure)
    return model.transform_records(records=records, schema_mapping=element)


def transform_each(
    model: Any,  # a DiffSyncModelMixin subclass
    records: Iterable[dict[str, Any]],
    element: SchemaMappingModel,
    on_failure: Callable[[TransformError], None] | None,
) -> list[dict[str, Any]]:
    """Apply the transforms of `element` to each record, in order.

    A failed transform raises `TransformError` with the entry's kind and the transform's
    position. With `on_failure`, the error is passed to it and the record is left out of the
    result, so the other records are transformed; without it, the error is raised.
    """
    transforms = list(element.transforms or ())
    if not transforms:
        return list(records)
    transformed: list[dict[str, Any]] = []
    for record in records:
        try:
            for position, transform in enumerate(transforms):
                try:
                    model.apply_transform(item=record, transform_expr=transform.expression, field=transform.field)
                except TransformError as exc:
                    exc.kind = element.name
                    exc.position = position
                    raise
        except TransformError as exc:
            if on_failure is None:
                raise
            on_failure(exc)
            continue
        transformed.append(record)
    return transformed
