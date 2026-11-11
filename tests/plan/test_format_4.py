"""Saved plan format 4 (spec 019 T013, FR-031, contracts/plan-artifact-format-4.md).

Format 4 is format 3 plus `plan/report.jsonl` and a `report` object in the manifest:

- the report holds one canonical JSON object per line (ADR 0001), problems first, then left-out
  records, then kept records, each group in the stable order of data-model.md "Plan report"
- the manifest records the report's SHA-256 digest, its size and its counts, and the plan
  checksum covers the manifest, so it covers the report
- a report that does not match the manifest is a `PlanVerificationError`
- a plan with no problem still writes an empty report and a `report` object with zeros
- a run without a destination schema snapshot records `dropdown_values_checked: false`
- the format 3 fields stay: `destination_id` on updates, `lineage` and `plan/last_changes.jsonl`

Spec 019 T027 (and T017 to T028 for the planned fixture) implements the behavior. Until then each
test is an expected failure: the fixture source needs the base-class read loop
(`NotImplementedError`), and the writer writes format 3 (`AssertionError` on the format version).
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

import pytest

from infrahub_sync.plan.canonical import canonical_json_bytes
from infrahub_sync.plan.checksum import compute_plan_checksum
from infrahub_sync.plan.errors import PlanVerificationError
from infrahub_sync.plan.models import LineageAccountRecord, LineageNodeRecord, PlanLineageRecord
from infrahub_sync.plan.reader import load_plan_artifact
from infrahub_sync.plan.review import resolve_run_directory
from infrahub_sync.plan.writer import (
    LAST_CHANGES_FILE_NAME,
    MANIFEST_FILE_NAME,
    OPERATIONS_FILE_NAME,
    PLAN_DIR_NAME,
    write_plan_artifact,
)
from tests.plan.artifact_fixtures import CONFIG_VERSION, RUN_ID, SYNC_NAME
from tests.plan.conftest import Variant
from tests.plan.problem_plan import REPORT_FILE_NAME, plan_fixture, report_digest
from tests.plan.test_last_changes import _update_operations

if TYPE_CHECKING:
    from pathlib import Path

    from tests.plan.conftest import PlanProblemFixture
    from tests.plan.problem_plan import PlannedFixture

NOT_IMPLEMENTED = pytest.mark.xfail(
    strict=True,
    raises=NotImplementedError,
    reason="spec 019 US1 not implemented: DiffSyncMixin.model_loader has no default read loop (T017)",
)

ZERO_COUNTS = {"problems": 0, "left_out": 0, "left_out_with_problem": 0, "left_out_dependent": 0, "kept": 0}


@pytest.fixture(autouse=True)
def _cache_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("INFRAHUB_SYNC_CACHE_DIR", str(tmp_path / "cache"))


def _plan_dir(planned: PlannedFixture) -> Path:
    return planned.run_dir / PLAN_DIR_NAME


def _identity_bytes(record: dict[str, Any]) -> bytes:
    return b"" if record["identity"] is None else canonical_json_bytes(record["identity"])


def _order_key(line: dict[str, Any]) -> tuple[Any, ...]:
    """The stable order of data-model.md "Plan report", ties by the line's canonical bytes."""
    record = line["record"]
    group = ("problem", "left_out", "kept").index(line["entry"])
    if line["entry"] == "problem":
        key: tuple[Any, ...] = (
            line["problem_type"],
            record["kind"],
            _identity_bytes(record),
            record["source_id"] or "",
        )
    elif line["entry"] == "left_out":
        key = (record["kind"], _identity_bytes(record), record["source_id"] or "")
    else:
        key = (record["kind"], _identity_bytes(record), line["relationship"])
    return (group, *key, canonical_json_bytes(line))


@NOT_IMPLEMENTED
def test_the_manifest_is_format_4_and_records_the_report(plan_problems: PlanProblemFixture) -> None:
    """`report` holds the digest, the size and the counts of `report.jsonl`."""
    planned = plan_fixture(plan_problems)

    assert planned.manifest["format_version"] == 4
    assert sorted(path.name for path in _plan_dir(planned).iterdir()) == sorted(
        # The Infrahub adapter keeps last changes, so the format 3 file `last_changes.jsonl` stays.
        [LAST_CHANGES_FILE_NAME, MANIFEST_FILE_NAME, OPERATIONS_FILE_NAME, REPORT_FILE_NAME]
    )
    left_out = planned.left_out
    assert planned.manifest["report"] == {
        "digest": report_digest(planned.report_bytes),
        "size": len(planned.report_bytes),
        "problems": len(planned.problems),
        "left_out": len(left_out),
        "left_out_with_problem": sum(1 for entry in left_out if entry["cause"] == "has_problem"),
        "left_out_dependent": sum(1 for entry in left_out if entry["cause"] == "depends_on_left_out"),
        "kept": len(planned.kept),
    }
    assert planned.manifest["report"]["problems"] == 10
    assert "dropdown_values_checked" not in planned.manifest
    assert planned.loaded_manifest.format_version == 4


@NOT_IMPLEMENTED
def test_the_report_is_canonical_and_in_the_stable_order(plan_problems: PlanProblemFixture) -> None:
    """Every line is in the canonical encoding, and the lines follow the order of data-model.md."""
    planned = plan_fixture(plan_problems)

    raw_lines = planned.report_bytes.splitlines()
    assert raw_lines
    assert all(raw == canonical_json_bytes(json.loads(raw)) for raw in raw_lines)
    assert planned.report == sorted(planned.report, key=_order_key)
    assert planned.report_bytes.endswith(b"\n")


@NOT_IMPLEMENTED
def test_the_report_does_not_depend_on_the_read_order(plan_problems: PlanProblemFixture) -> None:
    """The same records read in reverse order give the same report bytes.

    The duplicate pair `dup` (sites 4 and 5) is left out of the comparison: the store keeps the
    record read first, so which record the problem names depends on the read order by design.
    """
    records = plan_problems.source_records(Variant.ALL)
    records["LocationSite"] = [site for site in records["LocationSite"] if site["id"] not in {4, 5}]
    reversed_records = {kind: list(reversed(items)) for kind, items in records.items()}

    first = plan_fixture(plan_problems, records=records, run_id="20261111T120000-01900091")
    second = plan_fixture(plan_problems, records=reversed_records, run_id="20261111T120000-01900092")

    assert first.report
    assert second.report_bytes == first.report_bytes


@NOT_IMPLEMENTED
def test_a_report_that_does_not_match_its_digest_is_refused(plan_problems: PlanProblemFixture) -> None:
    """One changed byte in `report.jsonl` is a `PlanVerificationError`."""
    planned = plan_fixture(plan_problems)
    report_path = _plan_dir(planned) / REPORT_FILE_NAME
    report_path.write_bytes(planned.report_bytes.replace(b"has_problem", b"has_Problem", 1))

    with pytest.raises(PlanVerificationError):
        load_plan_artifact(planned.run_dir)


@NOT_IMPLEMENTED
def test_a_missing_report_is_refused(plan_problems: PlanProblemFixture) -> None:
    """A format 4 plan without `report.jsonl` is a `PlanVerificationError`."""
    planned = plan_fixture(plan_problems)
    (_plan_dir(planned) / REPORT_FILE_NAME).unlink()

    with pytest.raises(PlanVerificationError):
        load_plan_artifact(planned.run_dir)


@NOT_IMPLEMENTED
def test_a_left_out_count_that_does_not_match_the_report_is_refused(plan_problems: PlanProblemFixture) -> None:
    """A manifest whose `report.left_out` differs from the report's lines is refused, even with a valid checksum."""
    planned = plan_fixture(plan_problems)
    plan_dir = _plan_dir(planned)
    manifest = json.loads((plan_dir / MANIFEST_FILE_NAME).read_bytes())
    manifest["report"]["left_out"] += 1
    manifest["plan_checksum"] = compute_plan_checksum(manifest, (plan_dir / OPERATIONS_FILE_NAME).read_bytes())
    (plan_dir / MANIFEST_FILE_NAME).write_bytes(canonical_json_bytes(manifest))

    with pytest.raises(PlanVerificationError):
        load_plan_artifact(planned.run_dir)


@NOT_IMPLEMENTED
def test_the_plan_checksum_covers_the_report_digest(plan_problems: PlanProblemFixture) -> None:
    """The checksum computed over the manifest changes when only the report digest changes."""
    planned = plan_fixture(plan_problems)
    operations_bytes = (_plan_dir(planned) / OPERATIONS_FILE_NAME).read_bytes()
    manifest = dict(planned.manifest)

    assert compute_plan_checksum(manifest, operations_bytes) == manifest["plan_checksum"]
    manifest["report"] = {**manifest["report"], "digest": "0" * 64}
    assert compute_plan_checksum(manifest, operations_bytes) != planned.manifest["plan_checksum"]


@NOT_IMPLEMENTED
def test_a_plan_with_no_problem_writes_an_empty_report(plan_problems: PlanProblemFixture) -> None:
    """Valid records only: `report.jsonl` is empty and the `report` object holds zeros."""
    planned = plan_fixture(plan_problems, Variant.NO_PROBLEMS)

    assert planned.report_bytes == b""
    assert planned.manifest["report"] == {"digest": report_digest(b""), "size": 0, **ZERO_COUNTS}
    assert planned.manifest["format_version"] == 4


@NOT_IMPLEMENTED
def test_a_run_without_a_schema_snapshot_records_that_dropdown_values_were_not_checked(
    plan_problems: PlanProblemFixture,
) -> None:
    """A direct run has no destination schema snapshot: no Dropdown problem, and the manifest says so."""
    planned = plan_fixture(plan_problems, schema_snapshot=False)

    assert planned.manifest["dropdown_values_checked"] is False
    assert "dropdown_value_not_in_choices" not in {problem["problem_type"] for problem in planned.problems}
    assert planned.problems


@pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason="spec 019 US1 not implemented: the plan writer writes format 3 (T027)",
)
def test_format_3_fields_are_kept(tmp_path: Path) -> None:
    """Destination IDs on updates, `lineage` and `last_changes.jsonl` stay as format 3 writes them."""
    operations, last_changes = _update_operations()
    lineage = PlanLineageRecord(
        import_account=LineageAccountRecord(name="netbox", id="account-1"),
        source=LineageNodeRecord(kind="CoreAccount", name="netbox"),
        owner=None,
        protect=False,
    )
    directory = resolve_run_directory(SYNC_NAME, RUN_ID, base_directory=tmp_path)
    write_plan_artifact(
        run_dir=directory,
        run_id=RUN_ID,
        config_version=CONFIG_VERSION,
        source_snapshot=[],
        deletes_computed=True,
        operations=operations,
        lineage=lineage,
        last_changes=last_changes,
        metadata_read=True,
    )

    manifest = json.loads((directory / PLAN_DIR_NAME / MANIFEST_FILE_NAME).read_bytes())
    assert manifest["format_version"] == 4
    assert manifest["report"] == {"digest": report_digest(b""), "size": 0, **ZERO_COUNTS}
    assert (directory / PLAN_DIR_NAME / REPORT_FILE_NAME).read_bytes() == b""
    assert manifest["lineage"]["import_account"] == {"name": "netbox", "id": "account-1"}
    assert manifest["last_changes"]["path"] == LAST_CHANGES_FILE_NAME
    assert (directory / PLAN_DIR_NAME / LAST_CHANGES_FILE_NAME).is_file()
    loaded = load_plan_artifact(directory)
    updates = [operation for operation in loaded.operations if operation.action == "update"]
    assert updates
    assert all(operation.destination_id for operation in updates)
