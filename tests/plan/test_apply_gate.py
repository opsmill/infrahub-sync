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

import re
from typing import TYPE_CHECKING

import pytest

from infrahub_sync.plan.errors import LeftOutConfirmationRequiredError
from infrahub_sync.plan.models import REPORT_FILE_NAME, LeftOutRecord, PlanProblem, PlanReport, SourceRecordReference
from tests.plan.artifact_fixtures import CONFIG_VERSION, plan_dir, write_artifact
from tests.plan.problem_plan import plan_fixture
from tests.plan.test_plan_format_3 import CountingDestination, RecordingOwnership, engine_over, run_dir, update_record

if TYPE_CHECKING:
    from pathlib import Path

    from infrahub_sync.plan.models import ApplyRecord
    from tests.plan.conftest import PlanProblemFixture


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
    assert re.search(r"\b2 records\b", message), "The refusal states the count of records left out."
    next_action = str(getattr(outcome, "next_action", ""))
    assert "confirm" in f"{message} {next_action}".lower()


def test_a_confirmed_apply_of_a_plan_that_leaves_out_records_writes_its_operations(tmp_path: Path) -> None:
    directory = _write_plan(tmp_path, _report())
    ownership = RecordingOwnership()
    destination = CountingDestination()

    record = engine_over(directory, destination=destination).apply_plan(
        ownership=ownership,
        config_version=CONFIG_VERSION,
        confirm_left_out=True,
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


def test_a_confirmation_sent_for_a_plan_that_leaves_out_nothing_is_accepted_and_ignored(tmp_path: Path) -> None:
    """FR-011: the confirmation is accepted and changes nothing."""
    directory = _write_plan(tmp_path, None)
    destination = CountingDestination()

    record = engine_over(directory, destination=destination).apply_plan(
        ownership=RecordingOwnership(),
        config_version=CONFIG_VERSION,
        confirm_left_out=True,
    )

    assert len(record.applied_operations) == 1


# --- US2 independent test on the US1 fixture plan ------------------------------------------------


class _ReachedTheWrite(Exception):  # noqa: N818 - a sentinel, not an error condition.
    """Raised by the ownership boundary when the apply reaches its first operation."""


class _StopAtFirstOperation:
    """A write-ownership boundary that stops the apply at its first operation, so nothing is written."""

    proofs = 0

    def before_operation(self) -> None:
        self.proofs += 1
        raise _ReachedTheWrite

    @staticmethod
    def after_final_operation() -> None:
        raise _ReachedTheWrite


def test_the_us1_fixture_plan_needs_the_confirmation_and_then_reaches_the_write(
    plan_problems: PlanProblemFixture, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """tasks.md US2 "Independent test", engine side: the US1 plan without and with the confirmation.

    Without it, the apply is refused with the plan's own count before any operation. With it,
    the apply passes the left-out gate and reaches its first operation, where the stand-in
    ownership boundary stops it: the stand-in destination cannot take writes. The stand-in
    client serves no live schema, so the Infrahub payload check, which runs after the left-out
    gate, is replaced by a no-op.
    """
    monkeypatch.setenv("INFRAHUB_SYNC_CACHE_DIR", str(tmp_path / "cache"))
    planned = plan_fixture(plan_problems)
    left_out = planned.manifest["report"]["left_out"]
    assert left_out > 0
    checksum = planned.manifest["plan_checksum"]
    refused_ownership = _StopAtFirstOperation()

    with pytest.raises(LeftOutConfirmationRequiredError) as refused:
        planned.potenda.apply_plan(ownership=refused_ownership, expected_checksum=checksum)

    assert refused.value.left_out == left_out
    assert f"{left_out} records" in str(refused.value)
    assert refused_ownership.proofs == 0
    monkeypatch.setattr(planned.potenda.destination, "validate_planned_payload_fields", lambda _operations: None)
    confirmed_ownership = _StopAtFirstOperation()

    with pytest.raises(_ReachedTheWrite):
        planned.potenda.apply_plan(ownership=confirmed_ownership, expected_checksum=checksum, confirm_left_out=True)

    assert confirmed_ownership.proofs == 1
