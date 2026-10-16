"""An apply confirms the plan's left-out count and applies format 4 plans only (spec 009 T058).

`Potenda.apply_plan` compares the confirmed count with `manifest.report.left_out` after the
artifact is parsed and before the first ownership proof, so a refusal writes nothing. A plan
saved in format 2 or 3 records no report, so it is refused with
`PlanFormatApplyUnsupportedError` and the instruction to plan again (research R11, ADR 0022).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from infrahub_sync.plan.errors import LeftOutConfirmationError, PlanFormatApplyUnsupportedError
from tests.adapters.test_infrahub_planned_write import engine_over as planned_write_engine_over
from tests.plan.artifact_fixtures import CONFIG_VERSION, RUN_ID, operation_record, write_artifact
from tests.plan.problem_plan import RUN_ID as FIXTURE_RUN_ID
from tests.plan.problem_plan import plan_fixture
from tests.plan.test_plan_format_3 import CountingDestination, RecordingOwnership, run_dir

if TYPE_CHECKING:
    from pathlib import Path

    from tests.plan.problem_plan import PlannedFixture


@pytest.fixture
def planned(tmp_path: Path) -> PlannedFixture:
    """The problem fixture planned once: it leaves out records and keeps one without a link."""
    fixture = plan_fixture(tmp_path / "problem-run")
    assert fixture.manifest.report is not None
    assert fixture.manifest.report.left_out > 0, "The fixture must leave out records."
    return fixture


def _apply(fixture: PlannedFixture, destination: object, ownership: RecordingOwnership, count: int | None) -> object:
    engine = planned_write_engine_over(fixture.run_dir, destination, run_id=FIXTURE_RUN_ID)
    return engine.apply_plan(
        ownership=ownership,
        config_version=fixture.manifest.config_version,
        confirmed_left_out_count=count,
    )


@pytest.mark.parametrize("delta", [None, -1, 1], ids=["absent", "one-less", "one-more"])
def test_a_missing_or_wrong_count_is_refused_before_the_first_ownership_proof(
    planned: PlannedFixture, delta: int | None
) -> None:
    assert planned.manifest.report is not None
    left_out = planned.manifest.report.left_out
    count = None if delta is None else left_out + delta
    destination = CountingDestination()
    ownership = RecordingOwnership()

    with pytest.raises(LeftOutConfirmationError) as excinfo:
        _apply(planned, destination, ownership, count)

    assert excinfo.value.expected_left_out_count == left_out
    assert f"leaves out {left_out} records" in str(excinfo.value)
    assert excinfo.value.wrote is False
    assert ownership.proofs == 0, "No hold was proven, so nothing was dispatched."
    assert destination.applied == []


def test_the_right_count_applies_only_the_plan_operations(planned: PlannedFixture) -> None:
    assert planned.manifest.report is not None
    destination = CountingDestination()
    ownership = RecordingOwnership()

    record = _apply(planned, destination, ownership, planned.manifest.report.left_out)

    expected = [operation.operation_id for operation in planned.operations if operation.action != "delete"]
    assert destination.applied == expected
    assert list(record.applied_operations) == expected  # ty: ignore[unresolved-attribute]
    left_out_identities = {
        (line["record"]["kind"], repr(line["record"]["identity"]))
        for line in planned.report.entries()
        if line["entry"] == "left_out"
    }
    written_identities = {
        (operation.kind, repr(operation.identity))
        for operation in planned.operations
        if operation.operation_id in destination.applied
    }
    assert not left_out_identities & written_identities, "No operation writes a left-out record."


@pytest.mark.parametrize("count", [None, 0])
def test_a_plan_that_leaves_out_nothing_applies_without_a_count(tmp_path: Path, count: int | None) -> None:
    directory = run_dir(tmp_path)
    write_artifact(directory, [operation_record()], source_snapshot=[])
    destination = CountingDestination()

    record = planned_write_engine_over(directory, destination, run_id=RUN_ID).apply_plan(
        ownership=RecordingOwnership(), config_version=CONFIG_VERSION, confirmed_left_out_count=count
    )

    assert len(record.applied_operations) == 1


def test_a_count_for_a_plan_that_leaves_out_nothing_must_be_zero(tmp_path: Path) -> None:
    directory = run_dir(tmp_path)
    write_artifact(directory, [operation_record()], source_snapshot=[])
    ownership = RecordingOwnership()

    with pytest.raises(LeftOutConfirmationError) as excinfo:
        planned_write_engine_over(directory, CountingDestination(), run_id=RUN_ID).apply_plan(
            ownership=ownership, config_version=CONFIG_VERSION, confirmed_left_out_count=3
        )

    assert excinfo.value.expected_left_out_count == 0
    assert ownership.proofs == 0


@pytest.mark.parametrize("count", [None, 0])
def test_a_format_3_plan_is_refused_with_the_instruction_to_plan_again(tmp_path: Path, count: int | None) -> None:
    directory = run_dir(tmp_path)
    write_artifact(directory, [operation_record()], format_version=3, source_snapshot=[])
    destination = CountingDestination()
    ownership = RecordingOwnership()

    with pytest.raises(PlanFormatApplyUnsupportedError) as excinfo:
        planned_write_engine_over(directory, destination, run_id=RUN_ID).apply_plan(
            ownership=ownership, config_version=CONFIG_VERSION, confirmed_left_out_count=count
        )

    message = str(excinfo.value)
    assert "format version 3" in message
    assert "plan again" in message.lower()
    assert "not touched" in message
    assert ownership.proofs == 0
    assert destination.applied == []
