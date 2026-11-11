"""The plan report records, the load report and the stage failure record (spec 019 T004, T005, T008)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from infrahub_sync.client.models import FAILURE_TYPES, StageFailureResource
from infrahub_sync.load_report import LoadReport, LoadReportEntry, filter_entry, transform_entry
from infrahub_sync.plan.models import (
    PROBLEM_TYPES,
    REPORT_VALUE_LIMIT,
    KeptRecord,
    LeftOutRecord,
    PlanProblem,
    SourceRecordReference,
)

_SITE = SourceRecordReference(kind="LocationSite", identity={"name": "paris"}, source_id="1")


def test_the_nine_problem_type_names_are_stable() -> None:
    assert PROBLEM_TYPES == (
        "dropdown_value_not_in_choices",
        "duplicate_source_identity",
        "destination_identity_collision",
        "wrong_value_type",
        "required_peer_excluded_by_filter",
        "identities_merge_in_destination",
        "peer_not_found_in_source",
        "destination_identity_ambiguous",
        "transform_failed",
    )


def test_a_problem_leaves_out_destination_ids_and_config_entry_when_they_do_not_apply() -> None:
    problem = PlanProblem(problem_type="wrong_value_type", record=_SITE, field="position", value=2.5, reason="r")

    dumped = problem.model_dump()

    assert "destination_ids" not in dumped
    assert "config_entry" not in dumped
    assert dumped["value"] == pytest.approx(2.5)


def test_a_problem_keeps_config_entry_when_it_applies() -> None:
    problem = PlanProblem(
        problem_type="transform_failed", record=_SITE, field="name", config_entry="transforms[0]", reason="r"
    )

    assert problem.model_dump()["config_entry"] == "transforms[0]"
    assert problem.model_dump()["value"] is None


def test_a_problem_limits_string_values() -> None:
    problem = PlanProblem(
        problem_type="dropdown_value_not_in_choices",
        record=_SITE,
        value={"text": "x" * 1000, "items": ["y" * 1000]},
        reason="r",
    )

    assert problem.value == {"text": "x" * REPORT_VALUE_LIMIT, "items": ["y" * REPORT_VALUE_LIMIT]}


def test_a_problem_refuses_an_unknown_type_and_a_long_reason() -> None:
    with pytest.raises(ValidationError):
        PlanProblem(problem_type="unknown", record=_SITE, reason="r")  # ty: ignore[invalid-argument-type]
    with pytest.raises(ValidationError):
        PlanProblem(problem_type="wrong_value_type", record=_SITE, reason="r" * 513)


@pytest.mark.parametrize(
    "fields",
    [
        {"cause": "has_problem", "problem_types": []},
        {"cause": "depends_on_left_out", "depends_on": None},
    ],
)
def test_a_left_out_record_names_what_its_cause_needs(fields: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        LeftOutRecord.model_validate({"record": _SITE.model_dump(), "loaded": False, **fields})


def test_a_left_out_dependent_and_a_kept_record_validate() -> None:
    device = SourceRecordReference(kind="DcimDevice", identity={"name": "core-1"}, source_id="7")

    left_out = LeftOutRecord(
        record=device, cause="depends_on_left_out", depends_on=_SITE, relationship="location", loaded=True
    )
    kept = KeptRecord(record=device, cause="peer_left_out", relationship="platform", left_out_peer=_SITE)

    assert left_out.depends_on == _SITE
    assert kept.left_out_peer == _SITE


def test_a_load_report_entry_limits_its_value_and_names_positions_only() -> None:
    entry = LoadReportEntry(
        record=_SITE, reason="transform_failed", field="name", value="v" * 1000, config_entry=transform_entry(1)
    )

    assert entry.value == "v" * REPORT_VALUE_LIMIT
    assert entry.config_entry == "transforms[1]"
    assert filter_entry(0) == "filters[0]"
    assert filter_entry(None) is None


def test_a_load_report_lists_entries_per_kind_and_filtered_records() -> None:
    report = LoadReport(filtered_ids={"LocationSite": {"3": 0, "4": None}})
    report.add(LoadReportEntry(record=_SITE, reason="duplicate_source_identity", existing=_SITE))

    assert [entry.reason for entry in report.entries_for("LocationSite")] == ["duplicate_source_identity"]
    assert report.entries_for("DcimDevice") == []
    assert report.was_filtered("LocationSite", "4")
    assert not report.was_filtered("LocationSite", "1")
    assert report.filter_position("LocationSite", "3") == 0


def test_the_failure_type_names() -> None:
    assert len(FAILURE_TYPES) == 17
    assert "left_out_review_required" in FAILURE_TYPES
    assert "internal_error" in FAILURE_TYPES


def test_a_stage_failure_record_written_before_spec_019_validates() -> None:
    record = StageFailureResource.model_validate(
        {"stage": "plan", "outcome": "failed", "error_type": "ValueError", "cause_type": "ConnectionError"}
    )

    assert record.failure_type is None
    assert record.system is None


def test_a_stage_failure_record_reads_the_spec_019_fields() -> None:
    record = StageFailureResource.model_validate(
        {
            "stage": "apply",
            "outcome": "failed",
            "error_type": "OperationApplyFailedError",
            "failure_type": "destination_rejected_write",
            "system": {"role": "destination", "adapter": "infrahub"},
            "error_code": "422",
            "node_kind": "DcimDevice",
            "attribute": "name",
            "infrahub_message": "name is not unique",
            "record": {"kind": "DcimDevice", "identity": {"name": "core-1"}, "source_id": "7"},
            "field": "name",
            "operations_total": 12,
            "failed_operation_detail": {"action": "create", "kind": "DcimDevice", "identity": {"name": "core-1"}},
            "applied_operations": 3,
        }
    )

    assert record.system is not None
    assert record.system.role == "destination"
    assert record.failed_operation_detail is not None
    assert record.failed_operation_detail.action == "create"
    assert record.operations_total == 12


def test_a_stage_failure_record_refuses_an_unknown_role_and_a_long_name() -> None:
    with pytest.raises(ValidationError):
        StageFailureResource.model_validate({"system": {"role": "middle", "adapter": "netbox"}})
    with pytest.raises(ValidationError):
        StageFailureResource.model_validate({"node_kind": "K" * 129})
