"""The warm read of a source with the read loop (spec 019 research R22).

A warm read rebuilds a kind from the previous run's source snapshot and admits only the records
changed since the saved cursor. `WarmSourceReadMixin` holds the adapter side of it; the engine
side is `Potenda._load_source_kinds`. `DiffSyncMixin` inherits it, and every method runs on a
`DiffSyncMixin`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Iterable

    from infrahub_sync import DiffSyncMixin, SchemaMappingModel
    from infrahub_sync.cache.cursors import CursorState


class WarmSourceReadMixin:
    """The methods a warm read of the source calls on the adapter."""

    def list_changed_records(self, model_name: str, element: SchemaMappingModel, cursor: CursorState) -> Iterable[Any]:  # pylint: disable=unused-argument  # abstract hook
        """Return the raw records of one mapping entry changed since `cursor`, before any filter.

        The warm counterpart of `list_records`. A source adapter that implements it can read a
        kind warm: the engine rebuilds the kind from the previous snapshot and passes these
        records through `load_changed_records`. Without it, the source reads cold.
        """
        msg = f"{type(self).__name__}.list_changed_records is not implemented."
        raise NotImplementedError(msg)

    def implements_list_changed_records(self) -> bool:
        """Tell whether this adapter implements `list_changed_records`."""
        return type(self).list_changed_records is not WarmSourceReadMixin.list_changed_records

    def restore_record(self: DiffSyncMixin, model: type[Any], data: dict[str, Any]) -> None:
        """Store a record rebuilt from the previous snapshot and index its source record ID."""
        item = model(**data)
        self.add(item)  # ty: ignore[unresolved-attribute] -- DiffSync adapter method
        self._index(item, data)

    def load_changed_records(self: DiffSyncMixin, model_name: str, model: type[Any], cursor: CursorState) -> None:
        """Admit the records of `model_name` changed since `cursor` through `load_record`.

        Each changed record first replaces the stored record with the same source record ID, so
        a record the cursor overlap returns again, a renamed record, and a record that a filter
        now removes or that now fails give the result of a cold read. The kind then counts as
        read in full: the stored records and the changed records are its whole population.
        """
        for element in self.config.schema_mapping:
            if element.name != model_name or not element.mapping:
                continue
            records = list(self.list_changed_records(model_name, element, cursor))
            for record in records:
                self.forget_record(model_name, self.record_source_id(record))
            for record in self.filter_source_records(model, element, records):
                self.load_record(model, element, record)
        self._read_state.read_kinds.add(model_name)

    def forget_record(self: DiffSyncMixin, kind: str, source_id: str | None) -> None:
        """Remove the stored record `source_id` of `kind`, when the read stored one."""
        if source_id is None:
            return
        unique_id = self._read_state.peer_index.pop((kind, source_id), None)
        if unique_id is None:
            return
        item = self.store.get(model=kind, identifier=unique_id)
        self.remove(item)  # ty: ignore[unresolved-attribute] -- DiffSync adapter method

    def kind_read_is_clean(self: DiffSyncMixin, kind: str) -> bool:
        """Tell whether the read of `kind` recorded no load report entry and no filtered record."""
        report = self.load_report
        return not report.entries_for(kind) and not report.filtered_ids.get(kind)

    def discard_kind(self: DiffSyncMixin, kind: str, *, entries_from: int) -> None:
        """Remove the stored records of `kind` and what its read recorded, before a cold read.

        `entries_from` is the number of load report entries before the read of `kind` started;
        only that read added the entries after it.
        """
        for item in list(self.store.get_all(model=kind)):
            self.remove(item)  # ty: ignore[unresolved-attribute] -- DiffSync adapter method
        state = self._read_state
        del state.report.entries[entries_from:]
        state.report.filtered_ids.pop(kind, None)
        for key in [key for key in state.not_loaded if key[0] == kind]:
            del state.not_loaded[key]
        for key in [key for key in state.peer_index if key[0] == kind]:
            del state.peer_index[key]
        state.read_kinds.discard(kind)
