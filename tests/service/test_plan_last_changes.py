"""The `last_changes` field of the published plan review, `PlanResource`.

A plan made with `last_changes.jsonl` publishes its records in the review document, so
`runs plan` shows who last changed each value an update changes. A plan made before the file
existed publishes no `last_changes` field, and its review still validates.
"""

from __future__ import annotations

import json

import pytest

pytest.importorskip("prefect")
pytest.importorskip("opsmill_prefect_extras")

from infrahub_sync.client import PlanResource
from infrahub_sync.plan.models import LastChangeRecord, PlanManifest, RecordedAccountRecord
from infrahub_sync.plan.review import SavedPlan
from infrahub_sync.service import flow as service_flow

RUN_ID = "run-last-changes"
OPERATION_ID = "op_0123456789abcdef"
RECORDS = (
    LastChangeRecord(
        operation_id=OPERATION_ID,
        field="description",
        field_type="attribute",
        updated_by=RecordedAccountRecord(id="account-2", display_label="Jane Doe"),
        updated_at="2026-10-21T09:14:03Z",
    ),
    LastChangeRecord(
        operation_id=OPERATION_ID,
        field="site",
        field_type="relationship",
        updated_by=None,
        updated_at=None,
    ),
)


def _saved(last_changes: tuple[LastChangeRecord, ...] | None) -> SavedPlan:
    manifest = PlanManifest(
        format_version=3,
        run_id=RUN_ID,
        created_at="2026-10-23T12:00:00+00:00",
        config_version="configuration-v1",
        source_snapshot=[],
        operations_count=0,
        delete_operations_computed=True,
        plan_checksum="a" * 64,
    )
    return SavedPlan(
        manifest=manifest, operations=[], checksum_ok=True, verification_notes=[], last_changes=last_changes
    )


def test_the_review_of_a_new_plan_carries_its_last_change_records() -> None:
    """Each record reaches the review, `null` values kept as `null`."""
    document = service_flow._review_document(RUN_ID, _saved(RECORDS))

    assert document.last_changes is not None
    assert [record.model_dump(mode="json") for record in document.last_changes] == [
        record.model_dump(mode="json") for record in RECORDS
    ]
    published = json.loads(document.model_dump_json())
    assert published["last_changes"][1]["updated_by"] is None
    assert published["last_changes"][1]["updated_at"] is None
    assert PlanResource.model_validate(published) == document


def test_the_review_of_a_plan_with_no_changed_fields_carries_an_empty_list() -> None:
    """A plan with the file but no update records is not an older plan."""
    assert service_flow._review_document(RUN_ID, _saved(())).last_changes == ()


def test_the_review_of_an_older_plan_omits_last_changes() -> None:
    """A plan made before `last_changes.jsonl` existed publishes no records."""
    document = service_flow._review_document(RUN_ID, _saved(None))

    assert document.last_changes is None
    published = json.loads(document.model_dump_json())
    published.pop("last_changes")
    assert PlanResource.model_validate(published).last_changes is None


def test_the_review_of_a_plan_with_a_bad_last_change_record_carries_the_note() -> None:
    """A bad record publishes no records and the reader's note, in the existing field."""
    note = "last_changes.jsonl line 4 repeats field 'name' of operation 'op-12'; last-change details are not shown"
    saved = _saved(None)
    saved.verification_notes.append(note)

    document = service_flow._review_document(RUN_ID, saved)

    assert document.last_changes is None
    assert document.verification_notes == (note,)
    assert document.checksum_ok
