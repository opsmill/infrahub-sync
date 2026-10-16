"""Tests for DiffSyncMixin adapter contract methods."""

from __future__ import annotations

from typing import Any, ClassVar

import pytest

from infrahub_sync import (
    DiffSyncMixin,
    SchemaMappingField,
    SchemaMappingFilter,
    SchemaMappingModel,
    SyncAdapter,
    SyncConfig,
)
from infrahub_sync.cache.cursors import CursorState, CursorTier


class _Stub(DiffSyncMixin):
    pass


def test_cursor_tier_for_defaults_to_none() -> None:
    assert _Stub().cursor_tier_for("Anything") is CursorTier.NONE


def test_list_changed_since_raises_when_unimplemented() -> None:
    stub = _Stub()
    with pytest.raises(NotImplementedError):
        list(stub.list_changed_since("Anything", CursorState(tier=CursorTier.NONE)))


def test_list_existing_ids_raises_when_unimplemented() -> None:
    stub = _Stub()
    with pytest.raises(NotImplementedError):
        list(stub.list_existing_ids("Anything"))


def test_list_records_raises_when_unimplemented() -> None:
    """`list_records` is one of the two methods an adapter implements."""
    with pytest.raises(NotImplementedError):
        _Stub().list_records("Device", SchemaMappingModel(name="Device", mapping="dcim.devices"))


def test_record_to_data_raises_when_unimplemented() -> None:
    """`record_to_data` is the other method an adapter implements."""
    element = SchemaMappingModel(name="Device", mapping="dcim.devices")
    with pytest.raises(NotImplementedError):
        _Stub().record_to_data({}, element, _RecordingModel)  # ty: ignore[invalid-argument-type]


# ---------------------------------------------------------------------------------------
# The default `model_loader` (spec 009 T008)
# ---------------------------------------------------------------------------------------


class _RecordingModel:
    """Records the filter and transform calls the loader makes, in order."""

    calls: ClassVar[list[tuple[str, list[str]]]] = []

    @classmethod
    def filter_records(cls, records: list[dict[str, Any]], schema_mapping: SchemaMappingModel) -> list[dict[str, Any]]:
        cls.calls.append(("filter", [str(record["id"]) for record in records]))
        keep = schema_mapping.filters[0].value if schema_mapping.filters else None
        return [record for record in records if keep is None or record["role"] != keep]

    @classmethod
    def transform_records(
        cls,
        records: list[dict[str, Any]],
        schema_mapping: SchemaMappingModel,  # noqa: ARG003
    ) -> list[dict[str, Any]]:
        cls.calls.append(("transform", [str(record["id"]) for record in records]))
        return [{**record, "transformed": True} for record in records]


RECORDS = [
    {"id": 1, "role": "leaf"},
    {"id": 2, "role": "lab"},
    {"id": 3, "role": "core"},
    {"id": 4, "role": "lab"},
]


class _LoopAdapter(DiffSyncMixin):
    """An adapter that implements `list_records` only; `load_record` is recorded."""

    def __init__(self, target: str, *, filters: list[SchemaMappingFilter] | None) -> None:
        self.target = target
        self.config = SyncConfig(
            name="loop",
            source=SyncAdapter(name="source"),
            destination=SyncAdapter(name="destination"),
            schema_mapping=[
                SchemaMappingModel(name="Other", mapping="other"),
                SchemaMappingModel(
                    name="Device",
                    mapping="dcim.devices",
                    filters=filters,
                    fields=[SchemaMappingField(name="name", mapping="name")],
                ),
                SchemaMappingModel(name="Device", mapping=None),
            ],
        )
        self.listed: list[tuple[str, str | None]] = []
        self.loaded: list[tuple[Any, SchemaMappingModel, dict[str, Any]]] = []

    def list_records(self, model_name: str, element: SchemaMappingModel) -> list[dict[str, Any]]:
        self.listed.append((model_name, element.mapping))
        return [dict(record) for record in RECORDS]

    def load_record(self, model: Any, element: SchemaMappingModel | None, record: Any, **_hooks: Any) -> None:  # noqa: ANN401
        assert element is not None  # the base class loop always passes its mapping entry
        self.loaded.append((model, element, record))


@pytest.fixture
def recording_model() -> type[_RecordingModel]:
    _RecordingModel.calls = []
    return _RecordingModel


def test_default_model_loader_filters_transforms_and_admits_each_record_in_order(
    recording_model: type[_RecordingModel],
) -> None:
    """Source side: mapping lookup, `list_records`, filter, transform, then `load_record` per record."""
    adapter = _LoopAdapter("source", filters=[SchemaMappingFilter(field="role", operation="!=", value="lab")])

    adapter.model_loader("Device", recording_model)

    # Only the entry with a mapping is listed; the entry of another kind and the entry
    # without a mapping are skipped.
    assert adapter.listed == [("Device", "dcim.devices")]
    assert recording_model.calls == [("filter", ["1", "2", "3", "4"]), ("transform", ["1", "3"])]
    assert [(model, element.mapping) for model, element, _record in adapter.loaded] == [
        (recording_model, "dcim.devices"),
        (recording_model, "dcim.devices"),
    ]
    assert [record for _model, _element, record in adapter.loaded] == [
        {"id": 1, "role": "leaf", "transformed": True},
        {"id": 3, "role": "core", "transformed": True},
    ]
    assert adapter.excluded_peer_ids("Device") == {"2", "4"}


def test_default_model_loader_records_no_removed_ids_without_a_filter(recording_model: type[_RecordingModel]) -> None:
    """A mapping entry with no filter removes nothing, so no ID counts as removed by a filter."""
    adapter = _LoopAdapter("source", filters=None)

    adapter.model_loader("Device", recording_model)

    assert [record["id"] for _model, _element, record in adapter.loaded] == [1, 2, 3, 4]
    assert adapter.excluded_peer_ids("Device") == set()


def test_default_model_loader_does_not_filter_on_the_destination_side(recording_model: type[_RecordingModel]) -> None:
    """The destination side loads every listed record as it is, without filter or transform."""
    adapter = _LoopAdapter("destination", filters=[SchemaMappingFilter(field="role", operation="!=", value="lab")])

    adapter.model_loader("Device", recording_model)

    assert recording_model.calls == []
    assert [record for _model, _element, record in adapter.loaded] == [dict(record) for record in RECORDS]
    assert adapter.excluded_peer_ids("Device") == set()
