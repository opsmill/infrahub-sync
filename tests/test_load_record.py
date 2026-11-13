"""The base-class read loop and its admission step `load_record` (spec 020 T009, research R2).

`DiffSyncMixin.model_loader` lists the raw records of a kind, applies the configured filters
and admits each record through `load_record`. On the source side, `load_record` records each
record it cannot build in the adapter's load report and continues with the next record:

| Error | Load report reason |
| --- | --- |
| `pydantic.ValidationError` of type errors only | `wrong_value_type` |
| `diffsync.exceptions.ObjectAlreadyExists` | `duplicate_source_identity` |
| `ExcludedRequiredPeerError` | `required_peer_excluded_by_filter` |
| `PeerNotFoundError` (required relationship) | `peer_not_found_in_source` |
| `PeerNotLoadedError` | `peer_not_loaded` |
| `TransformError` | `transform_failed` |

Any other error is raised as `RecordLoadError` with the kind and source ID. On the destination
side nothing is caught.

The source adapter is the fixture adapter of `tests/plan/problem_plan.py`, which implements only
`list_records` and `record_to_data`; the records come from the shared problem fixture.

Spec 020 T017 (and T021 for the peer errors) implements the behavior.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest
from diffsync import Adapter, DiffSyncModel
from diffsync.exceptions import ObjectAlreadyExists

from infrahub_sync import (
    DiffSyncMixin,
    DiffSyncModelMixin,
    RecordLoadError,
    SchemaMappingFilter,
    SchemaMappingModel,
    SchemaMappingTransform,
    SyncAdapter,
    SyncConfig,
)
from infrahub_sync.adapters.references import peer_unique_id
from infrahub_sync.plan.problems import find_problems
from infrahub_sync.runtime_schema.worker import bind_runtime_models
from tests.plan.conftest import PlanProblemModel, Variant, load_plan_problem_fixture
from tests.plan.problem_plan import FixtureSource, build_source, reference_is_required

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

    from tests.plan.conftest import PlanProblemFixture


def _records(fixture: PlanProblemFixture, picks: Mapping[str, Iterable[int]]) -> dict[str, list[dict[str, Any]]]:
    """The fixture's raw records of each kind, by source ID, in the order given."""
    every = fixture.source_records(Variant.ALL)
    picked: dict[str, list[dict[str, Any]]] = {}
    for kind, ids in picks.items():
        by_id = {record["id"]: record for record in every[kind]}
        picked[kind] = [by_id[source_id] for source_id in ids]
    return picked


def _read(source: FixtureSource, *kinds: str) -> None:
    """Read `kinds` in order through the base-class read loop."""
    for kind in kinds:
        source.model_loader(model_name=kind, model=getattr(source, kind))


def _loaded(source: Adapter, kind: str) -> list[str]:
    """The source IDs of the stored records of `kind`, sorted."""
    return sorted(str(getattr(item, "local_id", None)) for item in source.get_all(kind))


def _entries(source: DiffSyncMixin) -> list[tuple[Any, ...]]:
    """The load report as (reason, kind, source ID, field, peer, config entry, existing, loaded)."""
    report = source.load_report
    return [
        (
            entry.reason,
            entry.record.kind,
            entry.record.source_id,
            entry.field,
            None if entry.peer is None else (entry.peer.kind, entry.peer.source_id),
            entry.config_entry,
            None if entry.existing is None else entry.existing.source_id,
            entry.loaded,
        )
        for entry in report.entries
    ]


def test_wrong_value_type_is_recorded_and_the_read_continues() -> None:
    """`position` 2.5 for a Number is `wrong_value_type`; the next device is read."""
    fixture = load_plan_problem_fixture()
    source = build_source(fixture, _records(fixture, {"LocationSite": [1], "DcimDevice": [30, 31]}))

    _read(source, "LocationSite", "DcimDevice")

    assert _entries(source) == [("wrong_value_type", "DcimDevice", "30", "position", None, None, None, False)]
    assert _loaded(source, "DcimDevice") == ["31"]


def test_duplicate_source_identity_keeps_the_first_record_and_the_read_continues() -> None:
    """The second `dup` site is `duplicate_source_identity`; the store keeps site 4, then reads `paris`."""
    fixture = load_plan_problem_fixture()
    source = build_source(fixture, _records(fixture, {"LocationSite": [4, 5, 1]}))

    _read(source, "LocationSite")

    assert _entries(source) == [("duplicate_source_identity", "LocationSite", "5", None, None, None, "4", False)]
    assert _loaded(source, "LocationSite") == ["1", "4"]


def test_required_peer_excluded_by_a_filter_is_recorded_and_the_read_continues() -> None:
    """`mgmt0` needs device 35, which `filters[0]` removed; interface `eth0` of `leaf-1` is read."""
    fixture = load_plan_problem_fixture()
    source = build_source(
        fixture,
        _records(fixture, {"LocationSite": [1], "DcimDevice": [35, 31], "InterfacePhysical": [60, 50]}),
    )

    _read(source, "LocationSite", "DcimDevice", "InterfacePhysical")

    report = source.load_report
    assert report.was_filtered("DcimDevice", "35")
    assert report.filter_position("DcimDevice", "35") == 0
    assert _entries(source) == [
        (
            "required_peer_excluded_by_filter",
            "InterfacePhysical",
            "60",
            "device",
            ("DcimDevice", "35"),
            "filters[0]",
            None,
            False,
        )
    ]
    assert _loaded(source, "DcimDevice") == ["31"]
    assert _loaded(source, "InterfacePhysical") == ["50"]


def test_required_peer_not_in_the_source_is_recorded_and_the_read_continues() -> None:
    """`spine-1` refers to site 997, which the source does not hold; `leaf-1` is read."""
    fixture = load_plan_problem_fixture()
    source = build_source(fixture, _records(fixture, {"LocationSite": [1], "DcimDevice": [36, 31]}))

    _read(source, "LocationSite", "DcimDevice")

    assert _entries(source) == [
        ("peer_not_found_in_source", "DcimDevice", "36", "site", ("LocationSite", "997"), None, None, False)
    ]
    assert _loaded(source, "DcimDevice") == ["31"]


def test_peer_not_loaded_is_recorded_and_the_read_continues() -> None:
    """Interfaces of `core-1`, which could not be built, are `peer_not_loaded`; `leaf-1`'s are read."""
    fixture = load_plan_problem_fixture()
    source = build_source(
        fixture,
        _records(fixture, {"LocationSite": [1], "DcimDevice": [30, 31], "InterfacePhysical": [40, 50]}),
    )

    _read(source, "LocationSite", "DcimDevice", "InterfacePhysical")

    assert _entries(source) == [
        ("wrong_value_type", "DcimDevice", "30", "position", None, None, None, False),
        ("peer_not_loaded", "InterfacePhysical", "40", "device", ("DcimDevice", "30"), None, None, False),
    ]
    assert _loaded(source, "InterfacePhysical") == ["50"]


def test_failed_transform_is_recorded_by_position_and_the_read_continues() -> None:
    """The `name` transform of `IpamVRF` fails for `q-in-q`; `default` is read."""
    fixture = load_plan_problem_fixture()
    source = build_source(fixture, _records(fixture, {"IpamVRF": [11, 10]}))

    _read(source, "IpamVRF")

    assert _entries(source) == [("transform_failed", "IpamVRF", "11", "name", None, "transforms[0]", None, False)]
    assert _loaded(source, "IpamVRF") == ["10"]


def test_no_entry_holds_the_error_text_or_the_transform_expression() -> None:
    """A load report entry holds named fields only, never the text of the error it stands for."""
    fixture = load_plan_problem_fixture()
    source = build_source(fixture, _records(fixture, {"IpamVRF": [11]}))

    _read(source, "IpamVRF")

    report = source.load_report
    dumped = [entry.model_dump_json() for entry in report.entries]
    assert len(dumped) == 1
    assert "{{" not in dumped[0]
    assert "q-in-q" not in dumped[0]


class _BrokenSource(FixtureSource):
    """A source whose conversion fails with an error the admission step does not catch."""

    def record_to_data(
        self, record: Mapping[str, Any], element: SchemaMappingModel, model: type[DiffSyncModel]
    ) -> dict[str, Any]:
        if str(record["id"]) == "2":
            msg = "conversion failed"
            raise RuntimeError(msg)
        return super().record_to_data(record, element, model)


def test_any_other_error_is_raised_as_record_load_error() -> None:
    """An error outside the table stops the read as `RecordLoadError` naming the kind and source ID."""
    fixture = load_plan_problem_fixture()
    config = fixture.config()
    source = _BrokenSource(config=config, records=_records(fixture, {"LocationSite": [1, 2]}), order=fixture.order)
    bind_runtime_models(source, fixture.build_models(model_base=PlanProblemModel, config=config))

    with pytest.raises(RecordLoadError) as raised:
        _read(source, "LocationSite")

    error = raised.value
    assert (error.kind, error.source_id) == ("LocationSite", "2")
    assert isinstance(error.__cause__, RuntimeError)


class _MissingNameSource(FixtureSource):
    """A source whose conversion leaves out the required `name` of one site."""

    def record_to_data(
        self, record: Mapping[str, Any], element: SchemaMappingModel, model: type[DiffSyncModel]
    ) -> dict[str, Any]:
        data = super().record_to_data(record, element, model)
        if str(record["id"]) == "2":
            data.pop("name")
        return data


def test_a_missing_required_value_is_raised_as_record_load_error() -> None:
    """A `ValidationError` for a missing required value is not `wrong_value_type`: it stops the read."""
    fixture = load_plan_problem_fixture()
    config = fixture.config()
    source = _MissingNameSource(config=config, records=_records(fixture, {"LocationSite": [1, 2]}), order=fixture.order)
    bind_runtime_models(source, fixture.build_models(model_base=PlanProblemModel, config=config))

    with pytest.raises(RecordLoadError) as raised:
        _read(source, "LocationSite")

    error = raised.value
    assert (error.kind, error.source_id, error.field) == ("LocationSite", "2", "name")


def test_the_destination_side_catches_nothing() -> None:
    """On the destination side a duplicate identity still raises `ObjectAlreadyExists`."""
    fixture = load_plan_problem_fixture()
    source = build_source(fixture, _records(fixture, {"LocationSite": [4, 5]}))
    source.target = "destination"

    with pytest.raises(ObjectAlreadyExists):
        _read(source, "LocationSite")


# -- Review fixes: complementary filters, a record read twice, a defaulted identifier ------------


class _Device(DiffSyncModelMixin, DiffSyncModel):
    _modelname = "DcimDevice"
    _identifiers = ("name",)
    _attributes = ()
    name: str
    local_id: str | None = None


class _Tenant(DiffSyncModelMixin, DiffSyncModel):
    _modelname = "OrgTenant"
    _identifiers = ("name",)
    _attributes = ()
    name: str
    local_id: str | None = None


class _Vlan(DiffSyncModelMixin, DiffSyncModel):
    _modelname = "IpamVLAN"
    _identifiers = ("name", "vlan_group")
    _attributes = ("tenant",)
    name: str
    vlan_group: str = "default"
    tenant: str | None = None
    local_id: str | None = None


class _InlineSource(DiffSyncMixin, Adapter):
    """A source over inline records; `record_to_data` copies the ID and the name, and resolves `tenant`."""

    DcimDevice = _Device
    OrgTenant = _Tenant
    IpamVLAN = _Vlan
    top_level = ["DcimDevice", "OrgTenant", "IpamVLAN"]  # noqa: RUF012  # the diffsync class attribute
    target = "source"

    def __init__(self, records: list[dict[str, Any]], entries: list[SchemaMappingModel] | None = None) -> None:
        super().__init__()
        self.config = SyncConfig(
            name="inline-source",
            source=SyncAdapter(name="inline"),
            destination=SyncAdapter(name="infrahub"),
            schema_mapping=entries or [],
        )
        self._records = records

    def list_records(self, model_name: str, element: SchemaMappingModel) -> list[dict[str, Any]]:  # noqa: ARG002
        return [dict(record) for record in self._records]

    def record_to_data(
        self, record: Mapping[str, Any], element: SchemaMappingModel, model: type[DiffSyncModel]
    ) -> dict[str, Any]:
        # A record without an ID carries the local ID its adapter would write for it ("None" or "").
        source_id = str(record["id"]) if "id" in record else record["local_id"]
        data: dict[str, Any] = {"local_id": source_id, "name": record["name"]}
        if record.get("group"):
            data["vlan_group"] = record["group"]
        if record.get("tenant"):
            unique_id = peer_unique_id(
                self,
                "OrgTenant",
                str(record["tenant"]),
                owner=(element.name, source_id),
                field="tenant",
                required=reference_is_required(model, "tenant"),
            )
            if unique_id is not None:
                data["tenant"] = unique_id
        return data


_RACKED = SchemaMappingModel(
    name="DcimDevice",
    mapping="dcim.devices",
    fields=[],
    filters=[SchemaMappingFilter(field="rack", operation="is_not_empty")],
)
_UNRACKED = SchemaMappingModel(
    name="DcimDevice",
    mapping="dcim.devices",
    fields=[],
    filters=[
        SchemaMappingFilter(field="rack", operation="is_empty"),
        SchemaMappingFilter(field="role", operation="!=", value="lab"),
    ],
)


@pytest.mark.parametrize(
    "entries", [[_RACKED, _UNRACKED], [_UNRACKED, _RACKED]], ids=["racked-first", "unracked-first"]
)
def test_a_record_another_entry_of_its_kind_keeps_is_not_filtered(entries: list[SchemaMappingModel]) -> None:
    """Two DcimDevice entries split racked and unracked devices: only device 3, which both remove, is filtered."""
    records = [
        {"id": 1, "name": "racked", "rack": 7, "role": "leaf"},
        {"id": 2, "name": "unracked", "rack": None, "role": "leaf"},
        {"id": 3, "name": "lab", "rack": None, "role": "lab"},
    ]
    source = _InlineSource(records, entries)

    source.model_loader(model_name="DcimDevice", model=_Device)

    assert _loaded(source, "DcimDevice") == ["1", "2"]
    assert set(source.load_report.filtered_ids["DcimDevice"]) == {"3"}


def test_a_record_read_twice_under_one_source_id_is_one_record() -> None:
    """Tenant 5, returned twice by overlapping pages, is stored once and is not its own duplicate."""
    source = _InlineSource([])
    element = SchemaMappingModel(name="OrgTenant", mapping="tenancy.tenants", fields=[])

    for record in ({"id": 5, "name": "acme"}, {"id": 5, "name": "acme"}):
        source.load_record(_Tenant, element, record)
    report = find_problems(source=source, destination=None, config=None)

    assert _loaded(source, "OrgTenant") == ["5"]
    assert source.load_report.entries == []
    assert (report.problems, report.left_out) == ((), ())


@pytest.mark.parametrize("local_id", ["None", ""], ids=["none", "empty"])
def test_two_records_without_a_source_id_are_still_duplicates(local_id: str) -> None:
    """Two tenants `acme` with no source ID are two records: one `duplicate_source_identity` leaves out both."""
    source = _InlineSource([])
    element = SchemaMappingModel(name="OrgTenant", mapping="tenancy.tenants", fields=[])

    for number in (1, 2):
        source.load_record(_Tenant, element, {"name": "acme", "n": number, "local_id": local_id})
    report = find_problems(source=source, destination=None, config=None)

    assert [problem.problem_type for problem in report.problems] == ["duplicate_source_identity"]
    assert [item.cause for item in report.left_out] == ["has_problem", "has_problem"]


def test_a_loaded_record_without_a_defaulted_identifier_is_kept() -> None:
    """VLAN 7 has no group (defaulted) and names tenant 99, not in the source: it loads and is kept without it."""
    source = _InlineSource([])
    element = SchemaMappingModel(name="IpamVLAN", mapping="ipam.vlans", fields=[])

    stored = source.load_record(_Vlan, element, {"id": 7, "name": "users", "tenant": 99})
    report = find_problems(source=source, destination=None, config=None)

    assert stored is not None
    assert [(entry.reason, entry.loaded) for entry in source.load_report.entries] == [
        ("peer_not_found_in_source", True)
    ]
    assert report.left_out == ()
    assert [(item.record.kind, item.record.source_id, item.cause) for item in report.kept] == [
        ("IpamVLAN", "7", "peer_not_found_in_source")
    ]


# -- Review fixes: records without a source ID, references within one kind ----------------------


@pytest.mark.parametrize("local_id", ["None", ""], ids=["none", "empty"])
def test_a_dropdown_problem_lands_on_its_own_record_without_a_source_id(local_id: str) -> None:
    """Tenants `acme` and `bad` have no source ID: the Dropdown problem names `bad` and leaves out `bad` only."""
    source = _InlineSource([])
    element = SchemaMappingModel(name="OrgTenant", mapping="tenancy.tenants", fields=[])

    for name in ("acme", "bad"):
        source.load_record(_Tenant, element, {"name": name, "local_id": local_id})
    report = find_problems(
        source=source, destination=None, config=None, dropdown_choices={"OrgTenant": {"name": ["acme"]}}
    )

    assert [(problem.problem_type, problem.record.source_id, problem.value) for problem in report.problems] == [
        ("dropdown_value_not_in_choices", None, "bad")
    ]
    assert [item.record.identity for item in report.left_out] == [{"name": "bad"}]


def test_a_missing_optional_peer_is_kept_on_its_own_record_without_a_source_id() -> None:
    """VLANs `users` (tenant 99, not in the source) and `guests` have no source ID: `users` is the kept record."""
    source = _InlineSource([])
    element = SchemaMappingModel(name="IpamVLAN", mapping="ipam.vlans", fields=[])

    source.load_record(_Vlan, element, {"name": "guests", "local_id": ""})
    source.load_record(_Vlan, element, {"name": "users", "local_id": "", "tenant": 99})
    report = find_problems(source=source, destination=None, config=None)

    assert [(problem.problem_type, problem.record.identity) for problem in report.problems] == [
        ("peer_not_found_in_source", {"name": "users", "vlan_group": "default"})
    ]
    assert [item.record.identity for item in report.kept] == [{"name": "users", "vlan_group": "default"}]
    assert report.left_out == ()


class _Location(DiffSyncModelMixin, DiffSyncModel):
    _modelname = "LocationGeneric"
    _identifiers = ("name",)
    _attributes = ("parent", "sibling")
    name: str
    parent: str | None = None
    sibling: str | None = None
    local_id: str | None = None


class _Rack(DiffSyncModelMixin, DiffSyncModel):
    """A kind whose `parent` reference is required (the annotation does not accept `None`)."""

    _modelname = "LocationRack"
    _identifiers = ("name",)
    _attributes = ("parent",)
    name: str
    parent: str = ""
    local_id: str | None = None


class _TreeSource(DiffSyncMixin, Adapter):
    """A source whose records refer to a parent of their own kind; `transformed` counts transform runs."""

    LocationGeneric = _Location
    LocationRack = _Rack
    top_level = ["LocationGeneric", "LocationRack"]  # noqa: RUF012  # the diffsync class attribute
    target = "source"

    def __init__(self, records: list[dict[str, Any]], element: SchemaMappingModel) -> None:
        super().__init__()
        self.config = SyncConfig(
            name="tree-source",
            source=SyncAdapter(name="inline"),
            destination=SyncAdapter(name="infrahub"),
            schema_mapping=[element],
        )
        self._records = records
        self.transformed: list[str] = []

    def list_records(self, model_name: str, element: SchemaMappingModel) -> list[dict[str, Any]]:  # noqa: ARG002
        return [dict(record) for record in self._records]

    def record_to_data(
        self, record: Mapping[str, Any], element: SchemaMappingModel, model: type[DiffSyncModel]
    ) -> dict[str, Any]:
        source_id = str(record["id"])
        data: dict[str, Any] = {"local_id": source_id, "name": record["name"]}
        for field_name in ("parent", "sibling"):
            if not record.get(field_name):
                continue
            unique_id = peer_unique_id(
                self,
                element.name,
                str(record[field_name]),
                owner=(element.name, source_id),
                field=field_name,
                required=reference_is_required(model, field_name),
            )
            if unique_id is not None:
                data[field_name] = unique_id
        return data


def _tree(kind: str, records: list[dict[str, Any]]) -> _TreeSource:
    source = _TreeSource(records, SchemaMappingModel(name=kind, mapping="dcim.locations", fields=[]))
    source.model_loader(model_name=kind, model=getattr(source, kind))
    return source


@pytest.mark.parametrize("kind", ["LocationGeneric", "LocationRack"], ids=["optional", "required"])
def test_a_child_read_before_its_parent_refers_to_it(kind: str) -> None:
    """`paris` comes before its parent `france`: both load, `paris` names `france`, nothing is reported."""
    source = _tree(kind, [{"id": 2, "name": "paris", "parent": 1}, {"id": 1, "name": "france"}])

    assert _loaded(source, kind) == ["1", "2"]
    assert source.get(kind, "paris").model_dump()["parent"] == "france"
    assert source.load_report.entries == []


def test_a_three_level_chain_read_from_the_leaf_resolves_every_parent() -> None:
    """Room, building, site, in that order: every parent resolves, nothing is reported."""
    source = _tree(
        "LocationGeneric",
        [
            {"id": 3, "name": "room", "parent": 2},
            {"id": 2, "name": "building", "parent": 1},
            {"id": 1, "name": "site"},
        ],
    )

    assert source.get("LocationGeneric", "room").model_dump()["parent"] == "building"
    assert source.get("LocationGeneric", "building").model_dump()["parent"] == "site"
    assert source.load_report.entries == []


def test_a_parent_the_source_does_not_return_is_still_reported() -> None:
    """`paris` names parent 9, which the source does not list: `peer_not_found_in_source`, kept."""
    source = _tree("LocationGeneric", [{"id": 2, "name": "paris", "parent": 9}, {"id": 1, "name": "france"}])

    assert _entries(source) == [
        ("peer_not_found_in_source", "LocationGeneric", "2", "parent", ("LocationGeneric", "9"), None, None, True)
    ]


def test_records_that_refer_to_each_other_in_a_cycle_still_load() -> None:
    """`a` names `b` and `b` names `a`: the read ends, both load, and one reference is reported."""
    source = _tree("LocationGeneric", [{"id": 1, "name": "a", "parent": 2}, {"id": 2, "name": "b", "parent": 1}])

    assert _loaded(source, "LocationGeneric") == ["1", "2"]
    assert [(entry.reason, entry.in_cycle) for entry in source.load_report.entries] == [
        ("peer_not_found_in_source", True)
    ]


def test_a_record_that_waits_for_a_cycle_member_listed_later_resolves_it() -> None:
    """c (parent 1), a (parent 2), b (parent 1): a and b form a cycle, and c still names a."""
    source = _tree(
        "LocationGeneric",
        [
            {"id": 3, "name": "c", "parent": 1},
            {"id": 1, "name": "a", "parent": 2},
            {"id": 2, "name": "b", "parent": 1},
        ],
    )

    assert _loaded(source, "LocationGeneric") == ["1", "2", "3"]
    assert source.get("LocationGeneric", "c").model_dump()["parent"] == "a"
    assert source.get("LocationGeneric", "b").model_dump()["parent"] == "a"
    assert _entries(source) == [
        ("peer_not_found_in_source", "LocationGeneric", "1", "parent", ("LocationGeneric", "2"), None, None, True)
    ]


def test_a_record_in_two_cycles_resolves_the_reference_that_does_not_close_its_first_cycle() -> None:
    """a is in the cycle a, b through `parent` and refers to c of the cycle c, d through `sibling`.

    Only the reference that closes each cycle is written without its peer: a still names c.
    """
    source = _tree(
        "LocationGeneric",
        [
            {"id": 1, "name": "a", "parent": 2, "sibling": 3},
            {"id": 2, "name": "b", "parent": 1},
            {"id": 3, "name": "c", "parent": 4},
            {"id": 4, "name": "d", "parent": 3},
        ],
    )

    assert _loaded(source, "LocationGeneric") == ["1", "2", "3", "4"]
    assert source.get("LocationGeneric", "a").model_dump()["sibling"] == "c"
    assert source.get("LocationGeneric", "b").model_dump()["parent"] == "a"
    assert source.get("LocationGeneric", "d").model_dump()["parent"] == "c"
    assert sorted(
        (entry.record.source_id, entry.field, entry.peer.source_id if entry.peer else None, entry.in_cycle)
        for entry in source.load_report.entries
    ) == [("1", "parent", "2", True), ("3", "parent", "4", True)]


def test_a_reference_that_closes_a_cycle_has_its_own_reason() -> None:
    """The problem says the peer refers back in a cycle, not that the source does not return it."""
    source = _tree(
        "LocationGeneric",
        [
            {"id": 3, "name": "c", "parent": 1},
            {"id": 1, "name": "a", "parent": 2},
            {"id": 2, "name": "b", "parent": 1},
        ],
    )

    report = find_problems(source=source, destination=None, config=None)

    assert [problem.problem_type for problem in report.problems] == ["peer_not_found_in_source"]
    assert "in a cycle" in report.problems[0].reason
    assert "does not return" not in report.problems[0].reason
    assert [(item.record.source_id, item.cause) for item in report.kept] == [("1", "peer_not_found_in_source")]


def test_a_child_admitted_again_runs_its_transforms_once() -> None:
    """The transform of `paris` runs once, although `paris` is admitted twice."""
    calls: list[str] = []

    class _Counted(_Location):
        @classmethod
        def apply_transforms(cls, item: dict[str, Any], transforms: list[SchemaMappingTransform]) -> dict[str, Any]:  # noqa: ARG003
            calls.append(item["name"])
            item["name"] = item["name"].upper()
            return item

    element = SchemaMappingModel(
        name="LocationGeneric",
        mapping="dcim.locations",
        fields=[],
        transforms=[SchemaMappingTransform(field="name", expression="{{ name | upper }}")],
    )
    source = _TreeSource([{"id": 2, "name": "paris", "parent": 1}, {"id": 1, "name": "france"}], element)
    source.model_loader(model_name="LocationGeneric", model=_Counted)

    assert sorted(calls) == ["france", "paris"]
    assert source.get("LocationGeneric", "PARIS").model_dump()["parent"] == "FRANCE"


class _Port(DiffSyncModelMixin, DiffSyncModel):
    _modelname = "DcimInterface"
    _identifiers = ("name",)
    _attributes = ("speed", "mtu")
    name: str
    speed: int | None = None
    mtu: int | None = None
    local_id: str | None = None


class _PortSource(_InlineSource):
    DcimInterface = _Port

    def record_to_data(  # noqa: PLR6301  # the adapter hook
        self,
        record: Mapping[str, Any],
        element: SchemaMappingModel,  # noqa: ARG002
        model: type[DiffSyncModel],  # noqa: ARG002
    ) -> dict[str, Any]:
        return dict(record)


def test_two_problems_of_one_record_without_a_source_id_leave_out_one_record() -> None:
    """Port `eth0` has no source ID and two values of the wrong type: one record is left out, with both problems."""
    source = _PortSource([])
    element = SchemaMappingModel(name="DcimInterface", mapping="dcim.interfaces", fields=[])

    source.load_record(_Port, element, {"name": "eth0", "speed": "fast", "mtu": "big"})
    report = find_problems(source=source, destination=None, config=None)

    assert sorted(problem.field for problem in report.problems) == ["mtu", "speed"]
    assert [(item.record.identity, item.problem_types) for item in report.left_out] == [
        ({"name": "eth0"}, ["wrong_value_type"])
    ]


class _ChangedSource(_InlineSource):
    """A source whose changed records come per mapping entry, keyed by the entry's `mapping`."""

    def __init__(self, changed: dict[str, list[dict[str, Any]]], entries: list[SchemaMappingModel]) -> None:
        super().__init__([], entries)
        self._changed = changed

    def list_changed_records(
        self,
        model_name: str,  # noqa: ARG002
        element: SchemaMappingModel,
        cursor: object,  # noqa: ARG002
    ) -> list[dict[str, Any]]:
        return [dict(record) for record in self._changed[element.mapping or ""]]


def test_a_warm_read_forgets_a_source_id_once_across_the_entries_of_a_kind() -> None:
    """Two OrgTenant entries both return changed tenant 5: as in a cold read, both reads of it are admitted."""
    entries = [
        SchemaMappingModel(name="OrgTenant", mapping="tenancy.first", fields=[]),
        SchemaMappingModel(name="OrgTenant", mapping="tenancy.second", fields=[]),
    ]
    source = _ChangedSource(
        {"tenancy.first": [{"id": 5, "name": "first"}], "tenancy.second": [{"id": 5, "name": "second"}]}, entries
    )
    source.restore_record(_Tenant, {"name": "old", "local_id": "5"})

    source.load_changed_records("OrgTenant", _Tenant, cursor=None)  # ty: ignore[invalid-argument-type]

    assert sorted(item.model_dump()["name"] for item in source.get_all("OrgTenant")) == ["first", "second"]
