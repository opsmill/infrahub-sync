"""The plan report's order does not depend on the load order (review: stable sort order).

`PlanReport.ordered` sorts problems by type, kind, identity and source ID, left-out records by
kind, identity and source ID, and kept records by kind, identity and relationship. Entries that
tie on those keys, such as two problems of one record on two fields, are ordered by the rest of
the entry, so the report bytes, and the report digest that the plan checksum covers, are the same for every
load order.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from infrahub_sync.plan.canonical import canonical_json_bytes
from infrahub_sync.plan.models import KeptRecord, LeftOutRecord, PlanProblem, PlanRecordReference, PlanReport
from infrahub_sync.plan.writer import PLAN_DIR_NAME, REPORT_FILE_NAME
from tests.plan.problem_fixtures import Variant, source_records
from tests.plan.problem_plan import plan_fixture

if TYPE_CHECKING:
    from pathlib import Path

DEVICE = PlanRecordReference(kind="DcimDevice", identity={"name": "core-1"}, source_id="30")


def _problem(field: str, value: object) -> PlanProblem:
    return PlanProblem(problem_type="wrong_value_type", record=DEVICE, field=field, value=value, reason="r")


def _bytes(report: PlanReport) -> bytes:
    return b"".join(canonical_json_bytes(line) + b"\n" for line in report.entries())


def test_entries_that_tie_on_the_sort_keys_are_ordered_by_the_rest_of_the_entry() -> None:
    problems = [_problem("position", 2.5), _problem("serial", 7)]
    left_out = [
        LeftOutRecord(record=DEVICE, cause="depends_on_left_out", depends_on=DEVICE, relationship=name, loaded=True)
        for name in ("site", "rack")
    ]
    peers = [PlanRecordReference(kind="IpamVRF", source_id=source_id) for source_id in ("10", "11")]
    kept = [KeptRecord(record=DEVICE, cause="peer_left_out", relationship="vrf", left_out_peer=peer) for peer in peers]

    forward = PlanReport(problems=problems, left_out=left_out, kept=kept)
    backward = PlanReport(problems=problems[::-1], left_out=left_out[::-1], kept=kept[::-1])

    assert _bytes(forward) == _bytes(backward)


def test_two_load_orders_give_identical_report_bytes_and_checksum(tmp_path: Path) -> None:
    """Every kind is loaded in reverse order, except the two `dup` sites.

    Of two records with one source identity, the store keeps the one loaded first, and the
    report says which one was loaded (`loaded`), so that pair keeps its order.
    """
    records = source_records(Variant.ALL)
    reversed_records = {kind: items[::-1] for kind, items in records.items()}
    sites = reversed_records["LocationSite"]
    duplicates = [index for index, site in enumerate(sites) if site["name"] == "dup"]
    sites[duplicates[0]], sites[duplicates[1]] = sites[duplicates[1]], sites[duplicates[0]]

    first = plan_fixture(tmp_path / "first", records=records)
    second = plan_fixture(tmp_path / "second", records=reversed_records)

    report = PLAN_DIR_NAME + "/" + REPORT_FILE_NAME
    assert (first.run_dir / report).read_bytes() == (second.run_dir / report).read_bytes()
    # The checksum covers the report through this object. The source snapshot files, which it
    # also covers, keep the rows in load order, so the whole checksum is not compared here.
    assert first.manifest.report == second.manifest.report
