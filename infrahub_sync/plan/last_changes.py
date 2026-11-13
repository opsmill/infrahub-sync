"""Who last changed each value a plan proposes to change.

The file format is described in `docs/docs/development/knowledge/plan-artifact.md`, section
"The last-change file".

The Infrahub adapter keeps, outside its DiffSync models, a map from (destination node id,
field name) to the `updated_by` and `updated_at` it read with the destination. While a plan is
derived, `LastChangeCollector` turns that map into one `LastChangeRecord` for each field each
update proposes to change; the writer stores them in `plan/last_changes.jsonl`. A create has
no record. The records are display data: apply does not read them.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from infrahub_sync.plan.models import LastChangeRecord

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

    from infrahub_sync.plan.models import LastChangeFieldType, PlannedOperation, RecordedAccountRecord


@dataclass(frozen=True, slots=True)
class LastChange:
    """Who last changed a destination value and when, as Infrahub recorded it.

    `None` means Infrahub returned no value for that field; the plan shows it as not recorded.
    """

    updated_by: RecordedAccountRecord | None
    updated_at: str | None


NOT_RECORDED = LastChange(updated_by=None, updated_at=None)


class LastChangeCollector:
    """Collects one `LastChangeRecord` for each field each update of a plan proposes to change.

    `changes` is the destination's map from (destination node id, field name) to what it read
    about that value's last change. A field the map holds nothing for gets a record with
    `None` values, which review shows as "not recorded". `metadata_read` is `False` when the
    destination did not ask for that metadata: Infrahub is older than 1.7.0, its version
    could not be parsed, or Infrahub refused the version query.
    """

    def __init__(self, changes: Mapping[tuple[str, str], LastChange], *, metadata_read: bool = False) -> None:
        self.changes = changes
        self.metadata_read = metadata_read
        self.records: list[LastChangeRecord] = []

    def add(self, operation: PlannedOperation, *, fields: Iterable[tuple[str, LastChangeFieldType]]) -> None:
        """Record the last change of each `(field, field_type)` of one update.

        Looked up by the update's recorded destination id; an operation without one (a
        create) gets no record.
        """
        if operation.destination_id is None:
            return
        for field, field_type in fields:
            change = self.changes.get((operation.destination_id, field), NOT_RECORDED)
            self.records.append(
                LastChangeRecord(
                    operation_id=operation.operation_id,
                    field=field,
                    field_type=field_type,
                    updated_by=change.updated_by,
                    updated_at=change.updated_at,
                )
            )


def changed_fields(
    element: Any,
    *,
    payload: Mapping[str, Any],
    references: Mapping[str, Any],
) -> list[tuple[str, LastChangeFieldType]]:
    """The fields an update proposes to change, each with its type, sorted by name.

    A field counts when the comparison found its values different (`get_attrs_diffs()`'s
    `"+"` side) and the operation carries it: in its payload (an attribute) or in its
    resolved references (a relationship). A relationship clear the plan cannot record is not
    carried, so it gets no record.
    """
    diffs = element.get_attrs_diffs() if hasattr(element, "get_attrs_diffs") else {}
    changed = (diffs or {}).get("+") or {}
    fields: list[tuple[str, LastChangeFieldType]] = []
    for name in sorted(changed):
        if name in references:
            fields.append((name, "relationship"))
        elif name in payload:
            fields.append((name, "attribute"))
    return fields


def selected_last_changes(
    records: Iterable[LastChangeRecord], operations: Iterable[PlannedOperation]
) -> list[LastChangeRecord]:
    """The records of `records` that still describe a field one of `operations` changes.

    The selection step of spec 020 removes the operations of left-out records and the
    relationships written without their peer. A record whose update was removed, or whose
    field the update no longer carries, describes no change of the plan, so it is not written.
    """
    fields: dict[str, set[str]] = {}
    for operation in operations:
        if operation.action != "update":
            continue
        carried = set(operation.payload or {})
        carried.update(reference.field for reference in operation.relationships or ())
        fields[operation.operation_id] = carried
    return [record for record in records if record.field in fields.get(record.operation_id, ())]
