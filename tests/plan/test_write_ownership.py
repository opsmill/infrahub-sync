"""The apply engine may not dispatch a planned operation without a proven write hold.

The boundary is required, not optional: a caller that supplies none is refused where it
calls, before any destination is contacted. What the boundary proves is left to the caller
— these cases pass an explicit fake that records the exact interleaving of proofs and
dispatches, because the ordering *is* the product rule: one proof immediately before every
operation the engine dispatches, and one after the last one, before anything can record the
apply as complete.
"""

from __future__ import annotations

import json
import logging
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

import pytest
from infrahub_sdk.exceptions import ServerNotResponsiveError

from infrahub_sync.cache.sidecars import RunFile
from infrahub_sync.plan.errors import OperationApplyFailedError
from infrahub_sync.plan.models import ApplyRecord
from infrahub_sync.potenda import Potenda
from tests.plan.artifact_fixtures import CONFIG_VERSION, operation_record, write_artifact

if TYPE_CHECKING:
    from pathlib import Path

    from infrahub_sync.plan.models import PlannedOperation

RUN_ID = "20260903T0915-4c1ab390"


class OwnershipLostError(RuntimeError):
    """The failure a fake ownership boundary raises when its hold is gone."""


class RecordingOwnership:
    """An explicit write-ownership boundary that records its own call interleaving."""

    def __init__(self, events: list[str], *, lose_after: int | None = None) -> None:
        self.events = events
        self.applied: ApplyRecord | None = None
        self._lose_after = lose_after
        self._proofs = 0

    def before_operation(self) -> None:
        self._proofs += 1
        if self._lose_after is not None and self._proofs > self._lose_after:
            self.events.append("lost")
            raise OwnershipLostError
        self.events.append("prove")

    def after_final_operation(self) -> None:
        self.events.append("final-prove")

    def record_applied(self, record: ApplyRecord) -> None:
        self.applied = record


class RecordingDestination:
    """A destination implementing the planned-write surface that records every dispatch."""

    def __init__(self, events: list[str]) -> None:
        self.events = events

    def new_peer_resolver(self) -> object:  # noqa: PLR6301
        """The per-apply resolver factory; nothing below this double's surface reads it."""
        return object()

    def apply_planned_operation(self, *, operation: PlannedOperation, peers: Any) -> str:  # noqa: ANN401
        _ = peers
        self.events.append(f"dispatch:{operation.operation_id}")
        return "node"


class RejectingDestination(RecordingDestination):
    """A destination whose one dispatch fails inside the operational boundary."""

    def apply_planned_operation(self, *, operation: PlannedOperation, peers: Any) -> str:  # noqa: ANN401
        super().apply_planned_operation(operation=operation, peers=peers)
        msg = "the destination rejected the operation"
        raise ServerNotResponsiveError(msg)


def _potenda(run_directory: Path, destination: object) -> Potenda:
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


def _three_operations(run_directory: Path) -> list[str]:
    records = [
        operation_record(identity={"name": "alpha"}),
        operation_record(action="delete", identity={"name": "bravo"}),
        operation_record(action="update", identity={"name": "charlie"}),
    ]
    write_artifact(run_directory, records, run_id=RUN_ID, source_snapshot=[])
    return [str(record["operation_id"]) for record in records]


def test_an_apply_without_an_ownership_boundary_is_refused_where_it_is_called(tmp_path: Path) -> None:
    """No default, no None, no no-op: the engine cannot be asked to write unguarded."""
    directory = _run_dir(tmp_path)
    _three_operations(directory)
    events: list[str] = []
    destination = RecordingDestination(events)

    with pytest.raises(TypeError):
        _potenda(directory, destination).apply_plan(config_version=CONFIG_VERSION)  # ty: ignore[missing-argument]

    assert events == []


def test_one_proof_precedes_every_dispatch_and_one_follows_the_last(tmp_path: Path) -> None:
    """The recorded delete is skipped, so it consumes no proof and no dispatch."""
    directory = _run_dir(tmp_path)
    alpha, _bravo, charlie = _three_operations(directory)
    events: list[str] = []
    ownership = RecordingOwnership(events)

    record = _potenda(directory, RecordingDestination(events)).apply_plan(
        config_version=CONFIG_VERSION, ownership=ownership
    )

    assert events == [
        "prove",
        f"dispatch:{alpha}",
        "prove",
        f"dispatch:{charlie}",
        "final-prove",
    ]
    assert record.applied_operations == (alpha, charlie)


def test_ownership_lost_after_the_first_operation_prevents_the_second(tmp_path: Path) -> None:
    """A hold lost mid-apply stops the next write and keeps what the first one did."""
    directory = _run_dir(tmp_path)
    alpha, _bravo, _charlie = _three_operations(directory)
    events: list[str] = []
    ownership = RecordingOwnership(events, lose_after=1)

    with pytest.raises(OwnershipLostError) as raised:
        _potenda(directory, RecordingDestination(events)).apply_plan(config_version=CONFIG_VERSION, ownership=ownership)

    assert events == ["prove", f"dispatch:{alpha}", "lost"]
    carried = getattr(raised.value, "apply_record", None)
    assert isinstance(carried, ApplyRecord)
    assert carried.applied_operations == (alpha,)
    # The proof did not dispatch, so no operation may be named as possibly half-written.
    assert carried.failed_operation is None


def test_a_lost_final_proof_carries_the_completed_record(tmp_path: Path) -> None:
    """Everything the loop dispatched stays readable when the closing proof fails."""
    directory = _run_dir(tmp_path)
    alpha, bravo, charlie = _three_operations(directory)
    events: list[str] = []

    class LosingFinalOwnership(RecordingOwnership):
        def after_final_operation(self) -> None:
            self.events.append("final-lost")
            raise OwnershipLostError

    with pytest.raises(OwnershipLostError) as raised:
        _potenda(directory, RecordingDestination(events)).apply_plan(
            config_version=CONFIG_VERSION, ownership=LosingFinalOwnership(events)
        )

    assert events[-1] == "final-lost"
    carried = getattr(raised.value, "apply_record", None)
    assert isinstance(carried, ApplyRecord)
    assert carried.applied_operations == (alpha, charlie)
    assert carried.skipped_delete_operations == (bravo,)


def test_a_failed_dispatch_still_names_the_operation_that_may_have_written(tmp_path: Path) -> None:
    """The proof boundary must not blur the difference between refused and half-written."""
    directory = _run_dir(tmp_path)
    alpha, _bravo, _charlie = _three_operations(directory)
    events: list[str] = []

    with pytest.raises(OperationApplyFailedError) as raised:
        _potenda(directory, RejectingDestination(events)).apply_plan(
            config_version=CONFIG_VERSION, ownership=RecordingOwnership(events)
        )

    assert raised.value.apply_record.failed_operation == alpha
    assert events == ["prove", f"dispatch:{alpha}"]


def test_a_refused_plan_never_reaches_the_ownership_boundary(tmp_path: Path) -> None:
    """Deterministic refusals decide before the boundary is asked to prove anything."""
    directory = _run_dir(tmp_path)
    _three_operations(directory)
    manifest_path = directory / "plan" / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["operations_count"] = 99
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    events: list[str] = []

    with pytest.raises(Exception):  # noqa: B017 - any refusal; the point is that none proved.
        _potenda(directory, RecordingDestination(events)).apply_plan(
            config_version=CONFIG_VERSION, ownership=RecordingOwnership(events)
        )

    assert events == []


def test_the_apply_seam_cannot_delegate_without_an_ownership_boundary(tmp_path: Path) -> None:
    """`PlanApplier` assembles an apply; it may not assemble one that proves nothing."""
    from infrahub_sync.utils import PlanApplier  # pylint: disable=import-outside-toplevel

    directory = _run_dir(tmp_path)
    _three_operations(directory)
    delegations: list[object] = []

    class _RecordingEngine:
        def __init__(self) -> None:
            self.destination = SimpleNamespace()
            self.last_applied_plan_action_counts = {"create": 0, "update": 0, "delete": 0}

        def apply_plan(self, **kwargs: object) -> ApplyRecord:  # noqa: PLR6301
            delegations.append(kwargs)
            return ApplyRecord()

    applier = PlanApplier(_RecordingEngine(), run_dir=directory, run_id=RUN_ID)  # ty: ignore[invalid-argument-type]

    with pytest.raises(TypeError):
        applier.apply_plan()  # ty: ignore[missing-argument]

    assert delegations == []


def test_the_dispatch_tracker_records_only_a_proven_dispatch() -> None:
    """The one in-memory Boolean separates "certainly wrote nothing" from "may have"."""
    from infrahub_sync.plan.ownership import (  # pylint: disable=import-outside-toplevel
        ProvenWriteOwnership,
        WriteDispatchTracker,
    )

    tracker = WriteDispatchTracker()
    proofs: list[str] = []

    def prove() -> None:
        proofs.append("prove")

    ownership = ProvenWriteOwnership(prove=prove, tracker=tracker)
    assert tracker.dispatch_started is False

    ownership.after_final_operation()
    assert proofs == ["prove"]
    assert tracker.dispatch_started is False, "a closing proof alone is not a dispatch"

    ownership.before_operation()
    assert tracker.dispatch_started is True


def test_a_failed_proof_before_the_first_dispatch_leaves_the_tracker_clean() -> None:
    """A hold lost before any dispatch is a known pre-dispatch failure, not ambiguity."""
    from infrahub_sync.plan.ownership import (  # pylint: disable=import-outside-toplevel
        ProvenWriteOwnership,
        WriteDispatchTracker,
    )

    tracker = WriteDispatchTracker()

    def prove() -> None:
        raise OwnershipLostError

    ownership = ProvenWriteOwnership(prove=prove, tracker=tracker)

    with pytest.raises(OwnershipLostError):
        ownership.before_operation()

    assert tracker.dispatch_started is False


class _EngineApplier:
    """The apply seam over a real engine, so the engine's own proof order is what runs."""

    def __init__(self, engine: Potenda) -> None:
        self.engine = engine

    @property
    def applied_plan_action_counts(self) -> dict[str, int]:
        """Return counts parsed from the artifact the engine just applied."""
        counts = self.engine.last_applied_plan_action_counts
        assert counts is not None
        return counts

    def apply_plan(self, *, ownership: object, **_kwargs: object) -> ApplyRecord:
        """Delegate to the engine exactly as the production seam does."""
        return self.engine.apply_plan(
            ownership=ownership,  # ty: ignore[invalid-argument-type] - the test's explicit boundary.
            config_version=CONFIG_VERSION,
        )


def _lifecycle(directory: Path, ownership: object, destination: object) -> object:
    """Run the core-owned apply lifecycle over one real engine and one real sidecar."""
    from infrahub_sync import execution  # pylint: disable=import-outside-toplevel

    applier = _EngineApplier(_potenda(directory, destination))
    return execution._run_apply_lifecycle(
        sync_instance=SimpleNamespace(name="write-ownership"),  # ty: ignore[invalid-argument-type]
        run_id=RUN_ID,
        branch=None,
        verbosity=logging.INFO,
        allow_destination_change=False,
        expected_checksum=None,
        ownership=ownership,  # ty: ignore[invalid-argument-type]
        _plan_applier_factory=lambda *_args, **_kwargs: applier,  # ty: ignore[invalid-argument-type]
        run_directory=directory,
        sidecar_mode="apply",
    )


def test_the_final_proof_precedes_the_applied_sidecar(tmp_path: Path) -> None:
    """Nothing may record an apply as complete before the closing proof succeeds.

    The proof reads the sidecar the lifecycle owns: seeing `running` there is what shows
    the proof ran while the apply was still open, rather than after it was recorded.
    """
    directory = _run_dir(tmp_path)
    _three_operations(directory)
    events: list[str] = []
    observed: list[str | None] = []

    class WatchingOwnership(RecordingOwnership):
        def after_final_operation(self) -> None:
            observed.append(RunFile.load_or_default(directory / "run.json").status)
            super().after_final_operation()

    _lifecycle(directory, WatchingOwnership(events), RecordingDestination(events))

    assert observed == ["running"]
    assert RunFile.load_or_default(directory / "run.json").status == "applied"
    assert events[-1] == "final-prove"


def test_the_apply_lifecycle_reports_its_completed_record_to_the_write_scope(tmp_path: Path) -> None:
    """The scope learns what was written before anything downstream of the engine can fail.

    Everything after this point — the applied sidecar, the guard's release, the product
    success commit — can fail without carrying a record of its own, and the scope's copy is
    the only account of the write those failures have.
    """
    directory = _run_dir(tmp_path)
    alpha, bravo, charlie = _three_operations(directory)
    events: list[str] = []
    ownership = RecordingOwnership(events)

    _lifecycle(directory, ownership, RecordingDestination(events))

    assert isinstance(ownership.applied, ApplyRecord)
    assert ownership.applied.applied_operations == (alpha, charlie)
    assert ownership.applied.skipped_delete_operations == (bravo,)


def test_a_failed_applied_sidecar_still_carries_the_completed_record(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """What the loop wrote stays readable when the run file cannot record it.

    Only the success transition fails, so the failed transition the lifecycle falls back
    to still runs — and the record travelling on the exception is the completed one, not
    an empty stand-in.
    """
    from infrahub_sync import execution  # pylint: disable=import-outside-toplevel

    directory = _run_dir(tmp_path)
    alpha, bravo, charlie = _three_operations(directory)
    events: list[str] = []
    original = execution._save_run_transition

    def refuse_success(run_directory: Path, **kwargs: Any) -> None:  # noqa: ANN401 - mirrors the wrapped signature.
        if kwargs.get("status") == "applied":
            msg = "the run file could not record the applied transition"
            raise OSError(msg)
        original(run_directory, **kwargs)

    monkeypatch.setattr(execution, "_save_run_transition", refuse_success)

    with pytest.raises(OSError, match="applied transition") as raised:
        _lifecycle(directory, RecordingOwnership(events), RecordingDestination(events))

    carried = getattr(raised.value, "apply_record", None)
    assert isinstance(carried, ApplyRecord)
    assert carried.applied_operations == (alpha, charlie)
    assert carried.skipped_delete_operations == (bravo,)
    assert carried.failed_operation is None
