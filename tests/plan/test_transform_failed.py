"""A transform that fails for one source record is a plan problem (spec 009 `transform_failed`).

The fixture maps VRF names through a transform that knows `default` and `mgmt` only, as the
NetBox example maps interface modes: the VRF `q-in-q` makes the transform fail. The record is
recorded in the load report, the plan lists a `transform_failed` problem that names the kind,
the record, the field and the transform's position, never the source value or the error text,
and the rest of the plan continues.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from infrahub_sync import (
    DiffSyncModelMixin,
    LoadReportEntry,
    SchemaMappingField,
    SchemaMappingModel,
    SchemaMappingTransform,
)
from infrahub_sync.load_report import transform_each
from infrahub_sync.plan.canonical import canonical_json_bytes
from tests.plan.problem_fixtures import (
    UNMAPPED_VRF_NAME,
    UNMAPPED_VRF_SOURCE_ID,
    VRF_NAME_TRANSFORM,
    Variant,
    build_source_adapter,
    source_records,
)
from tests.plan.problem_plan import plan_fixture

if TYPE_CHECKING:
    from pathlib import Path

    from infrahub_sync import TransformError


def test_the_loader_records_the_record_and_loads_the_others() -> None:
    adapter = build_source_adapter(Variant.ALL)
    adapter.load()

    entries = [entry for entry in adapter.load_report if entry.reason == "transform_failed"]

    assert entries == [
        LoadReportEntry(
            kind="IpamVRF",
            source_id=UNMAPPED_VRF_SOURCE_ID,
            reason="transform_failed",
            field="name",
            config_entry="transforms[0]",
        )
    ]
    assert [item.name for item in adapter.get_all("IpamVRF")] == ["default"]  # ty: ignore[unresolved-attribute]  # runtime model field


def test_the_plan_lists_the_problem_without_the_value(tmp_path: Path) -> None:
    planned = plan_fixture(tmp_path / "run")

    (problem,) = [item for item in planned.report.problems if item.problem_type == "transform_failed"]

    assert (problem.record.kind, problem.record.source_id, problem.record.identity) == (
        "IpamVRF",
        UNMAPPED_VRF_SOURCE_ID,
        None,
    )
    assert (problem.field, problem.config_entry, problem.value) == ("name", "transforms[0]", None)
    line = canonical_json_bytes(problem.model_dump()).decode()
    assert UNMAPPED_VRF_NAME not in line
    assert "UndefinedError" not in line
    assert VRF_NAME_TRANSFORM not in line
    assert "transforms[0]" in problem.reason


def test_the_record_is_left_out_and_the_rest_of_the_plan_continues(tmp_path: Path) -> None:
    planned = plan_fixture(tmp_path / "run")

    left = [item for item in planned.report.left_out if item.record.kind == "IpamVRF"]
    assert [(item.record.source_id, item.cause, item.problem_types, item.loaded) for item in left] == [
        (UNMAPPED_VRF_SOURCE_ID, "has_problem", ["transform_failed"], False)
    ]
    vrfs = [operation.identity for operation in planned.operations if operation.kind == "IpamVRF"]
    assert vrfs == [{"name": "default"}]


def test_a_record_that_refers_to_it_optionally_is_kept_without_that_relationship(tmp_path: Path) -> None:
    records = source_records(Variant.NO_PROBLEMS)
    records["IpamVRF"].append({"id": int(UNMAPPED_VRF_SOURCE_ID), "name": UNMAPPED_VRF_NAME})
    records["IpamIPAddress"].append(
        {"id": 90, "address": "10.0.0.90/32", "device": None, "vrf": {"id": int(UNMAPPED_VRF_SOURCE_ID)}}
    )

    planned = plan_fixture(tmp_path / "run", records=records)

    kept = [item for item in planned.report.kept if item.record.kind == "IpamIPAddress"]
    assert [(item.record.source_id, item.relationship, item.left_out_peer.source_id) for item in kept] == [
        ("90", "vrf", UNMAPPED_VRF_SOURCE_ID)
    ]


def _element(*transforms: tuple[str, str]) -> SchemaMappingModel:
    return SchemaMappingModel(
        name="Device",
        mapping="dcim.devices",
        identifiers=["name"],
        fields=[SchemaMappingField(name="name", mapping="name")],
        transforms=[SchemaMappingTransform(field=field, expression=expression) for field, expression in transforms],
    )


def test_transform_each_names_the_position_of_the_failed_transform() -> None:
    element = _element(("name", "{{ name | upper }}"), ("role", "{{ {'a': 'b'}[role] }}"))
    failures: list[TransformError] = []
    records: list[dict[str, Any]] = [{"id": 1, "name": "a", "role": "a"}, {"id": 2, "name": "b", "role": "z"}]

    result = transform_each(DiffSyncModelMixin, records, element, failures.append)

    assert [record["id"] for record in result] == [1]
    assert [(failure.kind, failure.source_id, failure.field, failure.position) for failure in failures] == [
        ("Device", "2", "role", 1)
    ]
