"""The load report of one adapter (spec 020 research R2).

The admission step of `DiffSyncMixin` records here each source record that it cannot build:
the record, the reason, and the named fields of that reason. No entry holds the text of an
exception. The plan step reads the report of the source adapter to build the plan problems and
the left-out records (data-model.md).
"""

from __future__ import annotations

import dataclasses
from typing import Any, Literal, TypeAlias, get_args

import pydantic

from infrahub_sync.plan.models import SourceRecordReference, limited_report_value

LoadReportReason: TypeAlias = Literal[
    "wrong_value_type",
    "duplicate_source_identity",
    "required_peer_excluded_by_filter",
    "peer_not_found_in_source",
    "peer_not_loaded",
    "transform_failed",
]
"""Why the admission step recorded a source record (research R2).

`peer_not_loaded` names a record whose required peer was itself not loaded; the plan step
records it as a dependent left-out record, not as a problem.
"""

LOAD_REPORT_REASONS: tuple[LoadReportReason, ...] = get_args(LoadReportReason)


_NO_SOURCE_ID_VALUES = frozenset({"", "None"})


def source_record_id(value: object) -> str | None:
    """Return a source record ID as text, or `None` when the record has none.

    Some adapters write a placeholder when a record has no ID: ACI writes `""` and Slurp'it
    `str(None)`, which is `"None"`. Every record with such a value would otherwise share one
    source ID, so the read and the problem pass would take one record for another.
    """
    if value is None:
        return None
    text = str(value)
    return None if text.strip() in _NO_SOURCE_ID_VALUES else text


def filter_entry(position: int | None) -> str | None:
    """The name of the filter at `position` of a mapping entry's `filters` list."""
    return None if position is None else f"filters[{position}]"


def transform_entry(position: int | None) -> str | None:
    """The name of the transform at `position` of a mapping entry's `transforms` list."""
    return None if position is None else f"transforms[{position}]"


class LoadReportEntry(pydantic.BaseModel):
    """One source record that the admission step recorded.

    `config_entry` names the configuration entry the reason comes from, by position only: the
    failed transform (`transforms[0]`) of a `transform_failed` entry, or the filter that
    removed the peer (`filters[0]`) of a `required_peer_excluded_by_filter` entry. `existing`
    is the record the store kept for a `duplicate_source_identity` entry. `loaded` is false
    when the record could not be built. `admission` numbers the admission step that recorded
    the entry: the entries of one number describe one source record, which tells apart the
    records that have no source ID.
    """

    model_config = pydantic.ConfigDict(extra="forbid", frozen=True)

    record: SourceRecordReference
    reason: LoadReportReason
    field: str | None = None
    value: Any = None
    peer: SourceRecordReference | None = None
    existing: SourceRecordReference | None = None
    config_entry: str | None = None
    loaded: bool = False
    admission: int | None = None

    @pydantic.field_validator("value", mode="before")
    @classmethod
    def _limit_value(cls, value: Any) -> Any:
        return limited_report_value(value)


@dataclasses.dataclass
class LoadReport:
    """The load report of one adapter: its entries, and the records its filters removed.

    `filtered_ids` maps each kind to the source IDs its filters removed, each with the position
    of the first filter that removed it, or `None` when that is not known.
    """

    entries: list[LoadReportEntry] = dataclasses.field(default_factory=list)
    filtered_ids: dict[str, dict[str, int | None]] = dataclasses.field(default_factory=dict)

    def add(self, entry: LoadReportEntry) -> None:
        """Record one entry."""
        self.entries.append(entry)

    def entries_for(self, kind: str) -> list[LoadReportEntry]:
        """The entries of the records of `kind`, in the order they were recorded."""
        return [entry for entry in self.entries if entry.record.kind == kind]

    def filter_position(self, kind: str, source_id: str) -> int | None:
        """The position of the filter that removed the record `source_id` of `kind`, if known."""
        return self.filtered_ids.get(kind, {}).get(source_id)

    def was_filtered(self, kind: str, source_id: str) -> bool:
        """Whether a configured filter removed the record `source_id` of `kind`."""
        return source_id in self.filtered_ids.get(kind, {})
