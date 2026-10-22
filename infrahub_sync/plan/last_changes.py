"""Who last changed each value a plan proposes to change (FR-007, `contracts/plan-last-changes.md`).

The Infrahub adapter keeps, outside its DiffSync models, a map from (destination node id,
field name) to the `updated_by` and `updated_at` it read with the destination. While a plan is
derived, `LastChangeCollector` turns that map into one `LastChangeRecord` for each field each
update proposes to change; the writer stores them in `plan/last_changes.jsonl`. A create has
no record. The records are display data: apply does not read them.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from infrahub_sync.plan.models import LastChangeRecord, RecordedAccountRecord

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

    from infrahub_sync.plan.models import LastChangeFieldType, PlannedOperation


class LastChangeCollector:
    """Collects one `LastChangeRecord` for each field each update of a plan proposes to change.

    `changes` is the destination's map from (destination node id, field name) to what it read
    about that value's last change: an object with `updated_by` (with `id` and
    `display_label`, or `None`) and `updated_at`. A field the map holds nothing for gets a
    record with `None` values, which review shows as "not recorded". `metadata_read` is
    `False` when the destination did not ask for that metadata (Infrahub before 1.7.0).
    """

    def __init__(self, changes: Mapping[tuple[str, str], Any], *, metadata_read: bool = False) -> None:
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
            change = self.changes.get((operation.destination_id, field))
            account = getattr(change, "updated_by", None)
            account_id = getattr(account, "id", None)
            updated_at = getattr(change, "updated_at", None)
            self.records.append(
                LastChangeRecord(
                    operation_id=operation.operation_id,
                    field=field,
                    field_type=field_type,
                    updated_by=(
                        RecordedAccountRecord(id=account_id, display_label=getattr(account, "display_label", None))
                        if isinstance(account_id, str) and account_id
                        else None
                    ),
                    updated_at=updated_at if isinstance(updated_at, str) else None,
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
