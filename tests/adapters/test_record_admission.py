"""The admission step of the adapter base class (spec 009 T009, research R3).

`DiffSyncMixin.load_record` builds one record and adds it to the store. On the source side it
records a record that cannot be built in the adapter's load report and continues with the
next record; on the destination side it catches nothing.
"""

from __future__ import annotations

import json
from typing import Any, ClassVar

import pydantic
import pytest
from diffsync import Adapter, DiffSyncModel
from diffsync.exceptions import ObjectAlreadyExists

from infrahub_sync import (
    LOAD_REPORT_VALUE_LIMIT,
    DiffSyncMixin,
    DiffSyncModelMixin,
    ExcludedRequiredPeerError,
    LoadReportEntry,
    PeerNotFoundError,
    PeerNotLoadedError,
    PeerReferenceError,
    RecordLoadError,
    SchemaMappingField,
    SchemaMappingModel,
    SourceRecordReference,
    SyncAdapter,
    SyncConfig,
)


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
    """Converts a record field by field; a record holding `raise` raises that exception."""

    top_level: ClassVar[list[str]] = ["Device"]
    Device = Device

    def __init__(self, target: str, records: list[dict[str, Any]]) -> None:
        super().__init__()
        self.target = target
        self.config = SyncConfig(
            name="admission",
            source=SyncAdapter(name="source"),
            destination=SyncAdapter(name="destination"),
            schema_mapping=[DEVICE_ELEMENT],
        )
        self._records = records

    def list_records(self, model_name: str, element: SchemaMappingModel) -> list[dict[str, Any]]:  # noqa: ARG002
        return list(self._records)

    def record_to_data(self, record: Any, element: SchemaMappingModel, model: type[DiffSyncModel]) -> dict[str, Any]:  # noqa: ANN401, ARG002, PLR6301
        if "raise" in record:
            raise record["raise"]
        data: dict[str, Any] = {"local_id": str(record["id"])}
        for field in element.fields:
            if field.mapping in record:
                data[field.name] = record[field.mapping]
        return data


def _load(records: list[dict[str, Any]], target: str = "source") -> _Adapter:
    adapter = _Adapter(target, records)
    adapter.load()
    return adapter


def _stored(adapter: _Adapter) -> list[dict[str, Any]]:
    return [item.model_dump(include={"local_id", "name", "position"}) for item in adapter.get_all("Device")]


def _peer_error(error_type: type[PeerReferenceError]) -> PeerReferenceError:
    return error_type(kind="Device", source_id="7", field="site", peer_kind="Site", peer_source_id="70")


def _assert_no_error_text(report: list[LoadReportEntry], *texts: str) -> None:
    dumped = json.dumps([entry.model_dump(mode="json") for entry in report])
    for text in texts:
        assert text not in dumped


def test_wrong_value_type_is_recorded_and_the_kind_keeps_loading() -> None:
    """A type error leaves the record out of the store and in the load report."""
    adapter = _load(
        [
            {"id": 1, "name": "core-1", "position": 2.5},
            {"id": 2, "name": "leaf-1", "position": 3},
        ]
    )

    assert _stored(adapter) == [{"local_id": "2", "name": "leaf-1", "position": 3}]
    assert adapter.load_report == [
        LoadReportEntry(
            kind="Device",
            source_id="1",
            identity={"name": "core-1"},
            reason="wrong_value_type",
            field="position",
            error_type="int_from_float",
            value=2.5,
            loaded=False,
        )
    ]


def test_wrong_value_type_limits_a_string_value_to_256_characters() -> None:
    """The refused value is kept, limited to 256 characters."""
    adapter = _load([{"id": 1, "name": "core-1", "position": "x" * 300}])

    (entry,) = adapter.load_report
    assert entry.error_type == "int_parsing"
    assert entry.value == "x" * LOAD_REPORT_VALUE_LIMIT
    assert LOAD_REPORT_VALUE_LIMIT == 256


def test_duplicate_source_identity_names_the_record_kept_in_the_store() -> None:
    """The store keeps the first record unchanged; the second one is recorded."""
    adapter = _load(
        [
            {"id": 4, "name": "dup", "position": 1},
            {"id": 5, "name": "dup", "position": 2},
        ]
    )

    assert _stored(adapter) == [{"local_id": "4", "name": "dup", "position": 1}]
    assert adapter.load_report == [
        LoadReportEntry(
            kind="Device",
            source_id="5",
            identity={"name": "dup"},
            reason="duplicate_source_identity",
            existing=SourceRecordReference(kind="Device", identity={"name": "dup"}, source_id="4"),
        )
    ]


@pytest.mark.parametrize(
    ("error_type", "reason"),
    [
        (ExcludedRequiredPeerError, "required_peer_excluded_by_filter"),
        (PeerNotLoadedError, "peer_not_loaded"),
        (PeerNotFoundError, "peer_not_found_in_source"),
    ],
)
def test_peer_errors_are_recorded_with_the_peer(error_type: type[PeerReferenceError], reason: str) -> None:
    """Each typed peer exception becomes its load report reason, naming the peer."""
    error = _peer_error(error_type)
    adapter = _load([{"id": 7, "raise": error}, {"id": 8, "name": "leaf-2"}])

    assert _stored(adapter) == [{"local_id": "8", "name": "leaf-2", "position": None}]
    assert adapter.load_report == [
        LoadReportEntry(
            kind="Device",
            source_id="7",
            reason=reason,  # ty: ignore[invalid-argument-type]
            field="site",
            peer=SourceRecordReference(kind="Site", identity=None, source_id="70"),
            loaded=False,
        )
    ]
    _assert_no_error_text(adapter.load_report, str(error))


def test_missing_required_value_fails_as_record_load_error() -> None:
    """A ValidationError that is not a type error is raised again with kind, record ID and field."""
    adapter = _Adapter("source", [{"id": 9, "position": 1}])

    with pytest.raises(RecordLoadError) as excinfo:
        adapter.load()

    error = excinfo.value
    assert (error.kind, error.source_id, error.field) == ("Device", "9", "name")
    assert isinstance(error.__cause__, pydantic.ValidationError)
    assert adapter.load_report == []


def test_any_other_failure_fails_as_record_load_error() -> None:
    """An exception the admission step does not record fails the load."""
    adapter = _Adapter("source", [{"id": 3, "raise": KeyError("name")}])

    with pytest.raises(RecordLoadError) as excinfo:
        adapter.load()

    assert (excinfo.value.kind, excinfo.value.source_id) == ("Device", "3")


@pytest.mark.parametrize(
    ("records", "expected"),
    [
        ([{"id": 1, "name": "core-1", "position": 2.5}], pydantic.ValidationError),
        ([{"id": 4, "name": "dup"}, {"id": 5, "name": "dup"}], ObjectAlreadyExists),
        ([{"id": 7, "raise": _peer_error(PeerNotFoundError)}], PeerNotFoundError),
    ],
    ids=["wrong-value-type", "duplicate", "peer-not-found"],
)
def test_destination_side_catches_nothing(records: list[dict[str, Any]], expected: type[Exception]) -> None:
    """A bad destination record is not a source problem: the exception reaches the caller."""
    adapter = _Adapter("destination", records)

    with pytest.raises(expected):
        adapter.load()

    assert adapter.load_report == []


def test_load_report_holds_no_error_text() -> None:
    """No entry holds the text of the exception that the admission step caught."""
    records: list[dict[str, Any]] = [
        {"id": 1, "name": "core-1", "position": 2.5},
        {"id": 4, "name": "dup"},
        {"id": 5, "name": "dup"},
    ]
    texts: list[str] = []
    try:
        Device(name="core-1", position=2.5)  # ty: ignore[invalid-argument-type]
    except pydantic.ValidationError as exc:
        texts.extend(error["msg"] for error in exc.errors(include_url=False))
        texts.append(str(exc))

    adapter = _load(records)

    assert {entry.reason for entry in adapter.load_report} == {"wrong_value_type", "duplicate_source_identity"}
    _assert_no_error_text(adapter.load_report, *texts, "already present")


def test_after_add_runs_for_each_loaded_record_only() -> None:
    """The `after_add` hook receives the added model and its raw record; a refused record skips it."""
    adapter = _Adapter("source", [])
    seen: list[tuple[str, int]] = []
    good = {"id": 1, "name": "leaf-1", "position": 1}
    bad = {"id": 2, "name": "core-1", "position": 2.5}

    for record in (good, bad):
        adapter.load_record(
            Device, DEVICE_ELEMENT, record, after_add=lambda item, raw: seen.append((item.name, raw["id"]))
        )

    assert seen == [("leaf-1", 1)]
