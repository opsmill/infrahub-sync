"""The timings of the plan step: the engine reports, the worker stores, the response derives (spec 019, US3).

Covers the derivation of research R11 and R13 (T038), the engine's plan step observer, the
worker's observer and the stage wiring (T039).
"""

from __future__ import annotations

import contextlib
import dataclasses
import logging
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any, cast

import pytest

from infrahub_sync.plan.ownership import WriteDispatchTracker
from infrahub_sync.product_store import PrefectExecutionLink, ProductProjection
from infrahub_sync.product_store.store import FileArtifactStore, SQLiteRunStore
from infrahub_sync.service import flow as service_flow
from infrahub_sync.service.plan_step import (
    RESPONSE_PARTS,
    PlanStepPartTimes,
    PlanStepState,
    StorePlanStepObserver,
    stage_plan_step,
)
from infrahub_sync.service.scratch import stage_scratch
from tests.plan.conftest import load_plan_problem_fixture
from tests.plan.problem_plan import build_potenda, finish_plan
from tests.product_store.test_contract import _run
from tests.service.execution_fixtures import append_execution, bind_granting_guard

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator, Mapping
    from pathlib import Path

    from infrahub_sync.potenda.plan_step import PlanStepPartName

_RUN_ID = "run-001"
_FLOW_RUN_ID = "flow-plan-1"
_WORKER_ID = "22222222-2222-2222-2222-222222222222"
_CANARY = "db.internal.example:5432 password=ZZ-PLAN-STEP-CANARY"
_SERVICE_LOGGER = "infrahub_sync.service.plan_step"
_ENGINE_LOGGER = "infrahub_sync.potenda"

_T0 = datetime(2026, 11, 16, 10, 0, 0, tzinfo=timezone.utc)


def _at(seconds: float) -> datetime:
    return _T0 + timedelta(seconds=seconds)


def _iso(seconds: float) -> str:
    return _at(seconds).isoformat()


_SUBMITTED, _CLAIMED = _at(0), _at(4)
_SOURCE_READ = {"state": "succeeded", "started_at": _iso(9), "ended_at": _iso(20), "read_mode": "full"}
_DESTINATION_READ = {"state": "succeeded", "started_at": _iso(10), "ended_at": _iso(25), "read_mode": "full"}
_SAVED_DOCUMENT = {
    "parts": {
        "comparing": {"started_at": _iso(25), "ended_at": _iso(30)},
        "checking": {"started_at": _iso(30), "ended_at": _iso(31)},
        "saving": {"started_at": _iso(31), "ended_at": _iso(33)},
    }
}


def _link(plan_step: Mapping[str, Any] | None, **fields: Any) -> PrefectExecutionLink:  # noqa: ANN401
    values: dict[str, Any] = {
        "flow_run_id": _FLOW_RUN_ID,
        "purpose": "plan",
        "attempt": 1,
        "submitted_at": _SUBMITTED,
        "claimed_at": _CLAIMED,
        "source_read": _SOURCE_READ,
        "destination_read": _DESTINATION_READ,
        "plan_step": None if plan_step is None else dict(plan_step),
    }
    values.update(fields)
    if values["claimed_at"] is not None:
        values["claiming_worker_id"] = _WORKER_ID
    return PrefectExecutionLink(**values)


def _failed(**fields: Any) -> dict[str, Any]:  # noqa: ANN401
    return {"terminal_at": _at(40), "terminal_state": "failed", "terminal_outcome": "failed", **fields}


def _completed() -> dict[str, Any]:
    return {"terminal_at": _at(40), "terminal_state": "completed", "terminal_outcome": "succeeded"}


# --- T038: the derivation of the run response (research R11, R13) -------------------------------


def test_an_execution_with_no_document_has_no_plan_step() -> None:
    assert stage_plan_step(_link(None), run_id=_RUN_ID) is None


def test_a_finished_plan_step_returns_every_part_in_order_with_its_times() -> None:
    state = stage_plan_step(_link(_SAVED_DOCUMENT, **_completed()), run_id=_RUN_ID)

    assert state is not None
    assert state == PlanStepState(
        running=None,
        parts={
            "waiting": PlanStepPartTimes(started_at=_SUBMITTED, ended_at=_CLAIMED),
            # From the claim to the earlier read start: the source read started first.
            "preparing": PlanStepPartTimes(started_at=_CLAIMED, ended_at=_at(9)),
            "comparing": PlanStepPartTimes(started_at=_at(25), ended_at=_at(30)),
            "checking": PlanStepPartTimes(started_at=_at(30), ended_at=_at(31)),
            "saving": PlanStepPartTimes(started_at=_at(31), ended_at=_at(33)),
        },
    )
    assert list(state.parts) == list(RESPONSE_PARTS)


def test_waiting_is_returned_only_when_the_claim_is_not_before_the_submission() -> None:
    skewed = stage_plan_step(_link(_SAVED_DOCUMENT, submitted_at=_at(5), claimed_at=_at(4)), run_id=_RUN_ID)

    assert skewed is not None
    assert "waiting" not in skewed.parts


def test_an_unclaimed_execution_returns_neither_waiting_nor_preparing() -> None:
    state = stage_plan_step(
        _link({"parts": {}}, claimed_at=None, source_read=None, destination_read=None), run_id=_RUN_ID
    )

    assert state == PlanStepState(running=None, parts={})


def test_preparing_ends_at_the_earlier_read_start_whichever_side_started_first() -> None:
    state = stage_plan_step(_link({"parts": {}}, source_read={**_SOURCE_READ, "started_at": _iso(12)}), run_id=_RUN_ID)

    assert state is not None
    assert state.parts["preparing"] == PlanStepPartTimes(started_at=_CLAIMED, ended_at=_at(10))


def test_preparing_runs_while_no_side_has_started_its_read() -> None:
    state = stage_plan_step(
        _link({"parts": {}}, source_read={"state": "waiting"}, destination_read={"state": "waiting"}), run_id=_RUN_ID
    )

    assert state == PlanStepState(
        running="preparing",
        parts={
            "waiting": PlanStepPartTimes(started_at=_SUBMITTED, ended_at=_CLAIMED),
            "preparing": PlanStepPartTimes(started_at=_CLAIMED),
        },
    )


def test_a_read_that_started_before_the_claim_gives_no_preparing() -> None:
    state = stage_plan_step(_link({"parts": {}}, claimed_at=_at(11)), run_id=_RUN_ID)

    assert state is not None
    assert "preparing" not in state.parts


def test_the_running_part_has_a_start_only_and_is_named() -> None:
    document = {"running": "checking", "parts": {**_SAVED_DOCUMENT["parts"], "checking": {"started_at": _iso(30)}}}
    del document["parts"]["saving"]

    state = stage_plan_step(_link(document), run_id=_RUN_ID)

    assert state is not None
    assert state.running == "checking"
    assert state.parts["checking"] == PlanStepPartTimes(started_at=_at(30))
    assert "saving" not in state.parts


@pytest.mark.parametrize(
    ("terminal_state", "terminal_outcome"),
    [("failed", "failed"), ("cancelled", "cancelled"), ("interrupted", "ambiguous"), ("abandoned", "abandoned")],
)
def test_the_part_that_was_running_at_the_verdict_is_stopped_at_the_verdict_time(
    terminal_state: str, terminal_outcome: str
) -> None:
    document = {"running": "comparing", "parts": {"comparing": {"started_at": _iso(25)}}}
    verdict = _failed(terminal_state=terminal_state, terminal_outcome=terminal_outcome)

    state = stage_plan_step(_link(document, **verdict), run_id=_RUN_ID)

    assert state is not None
    assert state.running is None
    assert state.parts["comparing"] == PlanStepPartTimes(started_at=_at(25), stopped_at=_at(40))
    assert state.parts["comparing"].stopped_at == _link(document, **_failed()).terminal_at
    # The parts that ended keep their times; the parts that did not start have no entry.
    assert state.parts["preparing"].ended_at == _at(9)
    assert "checking" not in state.parts
    assert "saving" not in state.parts


@pytest.mark.parametrize("prefect_state", ["FAILED", "Crashed", "cancelled"])
def test_a_part_whose_flow_run_ended_before_any_verdict_is_stopped_with_no_time(prefect_state: str) -> None:
    document = {"running": "saving", "parts": {**_SAVED_DOCUMENT["parts"], "saving": {"started_at": _iso(31)}}}

    state = stage_plan_step(_link(document), run_id=_RUN_ID, prefect_state=prefect_state)

    assert state is not None
    assert state.running is None
    assert state.parts["saving"] == PlanStepPartTimes(started_at=_at(31))


def test_a_completed_execution_with_a_part_still_running_returns_no_entry_for_it(
    caplog: pytest.LogCaptureFixture,
) -> None:
    document = {"running": "saving", "parts": {**_SAVED_DOCUMENT["parts"], "saving": {"started_at": _iso(31)}}}

    with caplog.at_level(logging.WARNING, logger=_SERVICE_LOGGER):
        state = stage_plan_step(_link(document, **_completed()), run_id=_RUN_ID)

    assert state is not None
    assert state.running is None
    assert "saving" not in state.parts
    assert state.parts["checking"].ended_at == _at(31)
    assert any("saving" in record.getMessage() for record in caplog.records)


def test_a_plan_step_that_failed_while_reading_returns_no_duration_for_the_parts_that_did_not_run() -> None:
    reading = {"state": "reading", "started_at": _iso(10)}

    state = stage_plan_step(
        _link({"parts": {}}, source_read=_SOURCE_READ, destination_read=reading, **_failed()), run_id=_RUN_ID
    )

    assert state is not None
    assert set(state.parts) == {"waiting", "preparing"}
    for times in state.parts.values():
        assert times.ended_at is not None
        assert times.ended_at > times.started_at


def test_a_preparing_part_that_never_ended_is_stopped_at_the_verdict() -> None:
    state = stage_plan_step(_link({"parts": {}}, source_read=None, destination_read=None, **_failed()), run_id=_RUN_ID)

    assert state is not None
    assert state.parts["preparing"] == PlanStepPartTimes(started_at=_CLAIMED, stopped_at=_at(40))


@pytest.mark.parametrize(
    "entry",
    [
        pytest.param(_CANARY, id="not-a-mapping"),
        pytest.param({"started_at": _CANARY}, id="start-that-does-not-parse"),
        pytest.param({"started_at": _iso(30), "ended_at": _iso(29)}, id="end-before-start"),
        pytest.param({"started_at": "2026-11-16T10:00:30"}, id="start-without-a-time-zone"),
    ],
)
def test_a_stored_part_that_does_not_parse_is_not_returned_and_the_warning_holds_no_value(
    entry: object, caplog: pytest.LogCaptureFixture
) -> None:
    document = {"parts": {**_SAVED_DOCUMENT["parts"], "checking": entry}}

    with caplog.at_level(logging.WARNING, logger=_SERVICE_LOGGER):
        state = stage_plan_step(_link(document), run_id=_RUN_ID)

    assert state is not None
    assert "checking" not in state.parts
    assert state.parts["saving"].ended_at == _at(33)
    assert all(_CANARY not in record.getMessage() for record in caplog.records)


def test_a_returned_state_holds_part_names_and_times_only() -> None:
    """FR-021: no field of the returned timings holds an error message or a data value."""
    state = stage_plan_step(_link(_SAVED_DOCUMENT, **_completed()), run_id=_RUN_ID)

    assert state is not None
    assert set(state.parts) <= set(RESPONSE_PARTS)
    for times in state.parts.values():
        for value in dataclasses.asdict(times).values():
            assert value is None or isinstance(value, datetime)


# --- T039: the worker's observer ----------------------------------------------------------------


class _RecordingProjection:
    """Records each plan step document written, and answers each write with `stored`."""

    def __init__(self, *, stored: bool = True) -> None:
        self.writes: list[dict[str, Any]] = []
        self._stored = stored

    def record_plan_step(self, run_id: str, flow_run_id: str, document: Mapping[str, Any]) -> bool:
        assert (run_id, flow_run_id) == (_RUN_ID, _FLOW_RUN_ID)
        self.writes.append(dict(document))
        return self._stored


class _RaisingProjection:
    """A store whose write raises with text that must not be logged."""

    def record_plan_step(  # noqa: PLR6301 - the projection method this double stands in for
        self, run_id: str, flow_run_id: str, document: Mapping[str, Any]
    ) -> bool:
        msg = f"store unavailable for {run_id}/{flow_run_id} ({len(document)}): {_CANARY}"
        raise ConnectionError(msg)


def _clock() -> Iterator[datetime]:
    seconds = 25
    while True:
        yield _at(seconds)
        seconds += 1


def _observer(projection: object) -> StorePlanStepObserver:
    ticks = _clock()
    return StorePlanStepObserver(
        cast("ProductProjection", projection), _RUN_ID, _FLOW_RUN_ID, clock=lambda: next(ticks)
    )


def _engine_reports(observer: StorePlanStepObserver) -> None:
    """The calls Potenda makes for a plan step that saves its plan."""
    observer.part_started("comparing")
    observer.part_ended("comparing")
    observer.part_started("checking")
    observer.part_ended("checking")
    observer.part_started("saving")
    observer.part_ended("saving")


def test_the_observer_stores_each_part_and_the_end_with_the_next_start_in_one_write() -> None:
    projection = _RecordingProjection()
    observer = _observer(projection)

    observer.begin()
    _engine_reports(observer)
    observer.close_saving()

    assert projection.writes == [
        {"parts": {}},
        {"running": "comparing", "parts": {"comparing": {"started_at": _iso(25)}}},
        {
            "running": "checking",
            "parts": {
                "comparing": {"started_at": _iso(25), "ended_at": _iso(26)},
                "checking": {"started_at": _iso(27)},
            },
        },
        {
            "running": "saving",
            "parts": {
                "comparing": {"started_at": _iso(25), "ended_at": _iso(26)},
                "checking": {"started_at": _iso(27), "ended_at": _iso(28)},
                "saving": {"started_at": _iso(29)},
            },
        },
        {
            "parts": {
                "comparing": {"started_at": _iso(25), "ended_at": _iso(26)},
                "checking": {"started_at": _iso(27), "ended_at": _iso(28)},
                "saving": {"started_at": _iso(29), "ended_at": _iso(30)},
            },
        },
    ]


def test_the_engine_end_of_saving_is_not_stored_until_the_plan_is_published() -> None:
    projection = _RecordingProjection()
    observer = _observer(projection)
    _engine_reports(observer)

    assert projection.writes[-1]["running"] == "saving"
    assert "ended_at" not in projection.writes[-1]["parts"]["saving"]
    assert observer.document()["running"] == "saving"


def test_a_repeated_start_keeps_the_first_start_and_an_end_with_no_start_is_ignored() -> None:
    projection = _RecordingProjection()
    observer = _observer(projection)

    observer.part_ended("checking")
    observer.part_started("comparing")
    observer.part_started("comparing")
    observer.stopped()

    assert projection.writes == [{"running": "comparing", "parts": {"comparing": {"started_at": _iso(25)}}}]


def test_stopped_stores_an_end_not_yet_stored_and_keeps_the_running_part() -> None:
    projection = _RecordingProjection()
    observer = _observer(projection)
    observer.part_started("comparing")
    observer.part_ended("comparing")
    observer.part_started("checking")

    observer.stopped()

    # Nothing was waiting to be stored: the start of checking stored the end of comparing.
    assert len(projection.writes) == 2
    assert projection.writes[-1]["running"] == "checking"

    observer.part_ended("checking")
    observer.stopped()

    assert len(projection.writes) == 3
    assert projection.writes[-1]["parts"]["checking"] == {"started_at": _iso(27), "ended_at": _iso(28)}
    assert "running" not in projection.writes[-1]


def test_a_store_failure_is_logged_by_class_and_never_raises(caplog: pytest.LogCaptureFixture) -> None:
    observer = _observer(_RaisingProjection())

    with caplog.at_level(logging.WARNING, logger=_SERVICE_LOGGER):
        observer.begin()
        _engine_reports(observer)
        observer.close_saving()
        observer.stopped()

    messages = [record.getMessage() for record in caplog.records]
    assert messages
    assert all("ConnectionError" in message for message in messages)
    assert all(_CANARY not in message for message in messages)


def test_a_write_the_store_refuses_is_logged(caplog: pytest.LogCaptureFixture) -> None:
    projection = _RecordingProjection(stored=False)
    observer = _observer(projection)

    with caplog.at_level(logging.WARNING, logger=_SERVICE_LOGGER):
        observer.begin()

    assert projection.writes == [{"parts": {}}]
    warnings = [
        record.getMessage()
        for record in caplog.records
        if record.name == _SERVICE_LOGGER and record.levelno == logging.WARNING
    ]
    assert len(warnings) == 1
    assert "were not stored" in warnings[0]
    assert "is ended or unknown" in warnings[0]


# --- T039: the engine calls the observer around comparing, checking and saving -----------------


class _RecordingObserver:
    def __init__(self) -> None:
        self.events: list[tuple[str, str]] = []

    def part_started(self, part: PlanStepPartName) -> None:
        self.events.append(("started", part))

    def part_ended(self, part: PlanStepPartName) -> None:
        self.events.append(("ended", part))


class _RaisingObserver:
    def __init__(self) -> None:
        self.calls = 0

    def part_started(self, part: PlanStepPartName) -> None:
        self.calls += 1
        msg = f"observer broke while starting {part}: {_CANARY}"
        raise RuntimeError(msg)

    def part_ended(self, part: PlanStepPartName) -> None:
        self.calls += 1
        msg = f"observer broke while ending {part}: {_CANARY}"
        raise RuntimeError(msg)


@pytest.fixture
def _cache_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("INFRAHUB_SYNC_CACHE_DIR", str(tmp_path / "cache"))


def _plan_bytes(observer: object, *, run_id: str) -> tuple[bytes, bytes]:
    potenda = build_potenda(load_plan_problem_fixture(), run_id=run_id)
    potenda.plan_step_observer = cast("Any", observer)
    potenda.load_both_sides()
    planned = finish_plan(potenda)
    plan_dir = planned.run_dir / "plan"
    return (plan_dir / "operations.jsonl").read_bytes(), planned.report_bytes


@pytest.mark.usefixtures("_cache_dir")
def test_the_engine_reports_comparing_checking_and_saving_in_order() -> None:
    observer = _RecordingObserver()

    _plan_bytes(observer, run_id="20261116T100000-01900037")

    assert observer.events == [
        ("started", "comparing"),
        ("ended", "comparing"),
        ("started", "checking"),
        ("ended", "checking"),
        ("started", "saving"),
        ("ended", "saving"),
    ]


@pytest.mark.usefixtures("_cache_dir")
def test_an_observer_error_is_logged_by_class_and_does_not_change_the_plan(caplog: pytest.LogCaptureFixture) -> None:
    expected = _plan_bytes(None, run_id="20261116T100000-01900038")
    observer = _RaisingObserver()

    with caplog.at_level(logging.WARNING, logger=_ENGINE_LOGGER):
        planned = _plan_bytes(observer, run_id="20261116T100000-01900039")

    assert planned == expected
    assert observer.calls == 6
    warnings = [record.getMessage() for record in caplog.records if "plan step observer" in record.getMessage()]
    assert len(warnings) == 6
    assert all("RuntimeError" in message for message in warnings)
    assert all(_CANARY not in message for message in warnings)


class _ProblemPassError(RuntimeError):
    """Stands in for a problem pass that fails."""


@pytest.mark.usefixtures("_cache_dir")
def test_a_part_that_raises_is_not_reported_as_ended(monkeypatch: pytest.MonkeyPatch) -> None:
    def _fail(**_kwargs: object) -> None:
        raise _ProblemPassError

    monkeypatch.setattr("infrahub_sync.plan.problems.run_problem_pass", _fail)
    observer = _RecordingObserver()

    with pytest.raises(_ProblemPassError):
        _plan_bytes(observer, run_id="20261116T100000-01900040")

    assert observer.events == [("started", "comparing"), ("ended", "comparing"), ("started", "checking")]


# --- T039: the worker wires the observer, closes saving and stores the running part -------------


class _StopStageError(RuntimeError):
    """Ends the stage once the call under test has been seen."""


class _FakeInstance:
    name = "from-fake"
    _runtime_models = None


class _FakeSaved:
    manifest = None
    report = ()

    def summary(self) -> object:  # noqa: PLR6301 - the SavedPlan method this double stands in for
        raise _StopStageError


_BINDING = ("cfg-plan-step", 1, "b" * 64)


def _store(tmp_path: Path, purpose: str) -> ProductProjection:
    projection = ProductProjection(
        SQLiteRunStore(tmp_path / "records.sqlite3"), FileArtifactStore(tmp_path / "objects")
    )
    config_id, registry_version, package_checksum = _BINDING
    projection.create_run(
        _run(_RUN_ID).model_copy(
            update={
                "configuration_reference": f"{config_id}@{registry_version}",
                "config_id": config_id,
                "registry_version": registry_version,
                "package_checksum": package_checksum,
            }
        )
    )
    append_execution(projection, _RUN_ID, PrefectExecutionLink(flow_run_id=_FLOW_RUN_ID, purpose=purpose, attempt=1))
    return projection


def _stored_link(projection: ProductProjection) -> PrefectExecutionLink:
    run = projection.lookup_run(_RUN_ID).value
    assert run is not None
    return run.prefect_executions[0]


def _bind_stage(monkeypatch: pytest.MonkeyPatch, projection: ProductProjection) -> dict[str, Any]:
    """Run the stage on a fake engine that reports every part, and record the document at publication."""
    seen: dict[str, Any] = {}

    def _context(*_args: object, **_kwargs: object) -> tuple[ProductProjection, _FakeInstance, str]:
        return projection, _FakeInstance(), "from-fake"

    def _plan(_instance: object, **kwargs: object) -> _FakeSaved:
        seen["at_plan"] = _stored_link(projection).plan_step
        observer = kwargs["plan_step"]
        assert isinstance(observer, StorePlanStepObserver)
        _engine_reports(observer)
        return _FakeSaved()

    def _publish(*_args: object, **_kwargs: object) -> None:
        seen["at_publish"] = _stored_link(projection).plan_step

    monkeypatch.setattr(service_flow, "_worker_execution_context", _context)
    monkeypatch.setattr(service_flow, "collect_secret_values", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(service_flow, "_plan", _plan)
    monkeypatch.setattr(service_flow, "publish_plan_checkpoint", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(service_flow, "_publish_plan", _publish)
    bind_granting_guard(monkeypatch, service_flow)
    return seen


@pytest.mark.parametrize("stage", ["plan", "sync"])
def test_the_stage_closes_saving_only_after_the_plan_is_published(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stage: str
) -> None:
    projection = _store(tmp_path, stage)
    seen = _bind_stage(monkeypatch, projection)

    with stage_scratch(stage) as scratch, pytest.raises(_StopStageError):
        service_flow._execute_stage(
            _RUN_ID,
            cast("Any", stage),
            *_BINDING,
            None,
            None,
            confirm_writes=stage == "sync",
            secrets=[],
            projection=projection,
            tracker=WriteDispatchTracker(),
            scratch=scratch,
            flow_run_id=_FLOW_RUN_ID,
        )

    # The stage wrote an empty document before the engine ran.
    assert seen["at_plan"] == {"parts": {}}
    # While the plan is published, saving still runs.
    assert seen["at_publish"]["running"] == "saving"
    assert "ended_at" not in seen["at_publish"]["parts"]["saving"]
    stored = _stored_link(projection).plan_step
    assert stored is not None
    assert "running" not in stored
    assert set(stored["parts"]) == {"comparing", "checking", "saving"}
    assert all("ended_at" in part for part in stored["parts"].values())


def _run_failing_stage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stage: str, report: Callable[[StorePlanStepObserver], None]
) -> ProductProjection:
    projection = _store(tmp_path, stage)
    assert projection.claim_execution(_RUN_ID, _FLOW_RUN_ID, worker_id=_WORKER_ID)

    def _execute_stage(*_args: object, **kwargs: object) -> None:
        observer = kwargs["plan_step"]
        assert isinstance(observer, StorePlanStepObserver)
        observer.begin()
        report(observer)
        msg = f"the comparison failed: {_CANARY}"
        raise RuntimeError(msg)

    monkeypatch.setattr(service_flow, "_runtime", lambda: projection)
    monkeypatch.setattr(service_flow, "_run_logger", lambda: (logging.getLogger("test-plan-step"), False))
    monkeypatch.setattr(service_flow, "_claim_current_execution", lambda *_args: (_FLOW_RUN_ID, _WORKER_ID))
    monkeypatch.setattr(service_flow, "_execute_stage", _execute_stage)

    # The flow either returns a `Failed` state or raises a sanitized copy of the failure.
    with contextlib.suppress(RuntimeError):
        service_flow.service_sync_run.fn(_RUN_ID, cast("Any", stage), *_BINDING)
    return projection


def _fail_while_checking(observer: StorePlanStepObserver) -> None:
    observer.part_started("comparing")
    observer.part_ended("comparing")
    observer.part_started("checking")


def _fail_after_checking_ended(observer: StorePlanStepObserver) -> None:
    _fail_while_checking(observer)
    observer.part_ended("checking")


@pytest.mark.parametrize("stage", ["plan", "sync"])
def test_a_failed_stage_keeps_the_running_part_and_returns_it_stopped_at_the_verdict(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stage: str
) -> None:
    projection = _run_failing_stage(tmp_path, monkeypatch, stage, _fail_while_checking)

    link = _stored_link(projection)
    assert link.terminal_at is not None
    assert link.terminal_state == "failed"
    assert link.plan_step is not None
    assert link.plan_step["running"] == "checking"
    assert _CANARY not in str(link.plan_step)
    state = stage_plan_step(link, run_id=_RUN_ID)
    assert state is not None
    assert state.running is None
    assert state.parts["checking"].ended_at is None
    assert state.parts["checking"].stopped_at == link.terminal_at
    assert state.parts["comparing"].ended_at is not None


def test_the_worker_stores_an_end_not_yet_stored_before_the_failure_commit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    projection = _run_failing_stage(tmp_path, monkeypatch, "plan", _fail_after_checking_ended)

    link = _stored_link(projection)
    assert link.terminal_at is not None
    assert link.plan_step is not None
    assert "running" not in link.plan_step
    assert "ended_at" in link.plan_step["parts"]["checking"]


@pytest.mark.parametrize("stage", ["verify", "apply"])
def test_a_stage_with_no_plan_step_gets_no_observer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stage: str
) -> None:
    projection = _store(tmp_path, stage)
    seen: dict[str, Any] = {}

    def _execute_stage(*_args: object, **kwargs: object) -> None:
        seen.update(kwargs)
        raise _StopStageError

    monkeypatch.setattr(service_flow, "_runtime", lambda: projection)
    monkeypatch.setattr(service_flow, "_run_logger", lambda: (logging.getLogger("test-plan-step"), False))
    monkeypatch.setattr(service_flow, "_claim_current_execution", lambda *_args: (_FLOW_RUN_ID, _WORKER_ID))
    monkeypatch.setattr(service_flow, "_execute_stage", _execute_stage)
    monkeypatch.setattr(service_flow, "_record_failure", lambda *_args, **_kwargs: None)

    with contextlib.suppress(RuntimeError):
        service_flow.service_sync_run.fn(_RUN_ID, cast("Any", stage), *_BINDING)

    assert seen["plan_step"] is None
    assert _stored_link(projection).plan_step is None
