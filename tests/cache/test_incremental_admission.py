"""The engine's own model builds go through the admission step (spec 009 T024, T026).

Incremental snapshot rows and changed rows are built through `load_record` with
`identity_record_data`, so a row that cannot be built is recorded in the source load report.
`Potenda._load_side` keeps both load reports and does not fail on a recorded record; a
`RecordLoadError` still fails the load.
"""

from __future__ import annotations

from typing import Any, ClassVar

import pytest
from diffsync import Adapter, DiffSyncModel

from infrahub_sync import (
    DiffSyncMixin,
    DiffSyncModelMixin,
    RecordLoadError,
    SchemaMappingField,
    SchemaMappingModel,
    SyncAdapter,
    SyncConfig,
    identity_record_data,
)
from infrahub_sync.cache.cursors import CursorState, CursorTier
from infrahub_sync.cache.incremental import apply_changed_rows
from infrahub_sync.potenda import Potenda


class Device(DiffSyncModelMixin, DiffSyncModel):
    _modelname = "Device"
    _identifiers = ("name",)
    _attributes = ("position",)

    name: str
    position: int | None = None


DEVICE_ELEMENT = SchemaMappingModel(
    name="Device",
    mapping="dcim.devices",
    identifiers=["name"],
    fields=[SchemaMappingField(name="name", mapping="name"), SchemaMappingField(name="position", mapping="position")],
)


class _Adapter(DiffSyncMixin, Adapter):
    """Loads its records as model data; its changed rows come from `changed`."""

    top_level: ClassVar[list[str]] = ["Device"]
    Device = Device

    def __init__(self, target: str, records: list[dict[str, Any]], changed: list[dict[str, Any]] | None = None) -> None:
        super().__init__(name=target)
        self.target = target
        self.config = SyncConfig(
            name="admission",
            source=SyncAdapter(name="source"),
            destination=SyncAdapter(name="destination"),
            schema_mapping=[DEVICE_ELEMENT],
        )
        self._records = records
        self._changed = changed or []

    def list_records(self, model_name: str, element: SchemaMappingModel) -> list[dict[str, Any]]:  # noqa: ARG002
        return list(self._records)

    def record_to_data(self, record: Any, element: SchemaMappingModel, model: type[DiffSyncModel]) -> dict[str, Any]:  # noqa: ANN401, ARG002, PLR6301
        if "raise" in record:
            raise record["raise"]
        return dict(record)

    def list_changed_since(self, model_name: str, cursor: CursorState) -> list[dict[str, Any]]:  # noqa: ARG002
        return list(self._changed)


CURSOR = CursorState(CursorTier.TIMESTAMP, "2026-10-01T00:00:00Z", safe=True)


def test_identity_record_data_returns_the_row_as_model_data() -> None:
    row = {"name": "leaf-1", "position": 1, "local_id": "7"}

    data = identity_record_data(row, None, Device)

    assert data == row
    assert data is not row


def test_snapshot_rows_record_a_duplicate_in_the_source_load_report() -> None:
    adapter = _Adapter("source", [])
    row = {"name": "leaf-1", "position": 1, "local_id": "7"}

    adapter.load_record(Device, None, row, to_data=identity_record_data)
    adapter.load_record(Device, None, dict(row), to_data=identity_record_data)

    assert [item.get_unique_id() for item in adapter.get_all("Device")] == ["leaf-1"]
    assert [entry.reason for entry in adapter.load_report] == ["duplicate_source_identity"]


def test_changed_rows_update_a_loaded_model_and_record_a_row_that_cannot_be_built() -> None:
    adapter = _Adapter(
        "source",
        [{"name": "leaf-1", "position": 1, "local_id": "7"}],
        changed=[
            {"name": "leaf-1", "position": 4, "local_id": "8"},
            {"name": "core-1", "position": 2.5, "local_id": "9"},
        ],
    )
    adapter.load()

    apply_changed_rows(adapter, "Device", Device, CURSOR)

    stored = adapter.get_all("Device")
    assert [item.model_dump(include={"name", "position", "local_id"}) for item in stored] == [
        {"name": "leaf-1", "position": 4, "local_id": "8"}
    ]
    assert [(entry.reason, entry.source_id) for entry in adapter.load_report] == [("wrong_value_type", "9")]


def _potenda(source: _Adapter, destination: _Adapter) -> Potenda:
    return Potenda(
        source=source,
        destination=destination,
        config=None,  # ty: ignore[invalid-argument-type]
        top_level=["Device"],
        show_progress=False,
    )


def test_load_side_keeps_both_load_reports_and_does_not_fail_on_a_recorded_record() -> None:
    source = _Adapter("source", [{"name": "leaf-1", "position": 1}, {"name": "core-1", "position": 2.5}])
    destination = _Adapter("destination", [{"name": "leaf-1", "position": 1}])
    ptd = _potenda(source, destination)

    ptd.source_load()
    ptd.destination_load()

    assert [entry.reason for entry in ptd.source_load_report] == ["wrong_value_type"]
    assert ptd.destination_load_report == []
    assert [item.get_unique_id() for item in source.get_all("Device")] == ["leaf-1"]


def test_load_side_still_fails_on_a_record_load_error() -> None:
    source = _Adapter("source", [{"name": "leaf-1", "raise": RuntimeError("unexpected")}])
    ptd = _potenda(source, _Adapter("destination", []))

    with pytest.raises(ValueError, match="An error occurred while loading") as caught:
        ptd.source_load()

    assert isinstance(caught.value.__cause__, RecordLoadError)
    assert ptd.source_load_report == []
