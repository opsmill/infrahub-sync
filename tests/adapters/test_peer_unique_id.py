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
from infrahub_sync.adapters.references import resolve_peer_list


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
KEPT_WITHOUT_LINK = "kept-without-link"

CASES = (
    Case("in-the-store", "1", required=True, expected="leaf-1"),
    Case("in-the-load-report-with-identity", "30", required=True, expected="core-1"),
    Case("in-the-load-report-without-identity-required", "40", required=True, expected=PeerNotLoadedError),
    # FR-005: an optional relationship to a left-out record keeps the record without the link.
    Case("in-the-load-report-without-identity-optional", "40", required=False, expected=KEPT_WITHOUT_LINK),
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

    if case.expected == KEPT_WITHOUT_LINK:
        assert result is None
        assert adapter.load_report == [
            *report_before,
            LoadReportEntry(
                kind="Interface",
                source_id="500",
                reason="peer_not_loaded",
                field="device",
                peer=SourceRecordReference(kind="Device", identity=None, source_id="40"),
                loaded=True,
            ),
        ]
        return

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


class Group(DiffSyncModelMixin, DiffSyncModel):
    """A record with a cardinality-many relationship to `Device`."""

    _modelname = "Group"
    _identifiers = ("name",)
    _attributes = ("devices",)

    name: str
    devices: list[str]


class ManyCase(NamedTuple):
    name: str
    peers: tuple[str, ...]
    required: bool
    loaded: bool
    entries: tuple[tuple[str, str, bool], ...]  # (reason, peer source ID, loaded)


MANY_CASES = (
    # H5: every peer of a required relationship is missing from the source: left out.
    ManyCase(
        "required-every-peer-not-in-the-source",
        ("998", "999"),
        required=True,
        loaded=False,
        entries=(("peer_not_found_in_source", "998", False), ("peer_not_found_in_source", "999", False)),
    ),
    ManyCase(
        "required-one-peer-resolved",
        ("1", "999"),
        required=True,
        loaded=True,
        entries=(("peer_not_found_in_source", "999", True),),
    ),
    ManyCase(
        "required-every-peer-not-loaded-without-identity",
        ("40",),
        required=True,
        loaded=False,
        entries=(("peer_not_loaded", "40", False),),
    ),
    ManyCase(
        "optional-every-peer-not-loaded-without-identity",
        ("40",),
        required=False,
        loaded=True,
        entries=(("peer_not_loaded", "40", True),),
    ),
    ManyCase("required-every-peer-removed-by-a-filter", ("35",), required=True, loaded=False, entries=()),
)


@pytest.mark.parametrize("case", MANY_CASES, ids=[case.name for case in MANY_CASES])
def test_a_cardinality_many_reference_is_admitted_as_research_r4_states(adapter: _Adapter, case: ManyCase) -> None:
    """A required relationship needs one resolved peer; each missing peer is recorded once."""
    report_before = list(adapter.load_report)

    def to_data(record: Any, element: Any, model: Any) -> dict[str, Any]:  # noqa: ANN401, ARG001
        devices, _ = resolve_peer_list(
            adapter,
            peer_kind="Device",
            peer_source_ids=case.peers,
            owner=("Group", "600"),
            field="devices",
            required=case.required,
        )
        return {"local_id": "600", "name": "group-1", "devices": devices}

    item = adapter.load_record(Group, None, {"id": 600}, to_data=to_data)

    assert (item is not None) is case.loaded
    added = adapter.load_report[len(report_before) :]
    peer_entries = [
        (entry.reason, entry.peer.source_id, entry.loaded)
        for entry in added
        if entry.reason in {"peer_not_found_in_source", "peer_not_loaded"} and entry.peer is not None
    ]
    assert tuple(peer_entries) == case.entries
    assert all(entry.kind == "Group" and entry.field == "devices" for entry in added)
    if case.name == "required-every-peer-removed-by-a-filter":
        assert [entry.reason for entry in added] == ["required_peer_excluded_by_filter"]
