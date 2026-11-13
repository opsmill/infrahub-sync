"""The plan's last-change file, `plan/last_changes.jsonl`.

The shape is one record for each field
an update proposes to change, none for a create, sorted by operation id then field and written
as canonical JSON. The manifest's `last_changes` field records the file's digest and record
count, so the plan checksum covers the file. A plan without the field is a plan made before
this feature: review shows no last-change data for it.
"""

from __future__ import annotations

import hashlib
import json
import re
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

import pytest

from infrahub_sync.plan.canonical import canonical_json_bytes
from infrahub_sync.plan.checksum import compute_plan_checksum
from infrahub_sync.plan.derive import operations_from_diff
from infrahub_sync.plan.errors import PlanArtifactTornError
from infrahub_sync.plan.last_changes import LastChange, LastChangeCollector, selected_last_changes
from infrahub_sync.plan.models import LastChangeRecord, PlanManifest, RecordedAccountRecord
from infrahub_sync.plan.reader import parse_plan_artifact, read_plan_artifact_bytes
from infrahub_sync.plan.review import read_saved_plan, resolve_run_directory
from infrahub_sync.plan.verify import verify_plan
from infrahub_sync.plan.write_surface import destination_last_changes
from infrahub_sync.plan.writer import (
    LAST_CHANGES_FILE_NAME,
    MANIFEST_FILE_NAME,
    OPERATIONS_FILE_NAME,
    PLAN_DIR_NAME,
    write_plan_artifact,
)
from tests.plan.artifact_fixtures import CONFIG_VERSION, RUN_ID, SYNC_NAME
from tests.test_potenda_plan_artifact import (
    DERIVED_DESTINATION_ID,
    KINDS,
    _FakeAdapter,
    _FakeDiff,
    _FakeElement,
    _FakeRecord,
    build_config,
    build_potenda,
    diff_between,
    plan_run_dir,
    qualified_source,
    resolver,
    run_plan,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence
    from pathlib import Path

    from infrahub_sync.plan.models import PlannedOperation

ACCOUNT = RecordedAccountRecord(id="account-jane", display_label="Jane Doe")
UPDATED_AT = "2026-10-21T09:14:03Z"


def _change(account: RecordedAccountRecord | None = ACCOUNT, updated_at: str | None = UPDATED_AT) -> LastChange:
    """A last change as the Infrahub adapter keeps it: `updated_by` with id and label, `updated_at`."""
    return LastChange(updated_by=account, updated_at=updated_at)


def _derive(
    diff: _FakeDiff, destination: _FakeAdapter, changes: Mapping[tuple[str, str], Any]
) -> tuple[list[PlannedOperation], list[LastChangeRecord]]:
    collector = LastChangeCollector(changes)
    operations = operations_from_diff(
        diff,
        config=build_config(),
        tier_of=resolver(),
        source_adapter=qualified_source(),
        destination_adapter=destination,
        last_changes=collector,
    )
    return operations, collector.records


def _keyed_destination(*, device_attrs: Mapping[str, Any] | None = None) -> _FakeAdapter:
    """A keyed copy of every source object; `device_attrs` replaces the device's attributes."""
    records: list[_FakeRecord] = []
    for kind in KINDS:
        for record in qualified_source().get_all(kind):
            attrs = dict(record.get_attrs())
            if kind == "DcimDevice" and device_attrs is not None:
                attrs = dict(device_attrs)
            records.append(_FakeRecord(kind, record.get_identifiers(), attrs, local_id=DERIVED_DESTINATION_ID))
    return _FakeAdapter("destination", records)


def _tag_update() -> _FakeElement:
    return _FakeElement(
        kind="BuiltinTag",
        name="prod",
        keys={"name": "prod"},
        source_attrs={"description": "production", "slug": "prod"},
        dest_attrs={"description": "stale", "slug": "prod"},
    )


def _write(
    tmp_path: Path,
    *,
    last_changes: Sequence[LastChangeRecord] | None,
    metadata_read: bool = True,
    operations: Sequence[PlannedOperation] = (),
) -> Path:
    directory = resolve_run_directory(SYNC_NAME, RUN_ID, base_directory=tmp_path)
    write_plan_artifact(
        run_dir=directory,
        run_id=RUN_ID,
        config_version=CONFIG_VERSION,
        source_snapshot=[],
        deletes_computed=True,
        operations=operations,
        last_changes=last_changes,
        metadata_read=metadata_read,
    )
    return directory


def _update_operations() -> tuple[list[PlannedOperation], list[LastChangeRecord]]:
    """One tag update and one device update, derived with a recorded change for each field."""
    destination = _keyed_destination(device_attrs={"model": "c9200", "tags": ["alpha"]})
    diff = diff_between(source=qualified_source(), destination=destination, kinds=["DcimDevice"])
    diff.children["BuiltinTag"] = {"prod": _tag_update()}
    changes = {
        (DERIVED_DESTINATION_ID, "description"): _change(),
        (DERIVED_DESTINATION_ID, "model"): _change(),
        (DERIVED_DESTINATION_ID, "tags"): _change(),
    }
    return _derive(diff, destination, changes)


# --- Derivation: one record for each changed field of each update ------------------------------


def test_an_update_has_one_record_for_each_field_it_changes() -> None:
    """Only the changed field gets a record; the unchanged `slug` gets none."""
    destination = _keyed_destination()
    operations, records = _derive(
        _FakeDiff({"BuiltinTag": [_tag_update()]}),
        destination,
        {(DERIVED_DESTINATION_ID, "description"): _change(), (DERIVED_DESTINATION_ID, "slug"): _change()},
    )

    (operation,) = operations
    assert [(record.operation_id, record.field, record.field_type) for record in records] == [
        (operation.operation_id, "description", "attribute")
    ]
    assert records[0].updated_by == ACCOUNT
    assert records[0].updated_at == UPDATED_AT


def test_a_changed_relationship_is_recorded_as_a_relationship() -> None:
    operations, records = _update_operations()

    device = next(operation for operation in operations if operation.kind == "DcimDevice")
    device_records = {
        record.field: record.field_type for record in records if record.operation_id == device.operation_id
    }
    assert device_records == {"model": "attribute", "tags": "relationship"}


def test_a_create_has_no_record() -> None:
    element = _FakeElement(
        kind="BuiltinTag",
        name="prod",
        keys={"name": "prod"},
        source_attrs={"description": "production", "slug": "prod"},
    )

    operations, records = _derive(
        _FakeDiff({"BuiltinTag": [element]}), _FakeAdapter("destination", []), {("any", "description"): _change()}
    )

    assert [operation.action for operation in operations] == ["create"]
    assert records == []


def test_a_field_with_no_recorded_change_keeps_null_values() -> None:
    """A field the map holds nothing for, or holds no value for, is recorded with `null`s."""
    destination = _keyed_destination()
    _, records = _derive(_FakeDiff({"BuiltinTag": [_tag_update()]}), destination, {})
    assert (records[0].updated_by, records[0].updated_at) == (None, None)

    _, records = _derive(
        _FakeDiff({"BuiltinTag": [_tag_update()]}),
        destination,
        {(DERIVED_DESTINATION_ID, "description"): _change(account=None, updated_at=None)},
    )
    assert (records[0].updated_by, records[0].updated_at) == (None, None)


def test_derivation_without_a_collector_is_unchanged() -> None:
    """A destination that keeps no last changes derives the same operations as before."""
    destination = _keyed_destination()
    with_collector, _ = _derive(_FakeDiff({"BuiltinTag": [_tag_update()]}), destination, {})
    without = operations_from_diff(
        _FakeDiff({"BuiltinTag": [_tag_update()]}),
        config=build_config(),
        tier_of=resolver(),
        source_adapter=qualified_source(),
        destination_adapter=destination,
    )
    assert without == with_collector


def test_the_selection_keeps_only_records_of_fields_the_plan_still_changes() -> None:
    """Spec 020 T028: a removed update, or a relationship the update no longer carries, keeps no record."""
    operations, records = _update_operations()
    device = next(operation for operation in operations if operation.kind == "DcimDevice")
    tag = next(operation for operation in operations if operation.kind == "BuiltinTag")
    assert {(record.operation_id, record.field) for record in records} >= {
        (device.operation_id, "tags"),
        (tag.operation_id, "description"),
    }
    # The selection step left the tag out and wrote the device without its `tags` relationship.
    without_tags = device.model_copy(update={"relationships": None})

    kept = selected_last_changes(records, [without_tags])

    assert kept
    assert {record.operation_id for record in kept} == {device.operation_id}
    assert "tags" not in {record.field for record in kept}
    assert kept == [
        record for record in records if record.operation_id == device.operation_id and record.field != "tags"
    ]


# --- Which destinations record last changes ------------------------------------------------------


class _LastChangeDestination:
    """A destination with the lineage and metadata-reading members the Infrahub destination has, and nothing to resolve."""

    lineage_record = None

    def __init__(self, changes: Mapping[tuple[str, str], LastChange], *, metadata_read: bool) -> None:
        self.last_changes = changes
        self.metadata_read = metadata_read

    def resolve_lineage(self, *, saved_plan: object = None) -> None:
        """Resolve nothing."""

    def resolve_metadata_read(self) -> None:
        """Read nothing."""


def test_a_destination_that_keeps_last_changes_is_read_with_its_metadata_flag() -> None:
    changes = {("node-1", "description"): _change()}
    found = destination_last_changes(_LastChangeDestination(changes, metadata_read=False))

    assert found is not None
    assert found.changes == changes
    assert found.metadata_read is False
    assert found.records == []


def test_a_destination_that_keeps_no_last_changes_writes_no_file() -> None:
    assert destination_last_changes(SimpleNamespace()) is None


# --- The written file and the manifest field -----------------------------------------------------


def test_records_are_sorted_and_written_as_canonical_json(tmp_path: Path) -> None:
    operations, records = _update_operations()
    directory = _write(tmp_path, last_changes=list(reversed(records)), operations=operations)

    written = (directory / PLAN_DIR_NAME / LAST_CHANGES_FILE_NAME).read_bytes()
    ordered = sorted(records, key=lambda record: (record.operation_id, record.field))
    assert written == b"".join(canonical_json_bytes(record.model_dump()) + b"\n" for record in ordered)
    assert len(written.splitlines()) == 3


def test_null_values_are_kept_in_the_file(tmp_path: Path) -> None:
    operations, records = _update_operations()
    nulls = [record.model_copy(update={"updated_by": None, "updated_at": None}) for record in records]
    directory = _write(tmp_path, last_changes=nulls, operations=operations)

    lines = (directory / PLAN_DIR_NAME / LAST_CHANGES_FILE_NAME).read_bytes().splitlines()
    first = json.loads(lines[0])
    assert "updated_by" in first
    assert first["updated_by"] is None
    assert first["updated_at"] is None


def test_the_manifest_records_the_digest_and_the_count(tmp_path: Path) -> None:
    operations, records = _update_operations()
    directory = _write(tmp_path, last_changes=records, operations=operations, metadata_read=True)

    sidecar = (directory / PLAN_DIR_NAME / LAST_CHANGES_FILE_NAME).read_bytes()
    manifest = json.loads((directory / PLAN_DIR_NAME / MANIFEST_FILE_NAME).read_bytes())
    assert manifest["last_changes"] == {
        "path": "last_changes.jsonl",
        "sha256": hashlib.sha256(sidecar).hexdigest(),
        "count": 3,
        "metadata_read": True,
    }
    assert manifest["format_version"] == 4
    assert PlanManifest.model_validate(manifest).last_changes is not None


def test_a_plan_whose_metadata_was_not_read_records_that(tmp_path: Path) -> None:
    operations, records = _update_operations()
    nulls = [record.model_copy(update={"updated_by": None, "updated_at": None}) for record in records]
    directory = _write(tmp_path, last_changes=nulls, operations=operations, metadata_read=False)

    manifest = json.loads((directory / PLAN_DIR_NAME / MANIFEST_FILE_NAME).read_bytes())
    assert manifest["last_changes"]["metadata_read"] is False
    assert manifest["last_changes"]["count"] == 3


def test_a_plan_with_no_last_changes_writes_no_file_and_no_field(tmp_path: Path) -> None:
    directory = _write(tmp_path, last_changes=None)

    assert not (directory / PLAN_DIR_NAME / LAST_CHANGES_FILE_NAME).exists()
    assert "last_changes" not in json.loads((directory / PLAN_DIR_NAME / MANIFEST_FILE_NAME).read_bytes())


# --- Review and verification ---------------------------------------------------------------------


def test_review_reads_the_records_back(tmp_path: Path) -> None:
    operations, records = _update_operations()
    _write(tmp_path, last_changes=records, operations=operations)

    saved = read_saved_plan(sync_name=SYNC_NAME, run_id=RUN_ID, base_directory=tmp_path)

    assert saved.last_changes == sorted(records, key=lambda record: (record.operation_id, record.field))


def test_a_plan_without_the_file_is_reviewed_without_last_changes(tmp_path: Path) -> None:
    _write(tmp_path, last_changes=None)

    saved = read_saved_plan(sync_name=SYNC_NAME, run_id=RUN_ID, base_directory=tmp_path)

    assert saved.last_changes is None
    assert saved.checksum_ok


def _tamper(directory: Path) -> None:
    path = directory / PLAN_DIR_NAME / LAST_CHANGES_FILE_NAME
    path.write_bytes(path.read_bytes().replace(b"Jane Doe", b"Jane Roe"))


def test_review_refuses_a_file_whose_digest_does_not_match(tmp_path: Path) -> None:
    operations, records = _update_operations()
    directory = _write(tmp_path, last_changes=records, operations=operations)
    _tamper(directory)

    with pytest.raises(PlanArtifactTornError, match=r"last_changes\.jsonl"):
        read_saved_plan(sync_name=SYNC_NAME, run_id=RUN_ID, base_directory=tmp_path)


def test_review_refuses_a_missing_file_the_manifest_names(tmp_path: Path) -> None:
    operations, records = _update_operations()
    directory = _write(tmp_path, last_changes=records, operations=operations)
    (directory / PLAN_DIR_NAME / LAST_CHANGES_FILE_NAME).unlink()

    with pytest.raises(PlanArtifactTornError, match=r"last_changes\.jsonl"):
        read_saved_plan(sync_name=SYNC_NAME, run_id=RUN_ID, base_directory=tmp_path)


def test_review_refuses_a_file_whose_record_count_does_not_match(tmp_path: Path) -> None:
    operations, records = _update_operations()
    directory = _write(tmp_path, last_changes=records, operations=operations)
    path = directory / PLAN_DIR_NAME / LAST_CHANGES_FILE_NAME
    # Drop a line and record the new digest, but keep the old count: only the count disagrees.
    lines = path.read_bytes().splitlines(keepends=True)
    _replace_last_change_lines(directory, lines[1:], count=len(lines))

    with pytest.raises(PlanArtifactTornError, match=r"last_changes\.jsonl does not match the manifest's record of it"):
        read_saved_plan(sync_name=SYNC_NAME, run_id=RUN_ID, base_directory=tmp_path)
    failures = verify_plan(artifact=read_plan_artifact_bytes(directory), run_id=RUN_ID, config_version=CONFIG_VERSION)
    assert [failure.check for failure in failures] == ["last_changes"]


def _replace_last_change_lines(directory: Path, lines: Sequence[bytes], *, count: int | None = None) -> None:
    """Write `lines` as the last-change file and record its digest, so the file matches.

    The manifest's digest and count are updated and its `plan_checksum` recomputed, which is
    what a writer that produced these lines would have written: only the content is bad.
    """
    sidecar = b"".join(lines)
    (directory / PLAN_DIR_NAME / LAST_CHANGES_FILE_NAME).write_bytes(sidecar)
    manifest_file = directory / PLAN_DIR_NAME / MANIFEST_FILE_NAME
    manifest = json.loads(manifest_file.read_bytes())
    manifest["last_changes"]["sha256"] = hashlib.sha256(sidecar).hexdigest()
    manifest["last_changes"]["count"] = len(lines) if count is None else count
    operations_bytes = (directory / PLAN_DIR_NAME / OPERATIONS_FILE_NAME).read_bytes()
    manifest["plan_checksum"] = compute_plan_checksum(manifest, operations_bytes)
    manifest_file.write_bytes(canonical_json_bytes(manifest))


def _record_lines(records: Sequence[LastChangeRecord]) -> list[bytes]:
    return [canonical_json_bytes(record.model_dump(mode="json")) + b"\n" for record in records]


def _ordered_records() -> tuple[list[PlannedOperation], list[LastChangeRecord]]:
    operations, records = _update_operations()
    return operations, sorted(records, key=lambda record: (record.operation_id, record.field))


# Each case: the bad last-change file's lines, built from the ordered good records, and the
# note review must show. Every bad line here is the last line, so removing it gives the plan
# SC-001 compares against.
_CONTENT_FAILURES = [
    pytest.param(
        lambda records: [*_record_lines(records), _record_lines(records)[0]],
        r"^last_changes\.jsonl line 4 repeats field 'model' of operation 'op_[0-9a-f]+' "
        r"\(first recorded on line 1\); last-change details are not shown$",
        id="duplicate-field",
    ),
    pytest.param(
        lambda records: [
            *_record_lines(records),
            _record_lines([records[0].model_copy(update={"field": "serial"})])[0],
        ],
        r"^last_changes\.jsonl line 4 names a field its update does not write \(attribute 'serial' of operation",
        id="field-not-in-the-update",
    ),
    pytest.param(
        lambda records: [
            *_record_lines(records),
            _record_lines([records[0].model_copy(update={"field_type": "relationship"})])[0],
        ],
        r"^last_changes\.jsonl line 4 names a field its update does not write \(relationship 'model'",
        id="attribute-recorded-as-a-relationship",
    ),
    pytest.param(
        lambda records: [
            *_record_lines(records),
            _record_lines([records[0].model_copy(update={"operation_id": "op_ffffffffffffffff"})])[0],
        ],
        r"^last_changes\.jsonl line 4 names no update of this plan \(operation 'op_ffffffffffffffff'\)",
        id="no-such-update",
    ),
    pytest.param(
        lambda records: [*_record_lines(records), b"{not json\n"],
        r"^last_changes\.jsonl line 4 is not parseable JSON; last-change details are not shown$",
        id="not-json",
    ),
    pytest.param(
        lambda records: [*_record_lines(records), b'{"field":"\xff"}\n'],
        r"^last_changes\.jsonl line 4 is not parseable JSON; last-change details are not shown$",
        id="not-utf-8",
    ),
    pytest.param(
        lambda records: [*_record_lines(records), b'{"operation_id":"op_0"}\n'],
        r"^last_changes\.jsonl line 4 is not a valid last-change record \(operation_id: ",
        id="not-a-record",
    ),
]


@pytest.mark.parametrize(("bad_lines", "note"), _CONTENT_FAILURES)
def test_review_shows_one_note_and_no_last_changes_for_a_bad_record(
    tmp_path: Path, bad_lines: Callable[[list[LastChangeRecord]], list[bytes]], note: str
) -> None:
    """A file that matches its digest but holds a bad record is shown, not refused."""
    operations, records = _ordered_records()
    directory = _write(tmp_path, last_changes=records, operations=operations)
    _replace_last_change_lines(directory, bad_lines(records))

    saved = read_saved_plan(sync_name=SYNC_NAME, run_id=RUN_ID, base_directory=tmp_path)

    assert saved.checksum_ok
    assert saved.last_changes is None
    assert len(saved.verification_notes) == 1
    assert re.search(note, saved.verification_notes[0])
    assert sorted(operation.operation_id for operation in saved.operations()) == sorted(
        operation.operation_id for operation in operations
    )


@pytest.mark.parametrize(("bad_lines", "note"), _CONTENT_FAILURES)
def test_apply_reads_a_plan_with_a_bad_record_as_the_plan_without_it(
    tmp_path: Path, bad_lines: Callable[[list[LastChangeRecord]], list[bytes]], note: str
) -> None:
    """SC-001: the plan passes verification and parses to the same operations."""
    del note
    operations, records = _ordered_records()
    good = _write(tmp_path / "good", last_changes=records, operations=operations)
    bad = _write(tmp_path / "bad", last_changes=records, operations=operations)
    _replace_last_change_lines(bad, bad_lines(records))

    good_raw, bad_raw = read_plan_artifact_bytes(good), read_plan_artifact_bytes(bad)

    assert verify_plan(artifact=bad_raw, run_id=RUN_ID, config_version=CONFIG_VERSION) == []
    good_plan = parse_plan_artifact(good_raw, run_id=RUN_ID)
    bad_plan = parse_plan_artifact(bad_raw, run_id=RUN_ID)
    assert bad_plan.operations == good_plan.operations
    assert bad_plan.operations_bytes == good_plan.operations_bytes
    assert bad_plan.last_changes is None
    assert bad_plan.last_changes_problem is not None
    assert good_plan.last_changes_problem is None


def test_review_names_the_first_bad_line(tmp_path: Path) -> None:
    operations, records = _ordered_records()
    directory = _write(tmp_path, last_changes=records, operations=operations)
    lines = _record_lines(records)
    _replace_last_change_lines(directory, [lines[0], b"{not json\n", lines[1], lines[0]])

    saved = read_saved_plan(sync_name=SYNC_NAME, run_id=RUN_ID, base_directory=tmp_path)

    assert saved.verification_notes == [
        "last_changes.jsonl line 2 is not parseable JSON; last-change details are not shown"
    ]


def test_review_accepts_an_update_field_with_no_record(tmp_path: Path) -> None:
    """The payload holds unchanged attributes too, so a field without a record is not a tear."""
    operations, records = _update_operations()
    kept = [record for record in records if record.field != "model"]
    _write(tmp_path, last_changes=kept, operations=operations)

    saved = read_saved_plan(sync_name=SYNC_NAME, run_id=RUN_ID, base_directory=tmp_path)

    assert saved.last_changes == sorted(kept, key=lambda record: (record.operation_id, record.field))


def test_verification_refuses_a_file_whose_digest_does_not_match(tmp_path: Path) -> None:
    operations, records = _update_operations()
    directory = _write(tmp_path, last_changes=records, operations=operations)
    assert verify_plan(artifact=read_plan_artifact_bytes(directory), run_id=RUN_ID, config_version=CONFIG_VERSION) == []
    _tamper(directory)

    failures = verify_plan(artifact=read_plan_artifact_bytes(directory), run_id=RUN_ID, config_version=CONFIG_VERSION)

    assert [failure.check for failure in failures] == ["last_changes"]


def test_the_plan_checksum_covers_the_manifest_field(tmp_path: Path) -> None:
    """Editing the recorded digest to match a tampered file breaks the plan checksum instead."""
    operations, records = _update_operations()
    directory = _write(tmp_path, last_changes=records, operations=operations)
    _tamper(directory)
    manifest_file = directory / PLAN_DIR_NAME / MANIFEST_FILE_NAME
    manifest = json.loads(manifest_file.read_bytes())
    sidecar = (directory / PLAN_DIR_NAME / LAST_CHANGES_FILE_NAME).read_bytes()
    manifest["last_changes"]["sha256"] = hashlib.sha256(sidecar).hexdigest()
    manifest_file.write_bytes(canonical_json_bytes(manifest))

    failures = verify_plan(artifact=read_plan_artifact_bytes(directory), run_id=RUN_ID, config_version=CONFIG_VERSION)

    assert [failure.check for failure in failures] == ["plan_checksum"]


# --- The plan run: the destination's map reaches the written file --------------------------------


def test_a_plan_run_writes_the_last_changes_the_destination_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`Potenda` passes the destination's map to derivation and the records to the writer."""
    monkeypatch.setenv("INFRAHUB_SYNC_CACHE_DIR", str(tmp_path / "cache"))
    destination = _keyed_destination(device_attrs={"model": "c9200", "tags": ["zeta", "alpha"]})
    destination.last_changes = {(DERIVED_DESTINATION_ID, "model"): _change()}  # ty: ignore[unresolved-attribute]
    destination.metadata_read = True  # ty: ignore[unresolved-attribute]
    # The lineage members too, as the Infrahub destination has them.
    destination.lineage_record = None  # ty: ignore[unresolved-attribute]
    destination.resolve_lineage = lambda **_kwargs: None  # ty: ignore[unresolved-attribute]
    destination.resolve_metadata_read = lambda: None  # ty: ignore[unresolved-attribute]
    potenda = build_potenda(config=build_config(), source=qualified_source(), destination=destination, run_id=RUN_ID)

    run_plan(potenda)

    directory = plan_run_dir(potenda)
    manifest = json.loads((directory / PLAN_DIR_NAME / MANIFEST_FILE_NAME).read_bytes())
    lines = (directory / PLAN_DIR_NAME / LAST_CHANGES_FILE_NAME).read_bytes().splitlines()
    assert manifest["last_changes"]["count"] == 1
    assert manifest["last_changes"]["metadata_read"] is True
    record = json.loads(lines[0])
    assert (record["field"], record["field_type"]) == ("model", "attribute")
    assert record["updated_by"] == {"id": "account-jane", "display_label": "Jane Doe"}
    assert record["updated_at"] == UPDATED_AT


def test_a_plan_run_against_a_destination_without_last_changes_writes_no_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("INFRAHUB_SYNC_CACHE_DIR", str(tmp_path / "cache"))
    destination = _keyed_destination(device_attrs={"model": "c9200", "tags": ["zeta", "alpha"]})
    potenda = build_potenda(config=build_config(), source=qualified_source(), destination=destination, run_id=RUN_ID)

    run_plan(potenda)

    directory = plan_run_dir(potenda)
    assert not (directory / PLAN_DIR_NAME / LAST_CHANGES_FILE_NAME).exists()
    assert "last_changes" not in json.loads((directory / PLAN_DIR_NAME / MANIFEST_FILE_NAME).read_bytes())
