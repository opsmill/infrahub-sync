"""A second plan on unchanged data is empty (spec 019 User Story 1, T010 and T011).

Each test plans, applies the plan with `ApplyingDestination` (`tests/plan/second_plan.py`), and
plans again over what the apply wrote. The second plan therefore compares the source with the
destination the apply produced, not with the source records.

- T010 (FR-022, FR-026): LAG member interfaces, prefixes and IP addresses without a VRF, and
  tagged VLANs give a second plan with zero operations; three plans in a row give the same
  operations digest and the same plan report.
- T011 (FR-025, FR-026): a first plan whose report holds problems, records left out and kept
  records (`Variant.ALL` of `tests/data/plan_problems/`) gives a second plan with zero
  operations and the same plan report.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest

from infrahub_sync.plan.digest import operations_digest
from tests.plan.conftest import Variant, load_plan_problem_fixture
from tests.plan.problem_plan import plan_fixture
from tests.plan.second_plan import (
    ApplyingDestination,
    kinds_of,
    load_second_plan_fixture,
    second_plan_destination,
    second_plan_records,
)

if TYPE_CHECKING:
    from pathlib import Path

    from tests.plan.conftest import PlanProblemFixture
    from tests.plan.problem_plan import PlannedFixture

FIRST_RUN_ID = "20261116T120000-01900010"
BEFORE_RUN_IDS = ("20261116T130000-01900010", "20261116T140000-01900010", "20261116T150000-01900010")
AFTER_RUN_IDS = ("20261116T160000-01900010", "20261116T170000-01900010", "20261116T180000-01900010")

# The edge cases of FR-022, each planned with the devices its records refer to.
EDGE_CASES = {
    "lag_members": ("devices", "lag"),
    "prefixes_and_addresses_without_vrf": ("devices", "no_vrf"),
    "tagged_vlans": ("devices", "tagged_vlans"),
    "all_groups": ("devices", "lag", "no_vrf", "tagged_vlans"),
}


@pytest.fixture
def cache_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep the plan cache under `tmp_path`."""
    monkeypatch.setenv("INFRAHUB_SYNC_CACHE_DIR", str(tmp_path / "cache"))


def _apply(planned: PlannedFixture, nodes: dict[str, list[dict[str, Any]]]) -> dict[str, list[dict[str, Any]]]:
    """Apply `planned` to a stand-in holding `nodes`; return what the stand-in holds afterwards.

    The apply confirms that the records the plan leaves out were reviewed, as an operator does.
    """
    return ApplyingDestination.for_plan(planned, nodes).apply(planned, confirm_left_out=bool(planned.left_out))


def _replans(
    fixture: PlanProblemFixture,
    *,
    records: dict[str, list[dict[str, Any]]] | None,
    destination: dict[str, list[dict[str, Any]]],
    run_ids: tuple[str, ...],
    variant: Variant = Variant.ALL,
) -> list[PlannedFixture]:
    """Plan the same source and destination three times in a row."""
    return [
        plan_fixture(fixture, variant, records=records, destination=destination, run_id=run_id) for run_id in run_ids
    ]


@pytest.mark.usefixtures("cache_dir")
@pytest.mark.parametrize("groups", list(EDGE_CASES.values()), ids=list(EDGE_CASES))
def test_unchanged_data_gives_an_empty_second_plan(groups: tuple[str, ...]) -> None:
    fixture = load_second_plan_fixture()
    records = second_plan_records(fixture, *groups)
    first = plan_fixture(fixture, records=records, destination=second_plan_destination(fixture), run_id=FIRST_RUN_ID)
    assert first.operations, "fixture error: the first plan creates nothing"
    assert all(operation.action == "create" for operation in first.operations), kinds_of(first.operations)

    destination = _apply(first, second_plan_destination(fixture))
    second = plan_fixture(fixture, records=second_plan_records(fixture, *groups), destination=destination)

    assert kinds_of(second.operations) == []
    assert second.report == []


@pytest.mark.usefixtures("cache_dir")
@pytest.mark.parametrize("groups", list(EDGE_CASES.values()), ids=list(EDGE_CASES))
def test_three_plans_in_a_row_give_the_same_digest_and_report(groups: tuple[str, ...]) -> None:
    """FR-026: on unchanged data, before and after the apply."""
    fixture = load_second_plan_fixture()

    before = _replans(
        fixture,
        records=second_plan_records(fixture, *groups),
        destination=second_plan_destination(fixture),
        run_ids=BEFORE_RUN_IDS,
    )
    assert len({operations_digest(planned.operations) for planned in before}) == 1
    assert len({planned.report_bytes for planned in before}) == 1

    destination = _apply(before[0], second_plan_destination(fixture))
    after = _replans(
        fixture, records=second_plan_records(fixture, *groups), destination=destination, run_ids=AFTER_RUN_IDS
    )
    assert {operations_digest(planned.operations) for planned in after} == {operations_digest([])}
    assert len({planned.report_bytes for planned in after}) == 1


@pytest.mark.usefixtures("cache_dir")
def test_a_first_plan_with_left_out_and_kept_records_gives_an_empty_second_plan() -> None:
    """T011: FR-025, FR-026 on `Variant.ALL` of `tests/data/plan_problems/`."""
    fixture = load_plan_problem_fixture()
    nodes = fixture.destination_records(fixture.source_records(Variant.ALL))
    first = plan_fixture(fixture, Variant.ALL, destination=nodes, run_id=FIRST_RUN_ID)
    assert first.left_out, "fixture error: the first plan leaves no record out"
    assert first.kept, "fixture error: the first plan keeps no record"

    destination = _apply(first, nodes)
    second = _replans(fixture, records=None, destination=destination, run_ids=AFTER_RUN_IDS)

    assert [kinds_of(planned.operations) for planned in second] == [[], [], []]
    assert {planned.report_bytes for planned in second} == {first.report_bytes}
