"""T066 — `Potenda.apply_plan` applies the saved plan artifact (FR-012, FR-016, FR-020, FR-023).

Rewritten with the v1 dispatch it used to assert. The old file wrote `plan.parquet` and
asserted that a `MagicMock` destination received one per-row call on a write surface no
adapter in the tree ever implemented — removed here as the second apply path FR-019
forbids. What replaces it is the artifact-driven apply: the plan is read from
`<run_dir>/plan/`, verified as one pre-write gate, and executed in **stored order** through
the destination's `apply_planned_operation`.

The destination double below is a plain recording object rather than a `MagicMock`, and
deliberately so: a mock answers `hasattr` for every name, so the missing-write-surface case
— the one this file has to be able to fail on — cannot be expressed against one.

The deep behavioural matrix (peer resolution, the replace-set flush, the rendered-mutation
conformance) belongs to `tests/adapters/test_infrahub_planned_write.py` and
`tests/plan/test_apply_conformance.py`. What is asserted here is the engine's own contract:
stored order, the pre-write gate, the collected delete, and the returned record.
"""

from __future__ import annotations

import logging
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any
from unittest.mock import patch

import pytest

from infrahub_sync.plan.errors import ApplyRecordInvariantError, PlanVerificationError
from infrahub_sync.plan.reader import load_plan_artifact
from infrahub_sync.potenda import Potenda
from tests.plan.artifact_fixtures import CONFIG_VERSION, operation_record, write_artifact

if TYPE_CHECKING:
    from pathlib import Path

    from infrahub_sync.plan.models import PlannedOperation

RUN_ID = "20260727T0915-4c1ab390"


class RecordingDestination:
    """A destination that implements the planned-write surface and records every dispatch."""

    def __init__(self) -> None:
        self.dispatched: list[str] = []

    def apply_planned_operation(self, *, operation: PlannedOperation, peers: Any) -> str:  # noqa: ANN401
        _ = peers
        self.dispatched.append(operation.operation_id)
        return f"node-{len(self.dispatched)}"


class SurfacelessDestination:
    """A destination with no planned-write surface at all — the FR-023 refusal case."""


def _potenda(run_directory: Path, destination: object) -> Potenda:
    """Build a Potenda over `run_directory` with no configuration and no source load."""
    return Potenda(
        source=SimpleNamespace(top_level=[]),  # ty: ignore[invalid-argument-type]
        destination=destination,  # ty: ignore[invalid-argument-type]
        config=None,  # ty: ignore[invalid-argument-type]
        top_level=["BuiltinTag"],
        run_dir=run_directory,
        run_id=RUN_ID,
    )


def _run_dir(tmp_path: Path) -> Path:
    directory = tmp_path / RUN_ID
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def test_apply_plan_executes_the_stored_operations_in_stored_order(tmp_path: Path) -> None:
    """Stored order is executed exactly — no re-sorting and no recomputation (FR-012, SC-001).

    The three operations are written in an order that is **not** their sorted order, so an
    implementation that sorted the operations it read — by identifier, kind or action —
    would dispatch them in a different sequence and fail here rather than passing by
    coincidence.
    """
    directory = _run_dir(tmp_path)
    records = [
        operation_record(identity={"name": "zulu"}),
        operation_record(action="update", identity={"name": "alpha"}),
        operation_record(identity={"name": "mike"}),
    ]
    write_artifact(directory, records, run_id=RUN_ID, source_snapshot=[])
    stored_order = [str(record["operation_id"]) for record in records]
    assert stored_order != sorted(stored_order), "the fixture must not already be in sorted order"

    destination = RecordingDestination()
    record = _potenda(directory, destination).apply_plan(config_version=CONFIG_VERSION)

    assert destination.dispatched == stored_order
    assert record.applied_operations == tuple(stored_order)
    assert record.skipped_delete_operations == ()
    assert record.skipped_delete_count == 0
    # FR-025's last-applied pointer is the final element, not a separate field.
    assert record.applied_operations[-1] == stored_order[-1]


def test_apply_plan_writes_no_run_file(tmp_path: Path) -> None:
    """The engine returns the record and writes nothing — the CLI is the single writer (AD069)."""
    directory = _run_dir(tmp_path)
    write_artifact(directory, [operation_record()], run_id=RUN_ID, source_snapshot=[])

    record = _potenda(directory, RecordingDestination()).apply_plan(config_version=CONFIG_VERSION)

    assert not (directory / "run.json").exists()
    assert record.as_summary_keys() == {
        "applied_operations": list(record.applied_operations),
        "skipped_delete_operations": [],
        "skipped_delete_count": 0,
    }


def test_a_destination_without_the_write_surface_is_refused_before_any_write(tmp_path: Path) -> None:
    """FR-023: the missing surface is refused in the pre-write gate, naming the adapter (AD058)."""
    directory = _run_dir(tmp_path)
    write_artifact(directory, [operation_record()], run_id=RUN_ID, source_snapshot=[])

    with pytest.raises(PlanVerificationError) as caught:
        _potenda(directory, SurfacelessDestination()).apply_plan(config_version=CONFIG_VERSION)

    message = str(caught.value)
    assert "write_surface" in message
    assert "SurfacelessDestination" in message
    assert "infrahub-sync sync" in message


def test_a_recorded_delete_is_collected_and_never_dispatched(tmp_path: Path, caplog) -> None:
    """A delete-bearing plan applies its non-deletes and ends with the skip recorded (AD055).

    Not a failure: applying deletes is out of scope for this release, so the delete is
    collected, the create is still applied, and the count is reported at `logging.WARNING`
    — the level `--quiet` floors the package logger at, so an `INFO` emission would vanish
    for exactly the scripted runs where this warning is the only signal.
    """
    directory = _run_dir(tmp_path)
    create = operation_record(identity={"name": "prod"})
    delete = operation_record(action="delete", identity={"name": "retired"})
    write_artifact(directory, [create, delete], run_id=RUN_ID, source_snapshot=[])

    destination = RecordingDestination()
    with caplog.at_level(logging.DEBUG, logger="infrahub_sync.potenda"):
        record = _potenda(directory, destination).apply_plan(config_version=CONFIG_VERSION)

    assert destination.dispatched == [create["operation_id"]]
    assert record.applied_operations == (create["operation_id"],)
    assert record.skipped_delete_operations == (delete["operation_id"],)
    assert record.skipped_delete_count == 1
    # The knowability invariant, as a value rather than an inference (DBR-016).
    assert set(record.applied_operations) | set(record.skipped_delete_operations) == {
        create["operation_id"],
        delete["operation_id"],
    }

    warnings = [entry for entry in caplog.records if entry.levelno >= logging.WARNING]
    assert len(warnings) == 1
    assert "1" in warnings[0].getMessage()


def test_a_changed_configuration_version_refuses_the_apply(tmp_path: Path) -> None:
    """FR-011: the comparison value reaches the verifier, and is compared for equality only."""
    directory = _run_dir(tmp_path)
    write_artifact(directory, [operation_record()], run_id=RUN_ID, source_snapshot=[])

    destination = RecordingDestination()
    with pytest.raises(PlanVerificationError) as caught:
        _potenda(directory, destination).apply_plan(config_version="a-different-configuration-version")

    assert "config_version" in str(caught.value)
    assert destination.dispatched == []


def test_an_empty_plan_applies_as_a_successful_no_op(tmp_path: Path) -> None:
    """FR-022: zero operations is a success, and verification still runs first (AD033)."""
    directory = _run_dir(tmp_path)
    write_artifact(directory, [], run_id=RUN_ID, source_snapshot=[])

    record = _potenda(directory, RecordingDestination()).apply_plan(config_version=CONFIG_VERSION)

    assert record.applied_operations == ()
    assert record.skipped_delete_count == 0


class InterruptingDestination(RecordingDestination):
    """A destination that applies the first operation and is then interrupted."""

    def apply_planned_operation(self, *, operation: PlannedOperation, peers: Any) -> str:  # noqa: ANN401
        if self.dispatched:
            raise KeyboardInterrupt
        return super().apply_planned_operation(operation=operation, peers=peers)


def test_an_interrupt_mid_apply_propagates_as_itself_and_carries_the_partial_record(tmp_path: Path) -> None:
    """Ctrl-C on a long apply keeps its own type **and** the record of what was written.

    Two claims, and both matter. Converting a `KeyboardInterrupt` into
    `OperationApplyFailedError` would swallow the one signal an operator expects to stop the
    process, so the type is asserted. Re-raising it bare would lose the operations already
    written, which the caller has no other way to learn — so the partial record riding on the
    exception is asserted too (AD062, AD069).
    """
    directory = _run_dir(tmp_path)
    records = [operation_record(identity={"name": "first"}), operation_record(identity={"name": "second"})]
    write_artifact(directory, records, run_id=RUN_ID, source_snapshot=[])
    destination = InterruptingDestination()

    with pytest.raises(KeyboardInterrupt) as caught:
        _potenda(directory, destination).apply_plan(config_version=CONFIG_VERSION)

    assert destination.dispatched == [records[0]["operation_id"]]
    partial = getattr(caught.value, "apply_record", None)
    assert partial is not None, "the interrupt carried no partial record"
    assert partial.applied_operations == (records[0]["operation_id"],)
    assert partial.skipped_delete_operations == ()
    assert partial.skipped_delete_count == 0


def test_the_invariant_error_carries_the_record_of_what_was_actually_written(tmp_path: Path) -> None:
    """AD062's safety net must not zero the record it exists to protect.

    The check runs *after* the loop wrote every non-delete operation, so an error carrying an
    empty record would report a run that wrote everything as having applied nothing — and
    invite a re-apply against a populated destination. The manifest's count is inflated here
    because that is the only clause of the invariant a well-formed artifact can violate.
    """
    directory = _run_dir(tmp_path)
    records = [operation_record(identity={"name": "first"}), operation_record(identity={"name": "second"})]
    write_artifact(directory, records, run_id=RUN_ID, source_snapshot=[])
    destination = RecordingDestination()
    applied_ids = tuple(str(record["operation_id"]) for record in records)

    real_loader = load_plan_artifact

    def _inflated_count(run_dir: Path, **kwargs: Any) -> Any:  # noqa: ANN401 — mirrors the loader
        loaded = real_loader(run_dir, **kwargs)
        loaded.manifest.operations_count += 1
        return loaded

    with (
        patch("infrahub_sync.plan.reader.load_plan_artifact", _inflated_count),
        pytest.raises(ApplyRecordInvariantError) as caught,
    ):
        _potenda(directory, destination).apply_plan(config_version=CONFIG_VERSION)

    assert destination.dispatched == list(applied_ids)
    assert caught.value.apply_record.applied_operations == applied_ids
    assert caught.value.apply_record.skipped_delete_count == 0
