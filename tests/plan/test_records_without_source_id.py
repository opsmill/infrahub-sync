"""Records the source returns without a source ID keep their problems in the plan (review M1, M2).

A record without a source ID is named by its identity. The problem pass must find its load
report entry, and two refused duplicates without a source ID are two records, not one.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from tests.plan.problem_fixtures import ProblemFixtureAdapter, Variant, source_records
from tests.plan.problem_plan import PlannedFixture, plan_fixture

if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path

    from diffsync import DiffSyncModel

    from infrahub_sync import SchemaMappingModel


class _SitesWithoutSourceId(ProblemFixtureAdapter):
    """Returns `LocationSite` records without an `id`, so they carry no source ID."""

    def record_to_data(
        self, record: Mapping[str, Any], element: SchemaMappingModel, model: type[DiffSyncModel]
    ) -> dict[str, Any]:
        if "id" in record:
            return super().record_to_data(record, element, model)
        return {field.name: record.get(field.mapping) for field in element.fields if field.mapping is not None}


def _plan(tmp_path: Path) -> PlannedFixture:
    records = source_records(Variant.NO_PROBLEMS)
    records["LocationSite"] = [
        {"name": "paris", "status": "active"},
        # `wrong_value_type`: a Dropdown value must be text.
        {"name": "rome", "status": 7},
        {"name": "dup", "status": "active"},
        {"name": "dup", "status": "planned"},
        {"name": "dup", "status": "active"},
    ]
    return plan_fixture(tmp_path / "run", records=records, source_class=_SitesWithoutSourceId)


def test_a_problem_of_a_record_without_a_source_id_is_listed_and_the_record_left_out(tmp_path: Path) -> None:
    """Review M1: the load report entry of `rome` is found by its identity."""
    planned = _plan(tmp_path)

    (problem,) = [problem for problem in planned.report.problems if problem.problem_type == "wrong_value_type"]
    (left,) = [entry for entry in planned.report.left_out if entry.record.identity == {"name": "rome"}]

    assert (problem.record.kind, problem.record.identity, problem.record.source_id) == (
        "LocationSite",
        {"name": "rome"},
        None,
    )
    assert problem.field == "status"
    assert (left.cause, left.problem_types, left.loaded) == ("has_problem", ["wrong_value_type"], False)
    assert all(operation.identity != {"name": "rome"} for operation in planned.operations)


def test_two_refused_duplicates_without_a_source_id_are_two_records(tmp_path: Path) -> None:
    """Review M2: each refused duplicate has its own problem, naming the record the store kept."""
    planned = _plan(tmp_path)

    duplicates = [problem for problem in planned.report.problems if problem.problem_type == "duplicate_source_identity"]
    left = [entry for entry in planned.report.left_out if entry.record.identity == {"name": "dup"}]

    assert len(duplicates) == 2
    for problem in duplicates:
        assert [(other.kind, other.identity) for other in problem.other_records] == [("LocationSite", {"name": "dup"})]
    assert len(left) == 3
    assert [entry.loaded for entry in left].count(True) == 1
    assert planned.manifest.report is not None
    assert planned.manifest.report.left_out == len(planned.report.left_out) == 4
    assert all(operation.identity != {"name": "dup"} for operation in planned.operations)
