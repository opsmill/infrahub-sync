"""Reference resolution of the adapter base class (spec 009 T010, research R4).

One case per row of the research.md R4 table. The peer kind `Device` is loaded through the
default `model_loader` with a `role != lab` filter, so its records end up in the store, in the
load report, or among the IDs the filter removed.
"""

from __future__ import annotations

from typing import Any, ClassVar, NamedTuple

import pytest
from diffsync import Adapter, DiffSyncModel

from infrahub_sync import (
    DiffSyncMixin,
    DiffSyncModelMixin,
    ExcludedRequiredPeerError,
    LoadReportEntry,
    PeerNotFoundError,
    PeerNotLoadedError,
    PeerReferenceError,
    SchemaMappingField,
    SchemaMappingFilter,
    SchemaMappingModel,
    SourceRecordReference,
    SyncAdapter,
    SyncConfig,
    is_not_in_source_placeholder,
    not_in_source_placeholder,
)


class Device(DiffSyncModelMixin, DiffSyncModel):
    _modelname = "Device"
    _identifiers = ("name",)
    _attributes = ("position",)

    name: str
    position: int | None = None


class _Adapter(DiffSyncMixin, Adapter):
    top_level: ClassVar[list[str]] = ["Device"]
    Device = Device

    RECORDS: ClassVar[list[dict[str, Any]]] = [
        {"id": 1, "name": "leaf-1", "position": 1, "role": "leaf"},
        # wrong_value_type: in the load report with its identity.
        {"id": 30, "name": "core-1", "position": 2.5, "role": "core"},
        # The conversion fails before the identity is known: in the load report without identity.
        {"id": 40, "name": "edge-1", "fail": True, "role": "edge"},
        # Removed by the `role != lab` filter.
        {"id": 35, "name": "lab-1", "position": 1, "role": "lab"},
    ]

    def __init__(self) -> None:
        super().__init__()
        self.target = "source"
        self.config = SyncConfig(
            name="peers",
            source=SyncAdapter(name="source"),
            destination=SyncAdapter(name="destination"),
            schema_mapping=[
                SchemaMappingModel(
                    name="Device",
                    mapping="dcim.devices",
                    identifiers=["name"],
                    filters=[SchemaMappingFilter(field="role", operation="!=", value="lab")],
                    fields=[
                        SchemaMappingField(name="name", mapping="name"),
                        SchemaMappingField(name="position", mapping="position"),
                    ],
                )
            ],
        )

    def list_records(self, model_name: str, element: SchemaMappingModel) -> list[dict[str, Any]]:  # noqa: ARG002
        return [dict(record) for record in self.RECORDS]

    def record_to_data(self, record: Any, element: SchemaMappingModel, model: type[DiffSyncModel]) -> dict[str, Any]:  # noqa: ANN401, ARG002, PLR6301
        if record.get("fail"):
            raise PeerNotFoundError(kind="Device", source_id="40", field="site", peer_kind="Site", peer_source_id="9")
        return {"local_id": str(record["id"]), "name": record["name"], "position": record["position"]}


class Case(NamedTuple):
    name: str
    peer_source_id: str
    required: bool
    expected: object


OWNER = ("Interface", "500")
PLACEHOLDER = "placeholder"

CASES = (
    Case("in-the-store", "1", required=True, expected="leaf-1"),
    Case("in-the-load-report-with-identity", "30", required=True, expected="core-1"),
    Case("in-the-load-report-without-identity-required", "40", required=True, expected=PeerNotLoadedError),
    Case("in-the-load-report-without-identity-optional", "40", required=False, expected=PeerNotLoadedError),
    Case("removed-by-a-filter-required", "35", required=True, expected=ExcludedRequiredPeerError),
    Case("removed-by-a-filter-optional", "35", required=False, expected=None),
    Case("not-in-the-source-required", "999", required=True, expected=PeerNotFoundError),
    Case("not-in-the-source-optional", "999", required=False, expected=PLACEHOLDER),
)


@pytest.fixture
def adapter() -> _Adapter:
    loaded = _Adapter()
    loaded.load()
    assert [entry.reason for entry in loaded.load_report] == ["wrong_value_type", "peer_not_found_in_source"]
    return loaded


@pytest.mark.parametrize("case", CASES, ids=[case.name for case in CASES])
def test_peer_unique_id_follows_the_research_r4_table(adapter: _Adapter, case: Case) -> None:
    """Each kind of peer resolves, raises or records as research R4 states."""
    report_before = list(adapter.load_report)

    def resolve() -> str | None:
        return adapter.peer_unique_id(
            "Device", case.peer_source_id, owner=OWNER, field="device", required=case.required
        )

    if isinstance(case.expected, type) and issubclass(case.expected, Exception):
        with pytest.raises(case.expected) as excinfo:
            resolve()
        error = excinfo.value
        assert isinstance(error, PeerReferenceError)
        assert (error.kind, error.source_id, error.field) == ("Interface", "500", "device")
        assert (error.peer_kind, error.peer_source_id) == ("Device", case.peer_source_id)
        assert adapter.load_report == report_before
        return

    result = resolve()

    if case.expected != PLACEHOLDER:
        assert result == case.expected
        assert adapter.load_report == report_before
        return

    assert result == not_in_source_placeholder("Device", "999")
    assert is_not_in_source_placeholder(result)
    assert adapter.load_report == [
        *report_before,
        LoadReportEntry(
            kind="Interface",
            source_id="500",
            reason="peer_not_found_in_source",
            field="device",
            peer=SourceRecordReference(kind="Device", identity=None, source_id="999"),
            loaded=True,
        ),
    ]
