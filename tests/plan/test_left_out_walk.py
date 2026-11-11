"""The left-out records, the dependency walk and the kept records (spec 019 T011, FR-005, FR-006, FR-008).

A record with a problem is left out of the plan, and so is every record that depends on it
through a required relationship, at any depth. A record that refers to a left-out record, or to a
peer that is not in the source, through an optional relationship is kept without that
relationship. The tests read the left-out and kept records from the saved plan's report
(data-model.md "Left-out record" and "Kept record") and the operations from the saved plan.

Also covered, the edge cases of spec.md: both records of a duplicate pair are left out, a left-out
record that Infrahub already holds gets no update and no delete, and a plan whose every record is
left out finishes with no operation.

Spec 019 T017 to T028 implement the behavior. Until then each test is an expected failure: the
fixture source needs the base-class read loop, and `DiffSyncMixin.model_loader` raises
`NotImplementedError`.
"""

from __future__ import annotations

import copy
from typing import TYPE_CHECKING, Any

import pytest

from infrahub_sync.plan.errors import DestinationIdentityCollisionError
from tests.plan.conftest import TWIN_SITE_NAME, Variant
from tests.plan.problem_plan import operations_of, plan_fixture, record_key

if TYPE_CHECKING:
    from pathlib import Path

    from tests.plan.conftest import PlanProblemFixture

pytestmark = [
    pytest.mark.usefixtures("_cache_dir"),
    pytest.mark.xfail(
        strict=True,
        # The variants without an identity group plan without the pass and fail an assertion.
        raises=(DestinationIdentityCollisionError, AssertionError),
        reason=(
            "spec 019 T028 not implemented: Potenda.write_plan does not run the problem pass yet, so it "
            "still refuses identity groups and writes an empty report"
        ),
    ),
]


@pytest.fixture
def _cache_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("INFRAHUB_SYNC_CACHE_DIR", str(tmp_path / "cache"))


def _reference(reference: dict[str, Any] | None) -> tuple[str, str] | None:
    return None if reference is None else (reference["kind"], reference["source_id"])


def _left_out_summary(entries: list[dict[str, Any]]) -> list[tuple[Any, ...]]:
    """Each left-out record as (kind, source ID, cause, problem types, depends on, relationship), sorted."""
    return sorted(
        (
            *record_key(entry),
            entry["cause"],
            sorted(entry.get("problem_types") or []),
            _reference(entry.get("depends_on")),
            entry.get("relationship"),
        )
        for entry in entries
    )


def _site(fixture: PlanProblemFixture, source_id: int) -> dict[str, Any]:
    sites = fixture.source_records(Variant.ALL)["LocationSite"]
    return copy.deepcopy(next(site for site in sites if site["id"] == source_id))


def test_the_walk_follows_required_relationships_at_any_depth(plan_problems: PlanProblemFixture) -> None:
    """`core-1` has a problem; its interface `eth0` and that interface's `eth0.100` are left out too."""
    planned = plan_fixture(plan_problems, Variant.DEPENDENCY_CHAIN)

    assert _left_out_summary(planned.left_out) == [
        ("DcimDevice", "30", "has_problem", ["wrong_value_type"], None, None),
        ("InterfacePhysical", "40", "depends_on_left_out", [], ("DcimDevice", "30"), "device"),
        ("InterfaceVirtual", "71", "depends_on_left_out", [], ("InterfacePhysical", "40"), "parent"),
    ]
    report = planned.manifest["report"]
    assert (report["left_out"], report["left_out_with_problem"], report["left_out_dependent"]) == (3, 1, 2)
    operations = planned.operations
    for kind in ("DcimDevice", "InterfacePhysical", "InterfaceVirtual"):
        assert list(operations_of(operations, kind)) == []
    assert [operation.identity for operation in operations_of(operations, "LocationSite")] == [{"name": "paris"}]


def test_the_dependents_of_the_full_fixture_are_left_out(plan_problems: PlanProblemFixture) -> None:
    """The five interfaces of `core-1` are left out through `device`, as `expected.json` lists."""
    planned = plan_fixture(plan_problems)

    dependents = [entry for entry in planned.left_out if entry["cause"] == "depends_on_left_out"]
    assert sorted((*record_key(entry), _reference(entry["depends_on"])) for entry in dependents) == sorted(
        (item["kind"], item["source_id"], (item["depends_on"]["kind"], item["depends_on"]["source_id"]))
        for item in plan_problems.expected_left_out_dependents()
    )
    assert {entry["relationship"] for entry in dependents} == {"device"}


def test_the_two_kept_cases(plan_problems: PlanProblemFixture) -> None:
    """Address 81 keeps its create without `device` (`core-1` is left out); address 82 without `vrf` (998 is missing)."""
    planned = plan_fixture(plan_problems)

    assert sorted(
        (*record_key(entry), entry["cause"], entry["relationship"], _reference(entry["left_out_peer"]))
        for entry in planned.kept
    ) == sorted(
        (
            item["kind"],
            item["source_id"],
            item["cause"],
            item["relationship"],
            (item["peer"]["kind"], item["peer"]["source_id"]),
        )
        for item in plan_problems.expected_kept()
    )
    missing_vrf = next(entry for entry in planned.kept if entry["cause"] == "peer_not_found_in_source")
    assert missing_vrf["left_out_peer"]["identity"] is None
    assert ("IpamIPAddress", "81") not in {record_key(entry) for entry in planned.left_out}
    assert ("IpamIPAddress", "82") not in {record_key(entry) for entry in planned.left_out}

    addresses = {
        operation.identity["address"]: operation for operation in operations_of(planned.operations, "IpamIPAddress")
    }
    assert "device" not in {reference.field for reference in addresses["10.0.0.5/32"].relationships or []}
    assert "vrf" not in {reference.field for reference in addresses["10.0.0.9/32"].relationships or []}
    assert planned.manifest["report"]["kept"] == 2


def test_a_record_with_two_problems_is_left_out_once(plan_problems: PlanProblemFixture) -> None:
    """Site 4 has a status outside the choices and shares its identity with site 5: one left-out entry."""
    first = _site(plan_problems, 4)
    first["status"] = "decommissioning"
    planned = plan_fixture(plan_problems, records={"LocationSite": [first, _site(plan_problems, 5)]}, destination={})

    assert sorted(problem["problem_type"] for problem in planned.problems) == [
        "dropdown_value_not_in_choices",
        "duplicate_source_identity",
    ]
    site_four = [entry for entry in planned.left_out if record_key(entry) == ("LocationSite", "4")]
    assert len(site_four) == 1
    assert sorted(site_four[0]["problem_types"]) == ["dropdown_value_not_in_choices", "duplicate_source_identity"]
    report = planned.manifest["report"]
    assert (report["problems"], report["left_out"], report["left_out_with_problem"]) == (2, 2, 2)
    assert planned.operations == []


def test_both_records_of_a_duplicate_pair_are_left_out(plan_problems: PlanProblemFixture) -> None:
    """Sites 4 and 5 (`dup`), devices 33 and 34 (`edge`) and racks 21 and 22 are all left out."""
    planned = plan_fixture(plan_problems)

    left_out = {record_key(entry) for entry in planned.left_out if entry["cause"] == "has_problem"}
    for pair in (("LocationSite", "4", "5"), ("DcimDevice", "33", "34"), ("LocationRack", "21", "22")):
        kind, *ids = pair
        assert {(kind, source_id) for source_id in ids} <= left_out
    assert [
        operation
        for operation in operations_of(planned.operations, "LocationSite")
        if operation.identity == {"name": "dup"}
    ] == []


def test_a_left_out_record_that_infrahub_holds_gets_no_update_or_delete(plan_problems: PlanProblemFixture) -> None:
    """The `comms-closet` rack and the `twin` sites exist in Infrahub; the plan neither updates nor deletes them."""
    planned = plan_fixture(plan_problems)

    racks = list(operations_of(planned.operations, "LocationRack"))
    assert [operation for operation in racks if operation.identity.get("name") == "comms-closet"] == []
    sites = list(operations_of(planned.operations, "LocationSite"))
    assert [operation for operation in sites if operation.identity.get("name") == TWIN_SITE_NAME] == []
    assert [operation for operation in sites if operation.action == "delete"] == []


def test_a_plan_whose_every_record_is_left_out_has_no_operation(plan_problems: PlanProblemFixture) -> None:
    """The only source record, site `berlin`, has a problem: the plan finishes with no operation."""
    planned = plan_fixture(plan_problems, records={"LocationSite": [_site(plan_problems, 3)]}, destination={})

    assert planned.operations == []
    assert _left_out_summary(planned.left_out) == [
        ("LocationSite", "3", "has_problem", ["dropdown_value_not_in_choices"], None, None)
    ]
    assert planned.manifest["report"]["left_out"] == 1
    assert planned.manifest["operations_count"] == 0
