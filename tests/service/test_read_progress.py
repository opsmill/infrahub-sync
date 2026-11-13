"""The worker stores each side's read state, and the run response derives it (spec 018, US1).

Covers the worker's observer (research R10), the response derivation (research R6) and the
stage wiring (research R4, R9).
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, cast

import pytest

from infrahub_sync.adapters.infrahub import InfrahubLineageSettingsError
from infrahub_sync.client.models import SideRead, StageReads
from infrahub_sync.plan.ownership import WriteDispatchTracker
from infrahub_sync.potenda.read_progress import SideReadReport
from infrahub_sync.product_store import PrefectExecutionLink, ProductProjection
from infrahub_sync.product_store.store import FileArtifactStore, SQLiteRunStore
from infrahub_sync.service import flow as service_flow
from infrahub_sync.service.apply_progress import StoreApplyProgressObserver
from infrahub_sync.service.read_progress import StoreSideReadObserver, record_waiting, stage_reads
from infrahub_sync.service.scratch import stage_scratch
from tests.product_store.test_contract import _run
from tests.service.execution_fixtures import append_execution, bind_granting_guard

if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path

_LOGGER_NAME = "infrahub_sync.service.read_progress"
_RUN_ID = "run-001"
_FLOW_RUN_ID = "flow-plan-1"
_SECRET_DETAIL = "db.internal.example:5432 password=ZZ-READ-CANARY"  # noqa: S105 — a canary, not a credential
_STARTED = "2026-11-09T10:00:01+00:00"
_ENDED = "2026-11-09T10:00:13+00:00"
_TERMINAL_AT = datetime(2026, 11, 9, 10, 5, tzinfo=timezone.utc)


class _RecordingProjection:
    """Records each side written by each `record_side_reads` call, and the number of calls."""

    def __init__(self, *, stored: bool = True) -> None:
        self.writes: list[tuple[str, str, str, dict[str, Any]]] = []
        self.calls = 0
        self._stored = stored

    def record_side_reads(self, run_id: str, flow_run_id: str, documents: Mapping[str, Mapping[str, Any]]) -> bool:
        self.calls += 1
        self.writes.extend((run_id, flow_run_id, side, dict(document)) for side, document in documents.items())
        return self._stored


class _RaisingProjection:
    """A store whose write raises with text that must not be logged."""

    def record_side_reads(  # noqa: PLR6301 - the projection method this double stands in for
        self, run_id: str, flow_run_id: str, documents: Mapping[str, Mapping[str, Any]]
    ) -> bool:
        msg = f"store unavailable for {run_id}/{flow_run_id} ({len(documents)}): {_SECRET_DETAIL}"
        raise ConnectionError(msg)


def _observer(projection: object) -> StoreSideReadObserver:
    return StoreSideReadObserver(cast("ProductProjection", projection), _RUN_ID, _FLOW_RUN_ID)


def _utc(text: str) -> datetime:
    value = datetime.fromisoformat(text)
    assert value.tzinfo is not None
    return value


# --- T011: the worker's observer writes each side's document ----------------------------------


def test_read_started_writes_a_reading_document_with_its_start_time() -> None:
    projection = _RecordingProjection()

    _observer(projection).read_started("source")

    assert len(projection.writes) == 1
    run_id, flow_run_id, side, document = projection.writes[0]
    assert (run_id, flow_run_id, side) == (_RUN_ID, _FLOW_RUN_ID, "source")
    assert set(document) == {"state", "started_at"}
    assert document["state"] == "reading"
    _utc(document["started_at"])


def test_a_successful_read_ended_writes_succeeded_with_times_mode_and_counts_in_reported_order() -> None:
    """Potenda reports the counts sorted by kind name; the observer stores them in that order."""
    projection = _RecordingProjection()
    observer = _observer(projection)

    observer.read_started("destination")
    observer.read_ended(
        "destination",
        SideReadReport(succeeded=True, read_mode="incremental", nodes_by_kind={"DcimDevice": 3, "LocationSite": 2}),
    )

    started = projection.writes[0][3]
    side, document = projection.writes[1][2], projection.writes[1][3]
    assert side == "destination"
    assert set(document) == {"state", "started_at", "ended_at", "read_mode", "nodes_by_kind"}
    assert document["state"] == "succeeded"
    assert document["started_at"] == started["started_at"]
    assert _utc(document["ended_at"]) >= _utc(document["started_at"])
    assert document["read_mode"] == "incremental"
    assert list(document["nodes_by_kind"].items()) == [("DcimDevice", 3), ("LocationSite", 2)]


def test_a_failed_read_ended_writes_failed_with_the_error_class_only() -> None:
    projection = _RecordingProjection()
    observer = _observer(projection)

    observer.read_started("source")
    observer.read_ended("source", SideReadReport(succeeded=False, error_class="ConnectionError"))

    document = projection.writes[1][3]
    assert set(document) == {"state", "started_at", "ended_at", "error_class"}
    assert document["state"] == "failed"
    assert document["error_class"] == "ConnectionError"


def test_a_store_write_that_raises_is_logged_as_a_warning_and_not_raised(caplog: pytest.LogCaptureFixture) -> None:
    observer = _observer(_RaisingProjection())

    with caplog.at_level(logging.WARNING, logger=_LOGGER_NAME):
        observer.read_started("source")
        observer.read_ended("source", SideReadReport(succeeded=True, read_mode="full"))

    warnings = [record for record in caplog.records if record.levelno == logging.WARNING]
    assert len(warnings) == 2
    assert all("ConnectionError" in record.getMessage() for record in warnings)
    assert _SECRET_DETAIL not in caplog.text


def test_record_waiting_writes_both_sides_as_waiting_in_one_write() -> None:
    projection = _RecordingProjection()

    record_waiting(cast("ProductProjection", projection), _RUN_ID, _FLOW_RUN_ID)

    assert projection.calls == 1
    assert projection.writes == [
        (_RUN_ID, _FLOW_RUN_ID, "source", {"state": "waiting"}),
        (_RUN_ID, _FLOW_RUN_ID, "destination", {"state": "waiting"}),
    ]


def test_record_waiting_logs_a_store_failure_and_does_not_raise(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING, logger=_LOGGER_NAME):
        record_waiting(cast("ProductProjection", _RaisingProjection()), _RUN_ID, _FLOW_RUN_ID)

    assert len([record for record in caplog.records if record.levelno == logging.WARNING]) == 1
    assert _SECRET_DETAIL not in caplog.text


def test_a_write_that_changes_no_row_is_logged_as_a_warning(caplog: pytest.LogCaptureFixture) -> None:
    """A write to an execution that ended, or to a flow run ID that matches no row, leaves a trace."""
    with caplog.at_level(logging.WARNING, logger=_LOGGER_NAME):
        _observer(_RecordingProjection(stored=False)).read_started("source")

    warnings = [record for record in caplog.records if record.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert _RUN_ID in warnings[0].getMessage()
    assert _FLOW_RUN_ID in warnings[0].getMessage()


# --- T012: what the run response derives from the stored documents ----------------------------

_SUCCEEDED = {
    "state": "succeeded",
    "started_at": _STARTED,
    "ended_at": _ENDED,
    "read_mode": "full",
    "nodes_by_kind": {"DcimDevice": 3, "LocationSite": 2},
}
_READING = {"state": "reading", "started_at": _STARTED}
_WAITING = {"state": "waiting"}


def _link(
    source: dict[str, Any] | None,
    destination: dict[str, Any] | None,
    *,
    terminal: tuple[str, str] | None = None,
) -> PrefectExecutionLink:
    fields: dict[str, Any] = {"source_read": source, "destination_read": destination}
    if terminal is not None:
        fields |= {"terminal_at": _TERMINAL_AT, "terminal_state": terminal[0], "terminal_outcome": terminal[1]}
    return PrefectExecutionLink(flow_run_id=_FLOW_RUN_ID, purpose="plan", attempt=1, **fields)


def test_an_execution_without_documents_has_no_reads() -> None:
    assert stage_reads(_link(None, None), run_id=_RUN_ID) is None


def test_an_execution_still_running_returns_the_stored_states() -> None:
    reads = stage_reads(_link(_SUCCEEDED, _READING), run_id=_RUN_ID)

    assert reads == StageReads(
        source=SideRead(
            state="succeeded",
            started_at=_utc(_STARTED),
            ended_at=_utc(_ENDED),
            read_mode="full",
            nodes_by_kind={"DcimDevice": 3, "LocationSite": 2},
        ),
        destination=SideRead(state="reading", started_at=_utc(_STARTED)),
    )
    assert reads is not None
    assert reads.destination is not None
    assert reads.destination.nodes_by_kind == {}
    assert reads.destination.error_class is None


@pytest.mark.parametrize(
    "terminal",
    [("failed", "failed"), ("interrupted", "ambiguous"), ("cancelled", "cancelled")],
    ids=["failed", "interrupted", "cancelled"],
)
def test_an_execution_that_did_not_complete_returns_waiting_as_not_started_and_reading_as_interrupted(
    terminal: tuple[str, str],
) -> None:
    reads = stage_reads(_link(_WAITING, _READING, terminal=terminal), run_id=_RUN_ID)

    assert reads == StageReads(
        source=SideRead(state="not_started"),
        destination=SideRead(state="interrupted", started_at=_utc(_STARTED), ended_at=None),
    )


def test_an_execution_that_did_not_complete_keeps_a_side_that_ended() -> None:
    failed = {"state": "failed", "started_at": _STARTED, "ended_at": _ENDED, "error_class": "ConnectionError"}

    reads = stage_reads(_link(_SUCCEEDED, failed, terminal=("failed", "failed")), run_id=_RUN_ID)

    assert reads is not None
    assert reads.source is not None
    assert reads.source.state == "succeeded"
    assert reads.destination == SideRead(
        state="failed", started_at=_utc(_STARTED), ended_at=_utc(_ENDED), error_class="ConnectionError"
    )


@pytest.mark.parametrize("prefect_state", ["crashed", "failed", "cancelled", "Crashed"])
def test_an_execution_prefect_reports_ended_returns_unfinished_sides_as_ended_before_a_verdict(
    prefect_state: str,
) -> None:
    """SC-003: a killed worker's execution has no verdict yet, but no side reads as still running."""
    reads = stage_reads(_link(_WAITING, _READING), run_id=_RUN_ID, prefect_state=prefect_state)

    assert reads == StageReads(
        source=SideRead(state="not_started"),
        destination=SideRead(state="interrupted", started_at=_utc(_STARTED), ended_at=None),
    )


@pytest.mark.parametrize("prefect_state", [None, "running", "completed"])
def test_an_execution_prefect_does_not_report_failed_keeps_the_stored_states(prefect_state: str | None) -> None:
    reads = stage_reads(_link(_WAITING, _READING), run_id=_RUN_ID, prefect_state=prefect_state)

    assert reads == StageReads(
        source=SideRead(state="waiting"), destination=SideRead(state="reading", started_at=_utc(_STARTED))
    )


@pytest.mark.parametrize("unfinished", [_WAITING, _READING], ids=["waiting", "reading"])
def test_a_completed_execution_returns_an_unfinished_side_as_none_and_logs_one_warning(
    unfinished: dict[str, Any], caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.WARNING, logger=_LOGGER_NAME):
        reads = stage_reads(_link(_SUCCEEDED, unfinished, terminal=("completed", "succeeded")), run_id=_RUN_ID)

    assert reads is not None
    assert reads.source is not None
    assert reads.source.state == "succeeded"
    assert reads.destination is None
    warnings = [record for record in caplog.records if record.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert _RUN_ID in warnings[0].getMessage()
    assert "destination" in warnings[0].getMessage()


@pytest.mark.parametrize(
    "document",
    [
        {"state": "finished"},
        {"state": "succeeded", "nodes_by_kind": "many"},
        {"state": "reading", "started_at": "yesterday"},
        {"started_at": _STARTED},
        {"state": "interrupted"},
        {"state": "succeeded", "read_mode": "partial"},
    ],
    ids=["unknown-state", "counts-not-an-object", "time-not-a-time", "no-state", "derived-state", "unknown-mode"],
)
def test_a_document_that_does_not_parse_returns_none_for_that_side_and_logs_one_warning(
    document: dict[str, Any], caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.WARNING, logger=_LOGGER_NAME):
        reads = stage_reads(_link(document, _SUCCEEDED), run_id=_RUN_ID)

    assert reads is not None
    assert reads.source is None
    assert reads.destination is not None
    assert reads.destination.state == "succeeded"
    warnings = [record for record in caplog.records if record.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert _RUN_ID in warnings[0].getMessage()
    assert "source" in warnings[0].getMessage()


def test_a_stored_field_outside_the_document_is_not_returned() -> None:
    reads = stage_reads(_link({**_READING, "message": _SECRET_DETAIL}, _WAITING), run_id=_RUN_ID)

    assert reads is not None
    assert _SECRET_DETAIL not in reads.model_dump_json()


def test_each_execution_of_a_run_keeps_and_returns_its_own_read_state(tmp_path: Path) -> None:
    """FR-011: reads recorded on a second execution leave the first execution's documents unchanged."""
    failed = {"state": "failed", "started_at": _STARTED, "ended_at": _ENDED, "error_class": "ConnectionError"}
    second_flow_run_id = "flow-plan-2"
    projection = _store(tmp_path, "plan")
    assert projection.record_side_reads(_RUN_ID, _FLOW_RUN_ID, {"source": _SUCCEEDED, "destination": failed}) is True
    assert projection.abandon_execution(_RUN_ID, _FLOW_RUN_ID, terminal_at=_TERMINAL_AT) is True
    append_execution(
        projection, _RUN_ID, PrefectExecutionLink(flow_run_id=second_flow_run_id, purpose="plan", attempt=2)
    )

    record_waiting(projection, _RUN_ID, second_flow_run_id)
    assert projection.record_side_reads(_RUN_ID, second_flow_run_id, {"source": _READING}) is True

    run = projection.lookup_run(_RUN_ID).value
    assert run is not None
    first, second = run.prefect_executions
    assert (first.attempt, second.attempt) == (1, 2)
    assert first.source_read == _SUCCEEDED
    assert first.destination_read == failed
    assert second.source_read == _READING
    assert second.destination_read == _WAITING
    first_reads = stage_reads(first, run_id=_RUN_ID)
    assert first_reads is not None
    assert first_reads.source is not None
    assert first_reads.source.state == "succeeded"
    assert first_reads.destination is not None
    assert first_reads.destination.state == "failed"
    assert stage_reads(second, run_id=_RUN_ID) == StageReads(
        source=SideRead(state="reading", started_at=_utc(_STARTED)), destination=SideRead(state="waiting")
    )


# --- T013: the stage records `waiting` and passes the observer ----------------------------------


class _StopStageError(RuntimeError):
    """Ends the stage once the call under test has been seen."""


class _FakeInstance:
    name = "from-fake"
    _runtime_models = None


def _store(tmp_path: Path, purpose: str) -> ProductProjection:
    projection = ProductProjection(
        SQLiteRunStore(tmp_path / "records.sqlite3"), FileArtifactStore(tmp_path / "objects")
    )
    projection.create_run(_run(_RUN_ID))
    append_execution(projection, _RUN_ID, PrefectExecutionLink(flow_run_id=_FLOW_RUN_ID, purpose=purpose, attempt=1))
    return projection


def _stored_link(projection: ProductProjection) -> PrefectExecutionLink:
    run = projection.lookup_run(_RUN_ID).value
    assert run is not None
    return run.prefect_executions[0]


def _bind_stage_context(monkeypatch: pytest.MonkeyPatch, projection: ProductProjection) -> None:
    def _context(*_args: object, **_kwargs: object) -> tuple[ProductProjection, _FakeInstance, str]:
        return projection, _FakeInstance(), "from-fake"

    monkeypatch.setattr(service_flow, "_worker_execution_context", _context)
    monkeypatch.setattr(service_flow, "collect_secret_values", lambda *_args, **_kwargs: [])


def _execute(projection: ProductProjection, stage: str, *, binding: tuple[Any, Any, Any] = (None, None, None)) -> None:
    with stage_scratch(stage) as scratch:
        service_flow._execute_stage(
            _RUN_ID,
            cast("Any", stage),
            *binding,
            None,
            "a" * 64 if stage == "apply" else None,
            confirm_writes=stage in {"apply", "sync"},
            secrets=[],
            config_directory=None,
            projection=projection,
            tracker=WriteDispatchTracker(),
            scratch=scratch,
            flow_run_id=_FLOW_RUN_ID,
        )


def test_a_plan_stage_records_both_sides_waiting_then_passes_the_observer_to_execute_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    projection = _store(tmp_path, "plan")
    _bind_stage_context(monkeypatch, projection)
    seen: dict[str, Any] = {}

    def _execute_run(_instance: object, **kwargs: object) -> None:
        link = _stored_link(projection)
        seen["source"], seen["destination"] = link.source_read, link.destination_read
        seen["observer"] = kwargs.get("_read_observer")
        seen["operation"] = kwargs.get("operation")
        raise _StopStageError

    monkeypatch.setattr(service_flow, "execute_run", _execute_run)

    with pytest.raises(_StopStageError):
        _execute(projection, "plan")

    assert seen["operation"] == "plan"
    assert seen["source"] == {"state": "waiting"}
    assert seen["destination"] == {"state": "waiting"}
    observer = seen["observer"]
    assert isinstance(observer, StoreSideReadObserver)
    # The observer writes to this execution, so its first write lands on the stored row.
    observer.read_started("source")
    assert _stored_link(projection).source_read is not None
    assert _stored_link(projection).source_read["state"] == "reading"  # ty: ignore[not-subscriptable]


def test_a_verify_stage_records_nothing_and_passes_no_observer(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    projection = _store(tmp_path, "verify")
    _bind_stage_context(monkeypatch, projection)
    monkeypatch.setattr(service_flow, "rehydrate_plan_checkpoint", lambda *_args, **_kwargs: None)
    seen: dict[str, Any] = {}

    def _execute_run(_instance: object, **kwargs: object) -> None:
        seen.update(kwargs)
        raise _StopStageError

    monkeypatch.setattr(service_flow, "execute_run", _execute_run)

    with pytest.raises(_StopStageError):
        _execute(projection, "verify")

    assert seen["operation"] == "verify"
    assert "_read_observer" not in seen
    link = _stored_link(projection)
    assert (link.source_read, link.destination_read) == (None, None)


def test_an_apply_stage_records_no_read_and_passes_only_the_apply_progress_observer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    projection = _store(tmp_path, "apply")
    _bind_stage_context(monkeypatch, projection)
    bind_granting_guard(monkeypatch, service_flow)
    monkeypatch.setattr(service_flow, "rehydrate_plan_checkpoint", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(service_flow, "_verify_registered_apply", lambda **_kwargs: SimpleNamespace(operations_count=7))
    monkeypatch.setattr(service_flow, "_require_planned_schema", lambda **_kwargs: None)
    seen: dict[str, Any] = {}

    def _execute_run(_instance: object, **kwargs: object) -> None:
        seen.update(kwargs)
        raise _StopStageError

    monkeypatch.setattr(service_flow, "execute_run", _execute_run)

    with pytest.raises(_StopStageError):
        _execute(projection, "apply", binding=("cfg-read-progress", 1, "b" * 64))

    assert seen["operation"] == "apply"
    assert "_read_observer" not in seen
    # The apply saves its progress marker instead (spec 022, T008).
    assert isinstance(seen["_apply_progress"], StoreApplyProgressObserver)
    link = _stored_link(projection)
    assert (link.source_read, link.destination_read) == (None, None)


def test_a_lineage_refusal_before_the_reads_leaves_both_sides_waiting_then_not_started(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    projection = _store(tmp_path, "plan")
    _bind_stage_context(monkeypatch, projection)

    def _refuse_lineage(_instance: object, **_kwargs: object) -> None:
        # `load_both_sides` resolves the destination's lineage before either side is read.
        msg = "the destination holds no lineage for this configuration"
        raise InfrahubLineageSettingsError(msg)

    monkeypatch.setattr(service_flow, "execute_run", _refuse_lineage)

    with pytest.raises(InfrahubLineageSettingsError):
        _execute(projection, "plan")

    link = _stored_link(projection)
    assert (link.source_read, link.destination_read) == ({"state": "waiting"}, {"state": "waiting"})
    failed = link.model_copy(
        update={"terminal_at": _TERMINAL_AT, "terminal_state": "failed", "terminal_outcome": "failed"}
    )
    assert stage_reads(failed, run_id=_RUN_ID) == StageReads(
        source=SideRead(state="not_started"), destination=SideRead(state="not_started")
    )


class _SchemaReadError(RuntimeError):
    """Stands in for a destination that cannot be reached during the live schema read."""


def _fail_schema_read(*_args: object, **_kwargs: object) -> None:
    raise _SchemaReadError


@pytest.mark.parametrize(
    ("stage", "binding"),
    [("plan", (None, None, None)), ("sync", ("cfg-read-progress", 1, "b" * 64))],
    ids=["plan", "sync"],
)
def test_a_stage_that_fails_in_its_schema_read_returns_both_sides_not_started(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stage: str, binding: tuple[Any, Any, Any]
) -> None:
    """Both sides are `waiting` from the start of the stage, before the live schema read."""
    projection = _store(tmp_path, stage)
    bind_granting_guard(monkeypatch, service_flow)
    monkeypatch.setattr(service_flow, "_worker_execution_context", _fail_schema_read)

    with pytest.raises(_SchemaReadError):
        _execute(projection, stage, binding=binding)

    link = _stored_link(projection)
    assert (link.source_read, link.destination_read) == ({"state": "waiting"}, {"state": "waiting"})
    failed = link.model_copy(
        update={"terminal_at": _TERMINAL_AT, "terminal_state": "failed", "terminal_outcome": "failed"}
    )
    assert stage_reads(failed, run_id=_RUN_ID) == StageReads(
        source=SideRead(state="not_started"), destination=SideRead(state="not_started")
    )


def test_a_sync_stage_records_waiting_before_it_waits_for_the_write_guard(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    projection = _store(tmp_path, "sync")
    _bind_stage_context(monkeypatch, projection)
    seen: dict[str, Any] = {}

    def _guard(_configuration_id: str) -> None:
        link = _stored_link(projection)
        seen["reads"] = (link.source_read, link.destination_read)
        raise _StopStageError

    monkeypatch.setattr(service_flow, "_configuration_write_guard", _guard)

    with pytest.raises(_StopStageError):
        _execute(projection, "sync", binding=_SYNC_BINDING)

    assert seen["reads"] == ({"state": "waiting"}, {"state": "waiting"})


# --- T022: the `sync` stage reads both sides the way a Plan does (research R9) ------------------

_FAKE_SOURCE_REPORT = SideReadReport(succeeded=True, read_mode="full", nodes_by_kind={"DcimDevice": 3})
_FAKE_DESTINATION_REPORT = SideReadReport(succeeded=False, error_class="ConnectionError")
_FIXED_NOW = "2026-11-09T10:00:05+00:00"
_SYNC_BINDING = ("cfg-read-progress", 1, "b" * 64)


def _bind_sync_stage(monkeypatch: pytest.MonkeyPatch, projection: ProductProjection) -> None:
    _bind_stage_context(monkeypatch, projection)
    bind_granting_guard(monkeypatch, service_flow)


def _fake_reads(_instance: object, **kwargs: object) -> None:
    """Report the same two reads to whatever observer the stage passes, then end the stage."""
    observer = cast("StoreSideReadObserver", kwargs["_read_observer"])
    observer.read_started("source")
    observer.read_started("destination")
    observer.read_ended("source", _FAKE_SOURCE_REPORT)
    observer.read_ended("destination", _FAKE_DESTINATION_REPORT)
    raise _StopStageError


def test_a_sync_stage_records_both_sides_waiting_then_passes_the_observer_to_its_composed_plan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    projection = _store(tmp_path, "sync")
    _bind_sync_stage(monkeypatch, projection)
    seen: dict[str, Any] = {}

    def _plan(_instance: object, **kwargs: object) -> None:
        link = _stored_link(projection)
        seen["source"], seen["destination"] = link.source_read, link.destination_read
        seen.update(kwargs)
        raise _StopStageError

    monkeypatch.setattr(service_flow, "_plan", _plan)

    with pytest.raises(_StopStageError):
        _execute(projection, "sync", binding=_SYNC_BINDING)

    assert seen["composed_sync"] is True
    assert seen["source"] == {"state": "waiting"}
    assert seen["destination"] == {"state": "waiting"}
    observer = seen["observer"]
    assert isinstance(observer, StoreSideReadObserver)
    observer.read_started("destination")
    assert _stored_link(projection).destination_read is not None
    assert _stored_link(projection).destination_read["state"] == "reading"  # ty: ignore[not-subscriptable]


def test_a_sync_stage_returns_the_same_reads_as_a_plan_stage_for_the_same_reads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("infrahub_sync.service.read_progress._now", lambda: _FIXED_NOW)
    monkeypatch.setattr(service_flow, "execute_run", _fake_reads)
    reads: dict[str, StageReads | None] = {}
    for stage in ("plan", "sync"):
        directory = tmp_path / stage
        directory.mkdir()
        projection = _store(directory, stage)
        _bind_sync_stage(monkeypatch, projection)
        with pytest.raises(_StopStageError):
            _execute(projection, stage, binding=_SYNC_BINDING if stage == "sync" else (None, None, None))
        reads[stage] = stage_reads(_stored_link(projection), run_id=_RUN_ID)

    fixed = _utc(_FIXED_NOW)
    assert reads["plan"] == StageReads(
        source=SideRead(
            state="succeeded", started_at=fixed, ended_at=fixed, read_mode="full", nodes_by_kind={"DcimDevice": 3}
        ),
        destination=SideRead(state="failed", started_at=fixed, ended_at=fixed, error_class="ConnectionError"),
    )
    assert reads["sync"] == reads["plan"]
