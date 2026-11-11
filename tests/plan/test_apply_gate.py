"""The engine's own check of the left-out confirmation, for callers that skip the Sync API.

Spec 019 FR-010 and FR-011, research R10 and T042. The Sync API refuses an apply of a plan that
leaves out records unless the request confirms it. The engine checks the same thing again from
the plan manifest, so a direct Python caller of `Potenda.apply_plan` cannot skip it: an apply of
a plan whose report leaves out records needs `confirm_left_out=True`, and is refused before the
first ownership proof, so before any operation is dispatched. A plan that leaves out nothing
applies with or without the confirmation.

The refusal's exception class is not named by the specification. These tests require only that
the refusal happens before any dispatch and that its message and next action name the count and
the confirmation.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from infrahub_sync.plan.models import REPORT_FILE_NAME, LeftOutRecord, PlanProblem, PlanReport, SourceRecordReference
from tests.plan.artifact_fixtures import CONFIG_VERSION, plan_dir, write_artifact
from tests.plan.test_plan_format_3 import CountingDestination, RecordingOwnership, engine_over, run_dir, update_record

if TYPE_CHECKING:
    from pathlib import Path

    from infrahub_sync.plan.models import ApplyRecord

_NOT_IMPLEMENTED = "spec 019 US2 not implemented"

_SITE = SourceRecordReference(kind="LocationSite", identity={"name": "paris"}, source_id="1")
_DEVICE = SourceRecordReference(kind="DcimDevice", identity={"name": "core-1"}, source_id="7")


def _report() -> PlanReport:
    """A report that leaves out two records: one with a problem and one that depends on it."""
    return PlanReport(
        problems=(
            PlanProblem(
                problem_type="wrong_value_type",
                record=_SITE,
                field="position",
                value=2.5,
                reason="LocationSite paris: position 2.5 is not an integer",
            ),
        ),
        left_out=(
            LeftOutRecord(record=_SITE, cause="has_problem", problem_types=["wrong_value_type"], loaded=True),
            LeftOutRecord(
                record=_DEVICE, cause="depends_on_left_out", depends_on=_SITE, relationship="location", loaded=True
            ),
        ),
    )


def _write_plan(tmp_path: Path, report: PlanReport | None) -> Path:
    """A format 4 plan with one update, whose report is `report` (an empty report when `None`)."""
    directory = run_dir(tmp_path)
    if report is None:
        write_artifact(directory, [update_record()], source_snapshot=[])
        return directory
    encoded = report.encode()
    write_artifact(directory, [update_record()], source_snapshot=[], report=report.record(encoded).model_dump())
    (plan_dir(directory) / REPORT_FILE_NAME).write_bytes(encoded)
    return directory


def _attempt(
    directory: Path, ownership: RecordingOwnership, destination: CountingDestination
) -> ApplyRecord | Exception:
    """Apply without a confirmation, and return what the engine returned or raised."""
    try:
        return engine_over(directory, destination=destination).apply_plan(
            ownership=ownership, config_version=CONFIG_VERSION
        )
    except Exception as exc:  # noqa: BLE001 - the case asserts on what was raised.
        return exc


@pytest.mark.xfail(strict=True, raises=AssertionError, reason=_NOT_IMPLEMENTED)
def test_an_unconfirmed_apply_of_a_plan_that_leaves_out_records_is_refused_before_any_dispatch(
    tmp_path: Path,
) -> None:
    directory = _write_plan(tmp_path, _report())
    ownership = RecordingOwnership()
    destination = CountingDestination()

    outcome = _attempt(directory, ownership, destination)

    assert isinstance(outcome, Exception), "The engine applied a plan that leaves out records without a confirmation."
    assert ownership.proofs == 0, "No hold was proven, so no operation was dispatched."
    assert destination.applied == []
    message = str(outcome)
    assert "2" in message, "The refusal states the count of records left out."
    next_action = str(getattr(outcome, "next_action", ""))
    assert "confirm" in f"{message} {next_action}".lower()


@pytest.mark.xfail(strict=True, raises=TypeError, reason=_NOT_IMPLEMENTED)
def test_a_confirmed_apply_of_a_plan_that_leaves_out_records_writes_its_operations(tmp_path: Path) -> None:
    directory = _write_plan(tmp_path, _report())
    ownership = RecordingOwnership()
    destination = CountingDestination()

    record = engine_over(directory, destination=destination).apply_plan(
        ownership=ownership,
        config_version=CONFIG_VERSION,
        confirm_left_out=True,  # ty: ignore[unknown-argument] - added by T042.
    )

    assert len(record.applied_operations) == 1
    assert len(destination.applied) == 1


def test_a_plan_that_leaves_out_nothing_applies_without_a_confirmation(tmp_path: Path) -> None:
    """FR-011: no confirmation is asked for when nothing is left out."""
    directory = _write_plan(tmp_path, None)
    destination = CountingDestination()

    record = engine_over(directory, destination=destination).apply_plan(
        ownership=RecordingOwnership(), config_version=CONFIG_VERSION
    )

    assert len(record.applied_operations) == 1


@pytest.mark.xfail(strict=True, raises=TypeError, reason=_NOT_IMPLEMENTED)
def test_a_confirmation_sent_for_a_plan_that_leaves_out_nothing_is_accepted_and_ignored(tmp_path: Path) -> None:
    """FR-011: the confirmation is accepted and changes nothing."""
    directory = _write_plan(tmp_path, None)
    destination = CountingDestination()

    record = engine_over(directory, destination=destination).apply_plan(
        ownership=RecordingOwnership(),
        config_version=CONFIG_VERSION,
        confirm_left_out=True,  # ty: ignore[unknown-argument] - added by T042.
    )

    assert len(record.applied_operations) == 1
