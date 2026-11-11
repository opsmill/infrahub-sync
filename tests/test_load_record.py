"""The base-class read loop and its admission step `load_record` (spec 019 T009, research R2).

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

Spec 019 T017 (and T021 for the peer errors) implements the behavior. Until then each test is
an expected failure: `DiffSyncMixin.model_loader` raises `NotImplementedError`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest
from diffsync.exceptions import ObjectAlreadyExists

from infrahub_sync.runtime_schema.worker import bind_runtime_models
from tests.plan.conftest import PlanProblemModel, Variant, load_plan_problem_fixture
from tests.plan.problem_plan import FixtureSource, build_source

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

    from diffsync import DiffSyncModel

    from infrahub_sync import SchemaMappingModel
    from tests.plan.conftest import PlanProblemFixture

pytestmark = pytest.mark.xfail(
    strict=True,
    raises=NotImplementedError,
    reason="spec 019 US1 not implemented: DiffSyncMixin.model_loader has no default read loop (T017)",
)


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


def _loaded(source: FixtureSource, kind: str) -> list[str]:
    """The source IDs of the stored records of `kind`, sorted."""
    return sorted(str(getattr(item, "local_id", None)) for item in source.get_all(kind))


def _entries(source: FixtureSource) -> list[tuple[Any, ...]]:
    """The load report as (reason, kind, source ID, field, peer, config entry, existing, loaded)."""
    report = source.load_report  # ty: ignore[unresolved-attribute] -- added by spec 019 T017
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

    report = source.load_report  # ty: ignore[unresolved-attribute] -- added by spec 019 T017
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

    report = source.load_report  # ty: ignore[unresolved-attribute] -- added by spec 019 T017
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

    # `RecordLoadError` does not exist before T017, so the test cannot name it in `pytest.raises`;
    # the missing read loop's `NotImplementedError` passes through unchanged.
    try:
        _read(source, "LocationSite")
    except NotImplementedError:
        raise
    except Exception as raised:  # noqa: BLE001
        error = raised
    else:
        pytest.fail("the read did not stop on the conversion error")

    assert type(error).__name__ == "RecordLoadError"
    assert (error.kind, error.source_id) == ("LocationSite", "2")  # ty: ignore[unresolved-attribute]
    assert isinstance(error.__cause__, RuntimeError)


def test_the_destination_side_catches_nothing() -> None:
    """On the destination side a duplicate identity still raises `ObjectAlreadyExists`."""
    fixture = load_plan_problem_fixture()
    source = build_source(fixture, _records(fixture, {"LocationSite": [4, 5]}))
    source.target = "destination"

    with pytest.raises(ObjectAlreadyExists):
        _read(source, "LocationSite")
