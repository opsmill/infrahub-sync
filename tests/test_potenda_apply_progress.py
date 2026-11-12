"""What `Potenda.apply_plan` reports to an `ApplyProgressObserver`, and where a resume starts.

The ordering is the product rule (spec 022 FR-002): an operation is reported as committed
only after its destination call returned and the apply recorded it, so a saved position never
runs ahead of a commit. These cases pass explicit fakes that record the exact interleaving of
observer calls, proofs and dispatches.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

import pytest
from infrahub_sdk.exceptions import ServerNotResponsiveError

from infrahub_sync.plan.errors import OperationApplyFailedError, PlanVerificationError
from infrahub_sync.plan.models import ApplyRecord
from infrahub_sync.potenda import Potenda
from infrahub_sync.potenda.apply_progress import ApplyProgressLostError
from infrahub_sync.utils import PlanApplier
from tests.plan.artifact_fixtures import CONFIG_VERSION, operation_record, write_artifact

if TYPE_CHECKING:
    from pathlib import Path

    from infrahub_sync.plan.models import PlannedOperation

RUN_ID = "20261112T2215-5d2bc401"


class RecordingOwnership:
    """A write-ownership boundary that records each proof."""

    def __init__(self, events: list[str]) -> None:
        self.events = events

    def before_operation(self) -> None:
        self.events.append("prove")

    def after_final_operation(self) -> None:
        self.events.append("final-prove")


class RecordingDestination:
    """A destination implementing the planned-write surface that records every dispatch."""

    def __init__(self, events: list[str], *, fail_on: str | None = None) -> None:
        self.events = events
        self._fail_on = fail_on

    def new_peer_resolver(self) -> object:  # noqa: PLR6301
        """The per-apply resolver factory; nothing below this double's surface reads it."""
        return object()

    def apply_planned_operation(self, *, operation: PlannedOperation, peers: Any) -> str:  # noqa: ANN401
        _ = peers
        self.events.append(f"dispatch:{operation.operation_id}")
        if operation.operation_id == self._fail_on:
            msg = "the destination rejected the operation"
            raise ServerNotResponsiveError(msg)
        return "node"


class RecordingProgress:
    """An `ApplyProgressObserver` that records every call, and stops when told to."""

    def __init__(self, events: list[str], *, stop_after_commits: int | None = None) -> None:
        self.events = events
        self._stop_after_commits = stop_after_commits
        self._commits = 0

    def committed(self, index: int, operation: PlannedOperation) -> None:
        self._commits += 1
        self.events.append(f"committed:{index}:{operation.operation_id}")

    def kind_finished(self, index: int) -> None:
        self.events.append(f"kind-finished:{index}")

    def stopped(self, index: int) -> None:
        self.events.append(f"stopped:{index}")

    def should_stop(self) -> bool:
        stop = self._stop_after_commits is not None and self._commits >= self._stop_after_commits
        self.events.append(f"should-stop:{stop}")
        return stop


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


def _plan(run_directory: Path) -> list[str]:
    """Write five operations over two kinds, with a recorded delete at position 1.

    Positions: 0 tag alpha (create), 1 tag bravo (delete), 2 tag charlie (update),
    3 status delta (create), 4 status echo (create).
    """
    records = [
        operation_record(identity={"name": "alpha"}),
        operation_record(action="delete", identity={"name": "bravo"}),
        operation_record(action="update", identity={"name": "charlie"}),
        operation_record(kind="BuiltinStatus", identity={"name": "delta"}),
        operation_record(kind="BuiltinStatus", identity={"name": "echo"}),
    ]
    write_artifact(run_directory, records, run_id=RUN_ID, source_snapshot=[])
    return [str(record["operation_id"]) for record in records]


def test_each_commit_is_reported_after_its_dispatch_and_each_kind_boundary_is_reported(tmp_path: Path) -> None:
    """`committed` follows the dispatch; the delete is never committed; both kinds finish."""
    directory = _run_dir(tmp_path)
    alpha, bravo, charlie, delta, echo = _plan(directory)
    events: list[str] = []

    record = _potenda(directory, RecordingDestination(events)).apply_plan(
        config_version=CONFIG_VERSION, ownership=RecordingOwnership(events), progress=RecordingProgress(events)
    )

    assert events == [
        "should-stop:False",
        "prove",
        f"dispatch:{alpha}",
        f"committed:0:{alpha}",
        "should-stop:False",
        "prove",
        f"dispatch:{charlie}",
        f"committed:2:{charlie}",
        "kind-finished:2",
        "should-stop:False",
        "prove",
        f"dispatch:{delta}",
        f"committed:3:{delta}",
        "should-stop:False",
        "prove",
        f"dispatch:{echo}",
        f"committed:4:{echo}",
        "kind-finished:4",
        "final-prove",
    ]
    assert record == ApplyRecord(applied_operations=(alpha, charlie, delta, echo), skipped_delete_operations=(bravo,))


def test_an_operation_whose_destination_call_raised_is_never_reported_as_committed(tmp_path: Path) -> None:
    """The failed operation is reported as the stop, never as a commit (FR-002)."""
    directory = _run_dir(tmp_path)
    alpha, _bravo, charlie, _delta, _echo = _plan(directory)
    events: list[str] = []

    with pytest.raises(OperationApplyFailedError) as raised:
        _potenda(directory, RecordingDestination(events, fail_on=charlie)).apply_plan(
            config_version=CONFIG_VERSION, ownership=RecordingOwnership(events), progress=RecordingProgress(events)
        )

    committed = [event for event in events if event.startswith("committed:")]
    assert committed == [f"committed:0:{alpha}"]
    assert events[-2:] == [f"dispatch:{charlie}", "stopped:2"]
    assert raised.value.apply_record.failed_operation == charlie


def test_should_stop_raises_progress_lost_with_the_partial_record_before_the_next_dispatch(tmp_path: Path) -> None:
    """A position that can no longer be saved stops the next write; the record keeps what was written."""
    directory = _run_dir(tmp_path)
    alpha, bravo, charlie, _delta, _echo = _plan(directory)
    events: list[str] = []

    with pytest.raises(ApplyProgressLostError) as raised:
        _potenda(directory, RecordingDestination(events)).apply_plan(
            config_version=CONFIG_VERSION,
            ownership=RecordingOwnership(events),
            progress=RecordingProgress(events, stop_after_commits=1),
        )

    # Stopped before the proof and the dispatch of `charlie`, so neither happened.
    assert events[-3:] == [f"committed:0:{alpha}", "should-stop:True", "stopped:2"]
    assert f"dispatch:{charlie}" not in events
    carried = raised.value.apply_record
    assert carried.applied_operations == (alpha,)
    assert carried.skipped_delete_operations == (bravo,)
    # Nothing was dispatched for the stopped operation, so none is named as possibly half-written.
    assert carried.failed_operation is None
    assert carried.operations_total == 5


def test_start_after_dispatches_only_the_operations_after_that_position(tmp_path: Path) -> None:
    """A resume after position 2 skips alpha, the bravo delete and charlie, and records only what it did."""
    directory = _run_dir(tmp_path)
    _alpha, _bravo, _charlie, delta, echo = _plan(directory)
    events: list[str] = []

    record = _potenda(directory, RecordingDestination(events)).apply_plan(
        config_version=CONFIG_VERSION,
        ownership=RecordingOwnership(events),
        progress=RecordingProgress(events),
        start_after=2,
    )

    assert [event for event in events if event.startswith("dispatch:")] == [f"dispatch:{delta}", f"dispatch:{echo}"]
    assert [event for event in events if event.startswith("committed:")] == [
        f"committed:3:{delta}",
        f"committed:4:{echo}",
    ]
    assert record == ApplyRecord(applied_operations=(delta, echo), skipped_delete_operations=())


def test_start_after_still_runs_every_pre_write_check_before_any_dispatch(tmp_path: Path) -> None:
    """A resume is refused by the same gate as a first apply, and writes nothing."""
    directory = _run_dir(tmp_path)
    _plan(directory)
    events: list[str] = []

    with pytest.raises(PlanVerificationError):
        _potenda(directory, RecordingDestination(events)).apply_plan(
            config_version="another-configuration-version",
            ownership=RecordingOwnership(events),
            progress=RecordingProgress(events),
            start_after=2,
        )

    assert events == []


@pytest.mark.parametrize("start_after", [-1, 5])
def test_a_start_after_outside_the_plan_is_refused_before_any_dispatch(tmp_path: Path, start_after: int) -> None:
    """A position the plan does not hold cannot name where a resume starts."""
    directory = _run_dir(tmp_path)
    _plan(directory)
    events: list[str] = []

    with pytest.raises(ValueError, match="Cannot resume the apply"):
        _potenda(directory, RecordingDestination(events)).apply_plan(
            config_version=CONFIG_VERSION, ownership=RecordingOwnership(events), start_after=start_after
        )

    assert events == []


def test_without_an_observer_the_apply_is_unchanged(tmp_path: Path) -> None:
    """No observer and no start position apply the whole plan exactly as before."""
    directory = _run_dir(tmp_path)
    alpha, bravo, charlie, delta, echo = _plan(directory)
    events: list[str] = []

    record = _potenda(directory, RecordingDestination(events)).apply_plan(
        config_version=CONFIG_VERSION, ownership=RecordingOwnership(events)
    )

    assert events == [
        "prove",
        f"dispatch:{alpha}",
        "prove",
        f"dispatch:{charlie}",
        "prove",
        f"dispatch:{delta}",
        "prove",
        f"dispatch:{echo}",
        "final-prove",
    ]
    assert record == ApplyRecord(applied_operations=(alpha, charlie, delta, echo), skipped_delete_operations=(bravo,))


def test_the_plan_applier_passes_the_observer_and_the_start_position_to_the_engine(tmp_path: Path) -> None:
    """`PlanApplier.apply_plan` forwards both, so the service worker reaches the engine through it."""
    directory = _run_dir(tmp_path)
    _alpha, _bravo, _charlie, _delta, echo = _plan(directory)
    events: list[str] = []
    applier = PlanApplier(_potenda(directory, RecordingDestination(events)), run_dir=directory, run_id=RUN_ID)

    record = applier.apply_plan(
        config_version=CONFIG_VERSION,
        ownership=RecordingOwnership(events),
        progress=RecordingProgress(events),
        start_after=3,
    )

    assert record.applied_operations == (echo,)
    assert f"committed:4:{echo}" in events
