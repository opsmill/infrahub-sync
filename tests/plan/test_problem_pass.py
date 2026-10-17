"""The problem pass lists every problem of the fixture in one plan (spec 009 T031, FR-003).

The T001 fixture holds one record of each of the nine problem types. `peer_not_found_in_source`
is found twice: once for the interface `eth9`, whose required `device` the source does not
return, and once for the IP address `10.0.0.9/32`, whose optional `vrf` the source does not
return. The second record is kept without that relationship (FR-005), so the fixture lists ten
problem entries of nine types.
"""

from __future__ import annotations

import copy
import re
from typing import TYPE_CHECKING, Any

import pytest

from infrahub_sync.plan.models import PROBLEM_TYPES, REPORT_REASON_LIMIT, REPORT_VALUE_LIMIT
from infrahub_sync.plan.writer import PLAN_DIR_NAME, REPORT_FILE_NAME
from tests.plan.problem_fixtures import (
    EXCLUDED_DEVICE_ROLE,
    MISSING_DEVICE_SOURCE_ID,
    MISSING_VRF_SOURCE_ID,
    PROBLEM_DEVICE_SOURCE_ID,
    SITE_STATUS_CHOICES,
    UNMAPPED_VRF_SOURCE_ID,
    Variant,
    destination_records,
    source_records,
    twin_site_nodes,
)
from tests.plan.problem_plan import PlannedFixture, plan_fixture

if TYPE_CHECKING:
    from pathlib import Path

    from infrahub_sync.plan.models import PlanProblem


@pytest.fixture(name="planned")
def _planned(tmp_path: Path) -> PlannedFixture:
    return plan_fixture(tmp_path / "run")


def _of_type(planned: PlannedFixture, problem_type: str) -> list[PlanProblem]:
    return [problem for problem in planned.report.problems if problem.problem_type == problem_type]


def test_every_problem_type_is_listed(planned: PlannedFixture) -> None:
    by_type = {name: len(_of_type(planned, name)) for name in PROBLEM_TYPES}

    assert by_type == {
        "dropdown_value_not_in_choices": 1,
        "duplicate_source_identity": 1,
        "destination_identity_collision": 1,
        "wrong_value_type": 1,
        "required_peer_excluded_by_filter": 1,
        "identities_merge_in_destination": 1,
        # `eth9` (required, left out) and `10.0.0.9/32` (optional, kept).
        "peer_not_found_in_source": 2,
        # Infrahub holds two `LocationSite twin` nodes.
        "destination_identity_ambiguous": 1,
        # The VRF name transform knows no `q-in-q`.
        "transform_failed": 1,
    }


def test_the_dropdown_problem_names_the_value_and_the_choices(planned: PlannedFixture) -> None:
    (problem,) = _of_type(planned, "dropdown_value_not_in_choices")

    assert (problem.record.kind, problem.record.identity, problem.record.source_id) == (
        "LocationSite",
        {"name": "berlin"},
        "3",
    )
    assert problem.field == "status"
    assert problem.value == "decommissioning"
    assert problem.allowed == list(SITE_STATUS_CHOICES)
    assert problem.peer is None
    assert problem.other_records == []


def test_the_wrong_value_type_problem_names_the_field_and_value(planned: PlannedFixture) -> None:
    (problem,) = _of_type(planned, "wrong_value_type")

    assert problem.record.kind == "DcimDevice"
    assert problem.record.source_id == PROBLEM_DEVICE_SOURCE_ID
    assert problem.record.identity == {"name": "core-1", "serial": "C1"}
    assert (problem.field, problem.value, problem.allowed) == ("position", 2.5, None)


@pytest.mark.parametrize(
    ("problem_type", "kind", "source_ids"),
    [
        ("duplicate_source_identity", "LocationSite", {"4", "5"}),
        ("destination_identity_collision", "DcimDevice", {"33", "34"}),
        ("identities_merge_in_destination", "LocationRack", {"21", "22"}),
    ],
)
def test_an_identity_problem_names_the_other_records(
    planned: PlannedFixture, problem_type: str, kind: str, source_ids: set[str]
) -> None:
    (problem,) = _of_type(planned, problem_type)

    assert problem.record.kind == kind
    assert len(problem.other_records) >= 1
    assert {problem.record.source_id, *(other.source_id for other in problem.other_records)} == source_ids
    assert all(other.kind == kind for other in problem.other_records)
    assert problem.allowed is None
    assert problem.peer is None


def test_the_identity_group_found_by_two_checks_is_listed_once(planned: PlannedFixture) -> None:
    """The two `edge` devices collide on the HFID and merge on the closest key: one group, one problem."""
    edge = [
        problem
        for problem in planned.report.problems
        if problem.record.kind == "DcimDevice" and problem.problem_type != "wrong_value_type"
    ]

    assert [problem.problem_type for problem in edge] == ["destination_identity_collision"]


def test_the_peer_problems_name_the_peer(planned: PlannedFixture) -> None:
    (excluded,) = _of_type(planned, "required_peer_excluded_by_filter")
    by_record = {problem.record.kind: problem for problem in _of_type(planned, "peer_not_found_in_source")}

    assert (excluded.record.kind, excluded.record.source_id, excluded.field) == ("InterfacePhysical", "60", "device")
    assert excluded.peer is not None
    assert (excluded.peer.kind, excluded.peer.source_id) == ("DcimDevice", "35")
    device = by_record["InterfacePhysical"]
    assert (device.record.source_id, device.field) == ("61", "device")
    assert device.peer is not None
    assert (device.peer.kind, device.peer.identity, device.peer.source_id) == (
        "DcimDevice",
        None,
        MISSING_DEVICE_SOURCE_ID,
    )
    vrf = by_record["IpamIPAddress"]
    assert (vrf.record.identity, vrf.field) == ({"address": "10.0.0.9/32"}, "vrf")
    assert vrf.peer is not None
    assert (vrf.peer.kind, vrf.peer.identity, vrf.peer.source_id) == ("IpamVRF", None, MISSING_VRF_SOURCE_ID)


def test_the_filter_problem_names_the_filter_by_its_position_without_its_value(planned: PlannedFixture) -> None:
    """Review #13: the reason names the filter that removed the peer, never the filter's value."""
    (excluded,) = _of_type(planned, "required_peer_excluded_by_filter")

    assert excluded.config_entry == "filters[0]"
    assert "the filter filters[0] of the DcimDevice mapping entry removed" in excluded.reason
    assert EXCLUDED_DEVICE_ROLE not in excluded.reason


_TEMPLATES = {
    "dropdown_value_not_in_choices": r"^\w+ record source ID \d+: the value .+ of \w+ is not one of the choices "
    r"of the Dropdown attribute: .+\.$",
    "duplicate_source_identity": r"^\w+ record source ID \d+ has the same source identity as .+; the plan "
    r"cannot tell which record is correct\.$",
    "destination_identity_collision": r"^\w+ record source ID \d+ and .+ would form the same Infrahub "
    r"human-friendly ID; the plan cannot tell which record is correct\.$",
    "wrong_value_type": r"^\w+ record source ID \d+: the value .+ of \w+ has the wrong type \(\w+\)\.$",
    "required_peer_excluded_by_filter": r"^\w+ record source ID \d+: the required relationship \w+ refers to "
    r"\w+ record source ID \d+, which the filter filters\[\d+\] of the \w+ mapping entry removed\.$",
    "identities_merge_in_destination": r"^\w+ record source ID \d+ and .+ have different source identities "
    r"that Infrahub would store as one node\.$",
    "peer_not_found_in_source": r"^\w+ record source ID \d+: the relationship \w+ refers to \w+ record source "
    r"ID \d+, which the source does not return\.$",
    "destination_identity_ambiguous": r"^Infrahub holds \d+ \w+ objects with the identity .+; the plan cannot tell "
    r"which one \w+ record (source ID \d+|with no source ID) maps to, so it plans no operation for that identity\.$",
    "transform_failed": r"^\w+ record source ID \d+: the transform transforms\[\d+\] of the \w+ mapping entry, which "
    r"writes \w+, failed for this record, so the record is not loaded\.$",
}


def test_every_reason_is_rendered_from_the_fixed_template_of_its_type(planned: PlannedFixture) -> None:
    for problem in planned.report.problems:
        assert len(problem.reason) <= REPORT_REASON_LIMIT
        assert re.fullmatch(_TEMPLATES[problem.problem_type], problem.reason), problem.reason


def test_a_long_value_is_limited_in_the_entry_and_in_the_reason(tmp_path: Path) -> None:
    records = source_records(Variant.NO_PROBLEMS)
    records["LocationSite"].append({"id": 9, "name": "long", "status": "x" * 2000})

    planned = plan_fixture(tmp_path / "run", records=records)
    (problem,) = planned.report.problems

    assert problem.value == "x" * REPORT_VALUE_LIMIT
    assert len(problem.reason) <= REPORT_REASON_LIMIT


def test_a_record_with_two_problems_has_two_entries_and_is_left_out_once(tmp_path: Path) -> None:
    """`dup` (source ID 4) is a duplicate and also holds a status that is not a choice."""
    records = source_records(Variant.ALL)
    first_dup = next(record for record in records["LocationSite"] if record["id"] == 4)
    first_dup["status"] = "decommissioning"

    planned = plan_fixture(tmp_path / "run", records=records)
    entries = [
        problem
        for problem in planned.report.problems
        if problem.record.source_id == "4" and problem.record.kind == "LocationSite"
    ]
    entries += [
        problem
        for problem in planned.report.problems
        if any(other.source_id == "4" and other.kind == "LocationSite" for other in problem.other_records)
    ]
    left_out = [item for item in planned.report.left_out if item.record == entries[0].record]

    assert sorted(problem.problem_type for problem in entries) == [
        "dropdown_value_not_in_choices",
        "duplicate_source_identity",
    ]
    assert len(left_out) == 1
    assert left_out[0].problem_types == ["dropdown_value_not_in_choices", "duplicate_source_identity"]
    assert planned.manifest.report is not None
    assert planned.manifest.report.left_out == len(planned.report.left_out)


def test_a_plan_without_problems_leaves_out_nothing(tmp_path: Path) -> None:
    """Scenario 6: the report is empty and the report file holds zero bytes."""
    planned = plan_fixture(tmp_path / "run", Variant.NO_PROBLEMS)

    assert planned.report.problems == []
    assert planned.report.left_out == []
    assert planned.report.kept == []
    assert planned.manifest.report is not None
    assert planned.manifest.report.left_out == 0
    assert planned.manifest.report.problems == 0
    assert (planned.run_dir / PLAN_DIR_NAME / REPORT_FILE_NAME).read_bytes() == b""


def _fixed(records: dict[str, list[dict[str, Any]]]) -> dict[str, list[dict[str, Any]]]:
    """The fixture records with each problem corrected at the source."""
    fixed = copy.deepcopy(records)
    by_id = {record["id"]: record for items in fixed.values() for record in items}
    by_id[3]["status"] = "planned"
    by_id[5]["name"] = "dup-2"
    by_id[34]["name"] = "edge-2"
    by_id[int(PROBLEM_DEVICE_SOURCE_ID)]["position"] = 2
    by_id[60]["device"] = {"id": 31}
    by_id[61]["device"] = {"id": 31}
    by_id[22]["name"] = "comms-closet-2"
    by_id[82]["vrf"] = {"id": 10}
    by_id[int(UNMAPPED_VRF_SOURCE_ID)]["name"] = "mgmt"
    return fixed


def test_after_the_data_is_fixed_the_left_out_records_are_planned(tmp_path: Path) -> None:
    """Scenario 5, FR-009: the next plan holds the formerly left-out records as creates or updates."""
    first = plan_fixture(tmp_path / "first")
    left_out_ids = {(item.record.kind, item.record.source_id) for item in first.report.left_out}

    records = _fixed(source_records(Variant.ALL))
    destination = destination_records(records)
    # The team removed the second Infrahub node of the site `twin`.
    destination["LocationSite"] = twin_site_nodes()[:1]
    second = plan_fixture(tmp_path / "second", records=records, destination=destination)
    planned_payloads = {
        (operation.kind, operation.payload.get("name") or operation.payload.get("address"))
        for operation in second.operations
        if operation.action in {"create", "update"} and operation.payload is not None
    }

    assert second.report.problems == []
    assert second.report.left_out == []
    assert ("DcimDevice", PROBLEM_DEVICE_SOURCE_ID) in left_out_ids
    for expected in [
        ("LocationSite", "berlin"),
        ("LocationSite", "dup"),
        ("LocationSite", "dup-2"),
        ("DcimDevice", "core-1"),
        ("DcimDevice", "edge"),
        ("DcimDevice", "edge-2"),
        ("InterfacePhysical", "mgmt0"),
        ("InterfacePhysical", "eth9"),
        ("LocationRack", "comms-closet"),
        ("LocationRack", "comms-closet-2"),
        ("IpamVRF", "mgmt"),
    ]:
        assert expected in planned_payloads, expected
    interfaces = [
        operation
        for operation in second.operations
        if operation.kind == "InterfacePhysical"
        and operation.identity["device"]["identity"] == {"name": "core-1", "serial": "C1"}
    ]
    assert len(interfaces) == 5


def test_the_destination_receives_no_write_during_the_plan(planned: PlannedFixture) -> None:
    """FR-004: the plan stage reads only."""
    assert planned.destination.writes == []
