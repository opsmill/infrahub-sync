"""Each of the nine problem types, planned from the shared problem fixture (spec 019 T010, FR-002, FR-003).

The full fixture (`Variant.ALL`) holds one record or group of records of each problem type. The
nine types give ten problem entries, because `peer_not_found_in_source` has one record through a
required relationship and one through an optional relationship (`expected.json`). The tests read
the problems from the saved plan's report (`plan/report.jsonl`, plan format 4), with the fields of
data-model.md "Plan problem".

The three tests of `test_problem_baseline.py` pin the behavior before spec 019 for three of these
types; the tasks that make this module pass rewrite or remove them.

Spec 019 T017 to T028 implement the behavior.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest

from tests.plan.conftest import PROBLEM_TYPES, TWIN_SITE_NAME
from tests.plan.problem_plan import PlannedFixture, plan_fixture, problem_source_ids

if TYPE_CHECKING:
    from tests.plan.conftest import PlanProblemFixture

pytestmark = [
    pytest.mark.usefixtures("_cache_dir"),
]

REASON_LIMIT = 512


@pytest.fixture
def _cache_dir(tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: ANN401
    monkeypatch.setenv("INFRAHUB_SYNC_CACHE_DIR", str(tmp_path / "cache"))


def _summary(problems: list[dict[str, Any]]) -> list[tuple[str, list[str], str | None]]:
    """Each problem as (kind, source IDs of the record and its other records, field), sorted."""
    return sorted(
        (problem["record"]["kind"], problem_source_ids(problem), problem.get("field")) for problem in problems
    )


def _expected(fixture: PlanProblemFixture, problem_type: str) -> list[tuple[str, list[str], str | None]]:
    return sorted(
        (problem["kind"], sorted(problem["source_ids"]), problem["field"])
        for problem in fixture.expected_problems()
        if problem["problem_type"] == problem_type
    )


def _of_type(planned: PlannedFixture, problem_type: str) -> list[dict[str, Any]]:
    return [problem for problem in planned.problems if problem["problem_type"] == problem_type]


def _planned_type(fixture: PlanProblemFixture, problem_type: str) -> list[dict[str, Any]]:
    """Plan the full fixture and return the problems of `problem_type`, checked against `expected.json`."""
    problems = _of_type(plan_fixture(fixture), problem_type)
    assert _summary(problems) == _expected(fixture, problem_type)
    for problem in problems:
        assert problem["reason"]
        assert len(problem["reason"]) <= REASON_LIMIT
    return problems


def test_the_plan_finishes_with_every_problem_and_no_write(plan_problems: PlanProblemFixture) -> None:
    """One plan lists all ten problem entries of the nine types, and nothing is written (FR-001)."""
    planned = plan_fixture(plan_problems)

    assert _summary(planned.problems) == sorted(
        (problem["kind"], sorted(problem["source_ids"]), problem["field"])
        for problem in plan_problems.expected_problems()
    )
    assert len(planned.problems) == len(plan_problems.expected_problems()) == 10
    assert sorted({problem["problem_type"] for problem in planned.problems}) == sorted(PROBLEM_TYPES)
    assert planned.manifest["report"]["problems"] == 10
    # The stand-in Infrahub client has no write method: a write would have failed the plan.
    assert planned.loaded_manifest.format_version == 4


def test_dropdown_value_not_in_choices(plan_problems: PlanProblemFixture) -> None:
    """Site `berlin`'s status `decommissioning` is not a choice; the problem lists the choices."""
    (problem,) = _planned_type(plan_problems, "dropdown_value_not_in_choices")

    choices = plan_problems.schema_record["LocationSite"]["attributes"]["status"]["choices"]
    assert problem["value"] == "decommissioning"
    assert sorted(problem["allowed"]) == sorted(choices)


def test_duplicate_source_identity(plan_problems: PlanProblemFixture) -> None:
    """Sites 4 and 5 share the mapping identity `dup`; the problem names the other record."""
    (problem,) = _planned_type(plan_problems, "duplicate_source_identity")

    assert len(problem["other_records"]) >= 1


def test_destination_identity_collision(plan_problems: PlanProblemFixture) -> None:
    """Devices 33 and 34 would form the same Infrahub human-friendly ID `edge`."""
    (problem,) = _planned_type(plan_problems, "destination_identity_collision")

    assert len(problem["other_records"]) >= 1


def test_wrong_value_type(plan_problems: PlanProblemFixture) -> None:
    """Device `core-1`'s `position` 2.5 is refused for a Number; the problem holds the value."""
    (problem,) = _planned_type(plan_problems, "wrong_value_type")

    assert problem["value"] == pytest.approx(2.5)


def test_required_peer_excluded_by_filter(plan_problems: PlanProblemFixture) -> None:
    """Interface `mgmt0` needs device 35, which the filter at `filters[0]` removed."""
    (problem,) = _planned_type(plan_problems, "required_peer_excluded_by_filter")

    assert (problem["peer"]["kind"], problem["peer"]["source_id"]) == ("DcimDevice", "35")
    assert problem["config_entry"] == "filters[0]"


def test_identities_merge_in_destination(plan_problems: PlanProblemFixture) -> None:
    """Racks 21 and 22 differ in their mapping identity, and Infrahub would hold one node for both."""
    (problem,) = _planned_type(plan_problems, "identities_merge_in_destination")

    assert len(problem["other_records"]) >= 1


def test_peer_not_found_in_source(plan_problems: PlanProblemFixture) -> None:
    """Device 36's site 997 and address 82's VRF 998 are not in the source; the peer has no identity."""
    problems = _planned_type(plan_problems, "peer_not_found_in_source")

    assert sorted(
        (problem["peer"]["kind"], problem["peer"]["source_id"], problem["peer"]["identity"]) for problem in problems
    ) == [("IpamVRF", "998", None), ("LocationSite", "997", None)]


def test_destination_identity_ambiguous(plan_problems: PlanProblemFixture) -> None:
    """Infrahub holds two `LocationSite` nodes named `twin`; the problem lists both node IDs."""
    (problem,) = _planned_type(plan_problems, "destination_identity_ambiguous")

    twins = [node["local_id"] for node in plan_problems.destination_data["LocationSite"]]
    assert all(node["name"] == TWIN_SITE_NAME for node in plan_problems.destination_data["LocationSite"])
    assert sorted(problem["destination_ids"]) == sorted(twins)


def test_transform_failed(plan_problems: PlanProblemFixture) -> None:
    """The `name` transform of VRF `q-in-q` fails; the problem names its position, never its expression."""
    (problem,) = _planned_type(plan_problems, "transform_failed")

    assert problem["config_entry"] == "transforms[0]"
    assert problem["value"] is None
    assert "{{" not in problem["reason"]
