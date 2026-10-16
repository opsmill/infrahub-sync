"""Saved plan format 4: `report.jsonl` and the manifest `report` object (spec 009 T035, ADR 0022)."""

from __future__ import annotations

import hashlib
import json
from typing import TYPE_CHECKING, Any

import pytest

from infrahub_sync.plan import writer
from infrahub_sync.plan.canonical import canonical_json_bytes
from infrahub_sync.plan.checksum import compute_plan_checksum
from infrahub_sync.plan.errors import (
    LeftOutPeerInOperationError,
    PlanReportCountMismatchError,
    PlanReportDigestMismatchError,
    PlanReportMissingError,
    PlanReportSizeMismatchError,
    PlanVerificationError,
)
from infrahub_sync.plan.identity import operation_id
from infrahub_sync.plan.left_out_peer import left_out_peer_pair
from infrahub_sync.plan.models import (
    LeftOutRecord,
    PlannedOperation,
    PlanProblem,
    PlanRecordReference,
    PlanReport,
    RelationshipReference,
)
from infrahub_sync.plan.reader import load_plan_artifact, parse_report, read_plan_artifact_bytes, report_page
from infrahub_sync.plan.verify import verify_plan
from infrahub_sync.plan.writer import MANIFEST_FILE_NAME, OPERATIONS_FILE_NAME, PLAN_DIR_NAME, REPORT_FILE_NAME
from tests.plan.problem_plan import plan_fixture

if TYPE_CHECKING:
    from pathlib import Path

RUN_ID = "20261015T1200-1a2b3c4d"
CONFIG_VERSION = "5f2c9b1e7a4d3c8f"
REPORT_KEYS = {"digest", "size", "problems", "left_out", "left_out_with_problem", "left_out_dependent", "kept"}


def _site(name: str) -> PlannedOperation:
    identity = {"name": name}
    return PlannedOperation(
        operation_id=operation_id("create", "LocationSite", identity),
        action="create",
        kind="LocationSite",
        identity=identity,
        tier=0,
        payload=dict(identity),
    )


def _reference(kind: str, source_id: str, **identity: Any) -> PlanRecordReference:  # noqa: ANN401
    return PlanRecordReference(kind=kind, identity=identity or None, source_id=source_id)


def _report(status: str = "decommissioning") -> PlanReport:
    berlin = _reference("LocationSite", "3", name="berlin")
    core = _reference("DcimDevice", "30", name="core-1")
    return PlanReport(
        problems=[
            PlanProblem(
                problem_type="wrong_value_type",
                record=core,
                field="position",
                value=2.5,
                reason="DcimDevice record source ID 30: the value 2.5 of position has the wrong type (int_from_float).",
            ),
            PlanProblem(
                problem_type="dropdown_value_not_in_choices",
                record=berlin,
                field="status",
                value=status,
                allowed=["active", "planned"],
                reason="LocationSite record source ID 3: a fixed reason.",
            ),
        ],
        left_out=[
            LeftOutRecord(
                record=berlin, cause="has_problem", problem_types=["dropdown_value_not_in_choices"], loaded=True
            ),
            LeftOutRecord(
                record=_reference("InterfacePhysical", "40", name="eth0"),
                cause="depends_on_left_out",
                depends_on=core,
                relationship="device",
                loaded=True,
            ),
            LeftOutRecord(record=core, cause="has_problem", problem_types=["wrong_value_type"], loaded=False),
        ],
    )


def _write(run_dir: Path, report: PlanReport | None = None, operations: list[PlannedOperation] | None = None) -> Any:  # noqa: ANN401
    return writer.write_plan_artifact(
        run_dir=run_dir,
        run_id=RUN_ID,
        config_version=CONFIG_VERSION,
        source_snapshot=[],
        deletes_computed=True,
        operations=operations if operations is not None else [_site("paris")],
        report=report,
    )


def _manifest(run_dir: Path) -> dict[str, Any]:
    return json.loads((run_dir / PLAN_DIR_NAME / MANIFEST_FILE_NAME).read_bytes())


def _rewrite_manifest(run_dir: Path, **changes: Any) -> None:  # noqa: ANN401
    """Change manifest fields and keep the checksum consistent, so only the report check fails."""
    mapping = _manifest(run_dir)
    mapping.update(changes)
    mapping["plan_checksum"] = compute_plan_checksum(
        mapping, (run_dir / PLAN_DIR_NAME / OPERATIONS_FILE_NAME).read_bytes()
    )
    (run_dir / PLAN_DIR_NAME / MANIFEST_FILE_NAME).write_bytes(canonical_json_bytes(mapping))


def test_the_writer_writes_operations_then_report_then_manifest_each_through_a_temporary_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    order: list[str] = []
    temporary: list[str] = []
    real_mkstemp = writer.tempfile.mkstemp

    def recording_mkstemp(*, prefix: str, dir: str) -> tuple[int, str]:  # noqa: A002 - the stdlib keyword
        handle, name = real_mkstemp(prefix=prefix, dir=dir)
        temporary.append(name)
        order.append(prefix.rstrip("."))
        return handle, name

    monkeypatch.setattr(writer.tempfile, "mkstemp", recording_mkstemp)
    _write(tmp_path, _report())

    assert order == [OPERATIONS_FILE_NAME, REPORT_FILE_NAME, MANIFEST_FILE_NAME]
    assert all(not name.endswith((OPERATIONS_FILE_NAME, REPORT_FILE_NAME, MANIFEST_FILE_NAME)) for name in temporary)
    assert sorted(path.name for path in (tmp_path / PLAN_DIR_NAME).iterdir()) == sorted(
        [OPERATIONS_FILE_NAME, REPORT_FILE_NAME, MANIFEST_FILE_NAME]
    )


def test_the_manifest_is_format_4_and_records_the_report(tmp_path: Path) -> None:
    manifest = _write(tmp_path, _report())
    report_bytes = (tmp_path / PLAN_DIR_NAME / REPORT_FILE_NAME).read_bytes()
    on_disk = _manifest(tmp_path)

    assert manifest.format_version == 4
    assert on_disk["format_version"] == 4
    assert set(on_disk["report"]) == REPORT_KEYS
    assert on_disk["report"] == {
        "digest": hashlib.sha256(report_bytes).hexdigest(),
        "size": len(report_bytes),
        "problems": 2,
        "left_out": 3,
        "left_out_with_problem": 2,
        "left_out_dependent": 1,
        "kept": 0,
    }


def test_an_empty_report_is_a_zero_byte_file_with_zero_counts(tmp_path: Path) -> None:
    _write(tmp_path)

    assert (tmp_path / PLAN_DIR_NAME / REPORT_FILE_NAME).read_bytes() == b""
    assert _manifest(tmp_path)["report"] == {
        "digest": hashlib.sha256(b"").hexdigest(),
        "size": 0,
        "problems": 0,
        "left_out": 0,
        "left_out_with_problem": 0,
        "left_out_dependent": 0,
        "kept": 0,
    }


def test_the_report_lines_follow_the_stable_order(tmp_path: Path) -> None:
    """Problems by type, kind, identity and source ID; then left-out records; then kept records."""
    planned = plan_fixture(tmp_path / "run")
    lines = [
        json.loads(line) for line in (planned.run_dir / PLAN_DIR_NAME / REPORT_FILE_NAME).read_bytes().splitlines()
    ]

    def order(line: dict[str, Any]) -> tuple[Any, ...]:
        record = line["record"]
        identity = b"" if record["identity"] is None else canonical_json_bytes(record["identity"])
        rank = ["problem", "left_out", "kept"].index(line["entry"])
        if line["entry"] == "problem":
            return (rank, line["problem_type"], record["kind"], identity, record["source_id"] or "")
        if line["entry"] == "left_out":
            return (rank, "", record["kind"], identity, record["source_id"] or "")
        return (rank, "", record["kind"], identity, line["relationship"])

    assert lines == sorted(lines, key=order)
    assert [line["entry"] for line in lines].count("problem") == 8
    assert all(
        raw == canonical_json_bytes(json.loads(raw))
        for raw in (planned.run_dir / PLAN_DIR_NAME / REPORT_FILE_NAME).read_bytes().splitlines()
    )


def test_the_plan_checksum_changes_when_one_report_byte_changes(tmp_path: Path) -> None:
    first = _write(tmp_path / "first", _report("decommissioning"))
    second = _write(tmp_path / "second", _report("decommissioninG"))
    first_report = (tmp_path / "first" / PLAN_DIR_NAME / REPORT_FILE_NAME).read_bytes()
    second_report = (tmp_path / "second" / PLAN_DIR_NAME / REPORT_FILE_NAME).read_bytes()

    assert len(first_report) == len(second_report)
    assert sum(a != b for a, b in zip(first_report, second_report, strict=True)) == 1
    assert first.plan_checksum != second.plan_checksum
    for directory, manifest in ((tmp_path / "first", first), (tmp_path / "second", second)):
        operations = (directory / PLAN_DIR_NAME / OPERATIONS_FILE_NAME).read_bytes()
        assert compute_plan_checksum(_manifest(directory), operations) == manifest.plan_checksum


def _report_failures(run_dir: Path) -> list[str]:
    failures = verify_plan(artifact=read_plan_artifact_bytes(run_dir), run_id=RUN_ID, config_version=CONFIG_VERSION)
    return [failure.check for failure in failures]


def test_verification_refuses_a_missing_report(tmp_path: Path) -> None:
    _write(tmp_path, _report())
    (tmp_path / PLAN_DIR_NAME / REPORT_FILE_NAME).unlink()

    with pytest.raises(PlanReportMissingError) as raised:
        load_plan_artifact(tmp_path)
    assert isinstance(raised.value, PlanVerificationError)
    assert _report_failures(tmp_path) == ["report"]


def test_verification_refuses_a_digest_mismatch(tmp_path: Path) -> None:
    _write(tmp_path, _report())
    path = tmp_path / PLAN_DIR_NAME / REPORT_FILE_NAME
    path.write_bytes(path.read_bytes().replace(b"decommissioning", b"decommissioninG"))

    with pytest.raises(PlanReportDigestMismatchError):
        load_plan_artifact(tmp_path)
    assert _report_failures(tmp_path) == ["report"]


def test_verification_refuses_a_size_mismatch(tmp_path: Path) -> None:
    _write(tmp_path, _report())
    recorded = _manifest(tmp_path)["report"]
    _rewrite_manifest(tmp_path, report={**recorded, "size": recorded["size"] + 1})

    with pytest.raises(PlanReportSizeMismatchError):
        load_plan_artifact(tmp_path)
    assert _report_failures(tmp_path) == ["report"]


def test_verification_refuses_a_line_count_mismatch(tmp_path: Path) -> None:
    _write(tmp_path, _report())
    recorded = _manifest(tmp_path)["report"]
    _rewrite_manifest(
        tmp_path,
        report={
            **recorded,
            "left_out": recorded["left_out"] + 1,
            "left_out_with_problem": recorded["left_out_with_problem"] + 1,
        },
    )

    with pytest.raises(PlanReportCountMismatchError):
        load_plan_artifact(tmp_path)
    assert _report_failures(tmp_path) == ["report"]


def test_the_reader_accepts_format_4_with_its_report(tmp_path: Path) -> None:
    _write(tmp_path, _report())

    loaded = load_plan_artifact(tmp_path)

    assert loaded.manifest.format_version == 4
    assert loaded.report == _report().ordered()


@pytest.mark.parametrize("version", [2, 3])
def test_the_reader_accepts_formats_2_and_3_with_an_empty_report(tmp_path: Path, version: int) -> None:
    _write(tmp_path)
    (tmp_path / PLAN_DIR_NAME / REPORT_FILE_NAME).unlink()
    mapping = _manifest(tmp_path)
    del mapping["report"]
    mapping["format_version"] = version
    mapping["plan_checksum"] = compute_plan_checksum(
        mapping, (tmp_path / PLAN_DIR_NAME / OPERATIONS_FILE_NAME).read_bytes()
    )
    (tmp_path / PLAN_DIR_NAME / MANIFEST_FILE_NAME).write_bytes(canonical_json_bytes(mapping))

    loaded = load_plan_artifact(tmp_path)

    assert loaded.manifest.format_version == version
    assert loaded.report == PlanReport()
    assert parse_report(None, mapping, run_id=RUN_ID) == PlanReport()
    assert _report_failures(tmp_path) == []


def test_the_writer_refuses_an_operation_that_still_holds_a_left_out_placeholder(tmp_path: Path) -> None:
    placeholder = left_out_peer_pair(peer_kind="DcimDevice", cause="not_loaded", source_id="30")["identity"]
    operation = _site("paris").model_copy(
        update={
            "relationships": [
                RelationshipReference(field="device", peer_kind="DcimDevice", cardinality="one", peers=[placeholder])
            ]
        }
    )

    with pytest.raises(LeftOutPeerInOperationError):
        _write(tmp_path, operations=[operation])
    assert not (tmp_path / PLAN_DIR_NAME).exists()


def test_report_pages_filter_by_entry_kind_and_problem_type(tmp_path: Path) -> None:
    report = plan_fixture(tmp_path / "run").report

    problems = report_page(report, entry="problem", limit=500)
    by_type = report_page(report, entry="problem", problem_type="peer_not_found_in_source")
    by_kind = report_page(report, entry="left_out", kind="InterfacePhysical")
    first = report_page(report, entry="left_out", offset=0, limit=4)
    rest = report_page(report, entry="left_out", offset=4, limit=500)

    assert problems.total == 8
    assert problems.next_offset is None
    assert by_type.total == 2
    assert {line["problem_type"] for line in by_type.entries} == {"peer_not_found_in_source"}
    assert {line["record"]["kind"] for line in by_kind.entries} == {"InterfacePhysical"}
    assert first.next_offset == 4
    assert first.entries + rest.entries == report_page(report, entry="left_out").entries
