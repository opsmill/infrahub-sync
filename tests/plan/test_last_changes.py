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
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

import pytest

from infrahub_sync.plan.canonical import canonical_json_bytes
from infrahub_sync.plan.derive import operations_from_diff
from infrahub_sync.plan.errors import PlanArtifactTornError
from infrahub_sync.plan.last_changes import LastChange, LastChangeCollector
from infrahub_sync.plan.models import LastChangeRecord, PlanManifest, RecordedAccountRecord
from infrahub_sync.plan.reader import read_plan_artifact_bytes
from infrahub_sync.plan.review import read_saved_plan, resolve_run_directory
from infrahub_sync.plan.verify import verify_plan
from infrahub_sync.plan.write_surface import destination_last_changes
from infrahub_sync.plan.writer import LAST_CHANGES_FILE_NAME, MANIFEST_FILE_NAME, PLAN_DIR_NAME, write_plan_artifact
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


# --- Which destinations record last changes ------------------------------------------------------


def test_a_destination_that_keeps_last_changes_is_read_with_its_metadata_flag() -> None:
    changes = {("node-1", "description"): _change()}
    found = destination_last_changes(SimpleNamespace(last_changes=changes, metadata_read=False))

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
    assert manifest["format_version"] == 3
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


def _first_record_with(**changes: str) -> Callable[[list[LastChangeRecord]], list[LastChangeRecord]]:
    """Replace the first record's field or field type, keeping the others."""
    return lambda records: [records[0].model_copy(update=changes), *records[1:]]


@pytest.mark.parametrize(
    ("edit", "message"),
    [
        pytest.param(
            lambda records: [*records, records[0]],
            r"last_changes\.jsonl repeats field 'model' of operation",
            id="duplicate-field",
        ),
        pytest.param(
            _first_record_with(field="serial"),
            r"last_changes\.jsonl line 1 names a field its update does not write",
            id="field-not-in-the-update",
        ),
        pytest.param(
            _first_record_with(field_type="relationship"),
            r"last_changes\.jsonl line 1 names a field its update does not write",
            id="attribute-recorded-as-a-relationship",
        ),
        pytest.param(
            _first_record_with(field="tags", field_type="attribute"),
            r"last_changes\.jsonl line 1 names a field its update does not write",
            id="relationship-recorded-as-an-attribute",
        ),
    ],
)
def test_review_refuses_a_record_that_does_not_match_its_update(
    tmp_path: Path, edit: Callable[[list[LastChangeRecord]], list[LastChangeRecord]], message: str
) -> None:
    operations, records = _update_operations()
    ordered = sorted(records, key=lambda record: (record.operation_id, record.field))
    _write(tmp_path, last_changes=edit(ordered), operations=operations)

    with pytest.raises(PlanArtifactTornError, match=message):
        read_saved_plan(sync_name=SYNC_NAME, run_id=RUN_ID, base_directory=tmp_path)


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
