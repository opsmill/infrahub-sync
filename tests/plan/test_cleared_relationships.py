"""Plan format 4 reserves `cleared_relationships` on an update (decided on 2026-10-17).

Spec 010 will clear a one-peer relationship that the source no longer sets. Plan format 4
reserves the field for it, so no plan format 5 is needed: an update may carry
`cleared_relationships`, a list of relationship names. This release never writes it, and a
plan without it keeps its bytes. Readers accept it. An apply refuses a plan whose update carries
a non-empty `cleared_relationships` before any write, because this release cannot clear a
relationship: ignoring the field would leave the old peer in Infrahub without saying so.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

import pytest

from infrahub_sync.failure_cause import build_failure_cause
from infrahub_sync.plan.canonical import canonical_json_bytes
from infrahub_sync.plan.errors import PlanFeatureNotSupportedError
from infrahub_sync.plan.models import PlannedOperation
from infrahub_sync.plan.reader import load_plan_artifact
from infrahub_sync.plan.writer import _operation_line_mapping  # noqa: PLC2701 - the line encoding under test
from tests.adapters.test_infrahub_planned_write import engine_over as planned_write_engine_over
from tests.plan.artifact_fixtures import CONFIG_VERSION, RUN_ID, operation_record, write_artifact
from tests.plan.test_plan_format_3 import CountingDestination, RecordingOwnership, run_dir

if TYPE_CHECKING:
    from pathlib import Path


def _update(**extra: Any) -> dict[str, Any]:  # noqa: ANN401 - an operation field's value is any JSON value
    return {**operation_record(action="update", payload={"name": "prod", "description": "edge"}), **extra}


def test_an_update_accepts_and_round_trips_cleared_relationships() -> None:
    operation = PlannedOperation.model_validate(_update(cleared_relationships=["site"]))

    assert operation.cleared_relationships == ["site"]
    line = _operation_line_mapping(operation)
    assert line["cleared_relationships"] == ["site"]
    assert PlannedOperation.model_validate(json.loads(canonical_json_bytes(line))) == operation


def test_an_operation_without_the_field_keeps_its_bytes() -> None:
    record = _update()
    operation = PlannedOperation.model_validate(record)

    assert operation.cleared_relationships is None
    assert "cleared_relationships" not in operation.model_dump()
    assert "cleared_relationships" not in _operation_line_mapping(operation)
    assert canonical_json_bytes(_operation_line_mapping(operation)) == canonical_json_bytes(record)


@pytest.mark.parametrize("action", ["create", "delete"])
def test_only_an_update_may_carry_cleared_relationships(action: str) -> None:
    record = {**operation_record(action=action), "cleared_relationships": ["site"]}

    with pytest.raises(ValueError, match="cleared_relationships"):
        PlannedOperation.model_validate(record)


def test_a_format_4_reader_accepts_the_field(tmp_path: Path) -> None:
    directory = run_dir(tmp_path)
    write_artifact(directory, [_update(cleared_relationships=["site"])], source_snapshot=[])

    loaded = load_plan_artifact(directory)

    assert [operation.cleared_relationships for operation in loaded.operations] == [["site"]]


def test_an_apply_refuses_a_plan_that_clears_a_relationship_before_any_write(tmp_path: Path) -> None:
    directory = run_dir(tmp_path)
    write_artifact(directory, [operation_record(), _update(cleared_relationships=["site"])], source_snapshot=[])
    destination = CountingDestination()
    ownership = RecordingOwnership()

    with pytest.raises(PlanFeatureNotSupportedError) as excinfo:
        planned_write_engine_over(directory, destination, run_id=RUN_ID).apply_plan(
            ownership=ownership, config_version=CONFIG_VERSION, confirm_left_out=True
        )

    assert "cleared_relationships" in str(excinfo.value)
    assert excinfo.value.wrote is False
    assert ownership.proofs == 0
    assert destination.applied == []


def test_an_empty_list_clears_nothing_and_applies(tmp_path: Path) -> None:
    directory = run_dir(tmp_path)
    write_artifact(directory, [_update(cleared_relationships=[])], source_snapshot=[])
    destination = CountingDestination()

    record = planned_write_engine_over(directory, destination, run_id=RUN_ID).apply_plan(
        ownership=RecordingOwnership(), config_version=CONFIG_VERSION, confirm_left_out=False
    )

    assert len(record.applied_operations) == 1


def test_the_refusal_is_an_apply_refused_cause_without_the_error_text() -> None:
    error = PlanFeatureNotSupportedError(
        "operation op_1 holds 'cleared_relationships'", feature="cleared_relationships"
    )

    cause = build_failure_cause("apply", error)

    assert cause.failure_type == "apply_refused"
    assert "op_1" not in cause.model_dump_json()
