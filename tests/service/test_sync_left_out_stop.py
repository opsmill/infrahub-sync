"""A `sync` run whose plan leaves out records stops after the plan (spec 020 T036 and T078).

FR-012, data-model.md "Stage result `failure` for a stopped `sync`", research R11 and decision 2
of 2026-11-11. The stage saves its plan, writes nothing, and ends with phase `planned`, outcome
`left-out-review-required` and a `failure` in its stage result whose next action names the run
ID, the plan checksum and `--confirm-left-out`. The last log line shows that next action. The
stop gives back the run's write admission, so the team applies the run with the confirmation.

T078: the stopped run's confirmed apply takes the configuration write guard like any other
write. When another run holds that guard, the apply is refused before it reads the destination
schema or dispatches anything; it never writes alongside the other run. The store side of the
same case is in `tests/product_store/test_stopped_sync_admission.py`.

The stage runs through the production worker boundary (`service_sync_run`). Only the source
read, the engine's write, the live schema read and the guard's PostgreSQL session are doubles,
the same doubles as `tests/service/test_managed_write_guard.py`.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

import pytest

pytest.importorskip("prefect")
pytest.importorskip("opsmill_prefect_extras")
pytest.importorskip("psycopg")

from infrahub_sync.configuration import ConfigurationPackage
from infrahub_sync.configuration.runtime import resolve_runtime_instance
from infrahub_sync.execution import RunResult
from infrahub_sync.plan.config_version import resolve_config_version
from infrahub_sync.plan.models import LeftOutRecord, PlanProblem, PlanReport, SourceRecordReference
from infrahub_sync.plan.review import read_saved_plan
from infrahub_sync.plan.writer import write_plan_artifact
from infrahub_sync.product_store import (
    MutationReceipt,
    PrefectExecutionLink,
    ProductProjection,
    ProductRun,
    local_product_projection,
)
from infrahub_sync.runtime_schema import RuntimeModelPlan, RuntimeSideModels
from infrahub_sync.service import flow as service_flow
from infrahub_sync.service.flow import service_sync_run
from tests.configuration.validation_packages import package
from tests.service.execution_fixtures import stage_root, write_applied_sidecar
from tests.service.test_managed_write_guard import (
    SCHEMA_FINGERPRINT,
    WORKER_ID,
    _FakeGuardSession,
    _lock_timeout,
    _receipt,
)

if TYPE_CHECKING:
    from pathlib import Path

_STOP_OUTCOME = "left-out-review-required"
_SYNC_FLOW_RUN_ID = "4b2f6e1a-8c3d-4e5f-9a0b-1c2d3e4f5a6b"
_APPLY_FLOW_RUN_ID = "9d8c7b6a-5f4e-4d3c-8b2a-1f0e9d8c7b6a"
_RUN_ID = "stopped-sync"

_SITE = SourceRecordReference(kind="LocationSite", identity={"name": "paris"}, source_id="1")
_DEVICE = SourceRecordReference(kind="DcimDevice", identity={"name": "core-1"}, source_id="7")


def _report() -> PlanReport:
    """Two records left out: one with a problem and one that depends on it."""
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


class _Harness:
    """One `sync` run of a registered configuration, with every collaborator recorded."""

    def __init__(self, projection: ProductProjection, binding: tuple[str, int, str], tmp_path: Path) -> None:
        self.projection = projection
        self.binding = binding
        self.tmp_path = tmp_path
        self.events: list[str] = []
        self.flow_run_id = _SYNC_FLOW_RUN_ID
        self.acquire_failure: BaseException | None = None
        self.checksum: str | None = None

    def stored(self) -> ProductRun:
        run = self.projection.lookup_run(_RUN_ID).value
        assert run is not None
        return run

    def run_sync(self) -> dict[str, Any]:
        result = service_sync_run.fn(_RUN_ID, "sync", *self.binding, confirm_writes=True)
        assert isinstance(result, dict)
        return result

    def admit_apply(self) -> MutationReceipt:
        """Reserve the confirmed apply through the store's normal write admission."""
        reserved, _created = self.projection.reserve_mutation(
            _receipt("m-confirmed-apply", run_id=_RUN_ID, operation="apply"), admit_write=True
        )
        return reserved

    def submit_apply(self, receipt: MutationReceipt) -> None:
        self.projection.add_prefect_execution(
            _RUN_ID,
            PrefectExecutionLink(
                flow_run_id=_APPLY_FLOW_RUN_ID, purpose="apply", attempt=1, submitted_at=datetime.now(timezone.utc)
            ),
            receipt_id=receipt.receipt_id,
        )
        self.projection.complete_mutation(
            receipt.receipt_id, response_status=202, response_body={"accepted": True}, flow_run_id=_APPLY_FLOW_RUN_ID
        )
        self.flow_run_id = _APPLY_FLOW_RUN_ID


def _harness(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, report: PlanReport | None) -> _Harness:
    monkeypatch.setenv("INFRAHUB_SYNC_CREDENTIAL_NETBOX_TOKEN", "stop-netbox-canary")
    monkeypatch.setenv("INFRAHUB_SYNC_CREDENTIAL_INFRAHUB_API_TOKEN", "stop-infrahub-canary")
    monkeypatch.setenv("INFRAHUB_SYNC_CACHE_DIR", str(tmp_path / "runs"))
    projection = local_product_projection(tmp_path / "product")
    registered = projection.create_configuration(package())
    binding = (registered.config_id, registered.registry_version, registered.package_checksum)
    # Created with its write admission in one reservation, as `RunService.create_run` does.
    created, _new = projection.reserve_mutation(
        _receipt("m-sync", run_id=_RUN_ID, operation="sync"),
        run=ProductRun(
            run_id=_RUN_ID,
            operation="sync",
            configuration_reference=f"{binding[0]}@{binding[1]}",
            config_id=binding[0],
            registry_version=binding[1],
            package_checksum=binding[2],
            actor="owner",
            started_at=datetime.now(timezone.utc),
            phase="accepted",
            summary={"sync_name": "stop"},
        ),
        admit_write=True,
    )
    projection.add_prefect_execution(
        _RUN_ID,
        PrefectExecutionLink(
            flow_run_id=_SYNC_FLOW_RUN_ID, purpose="sync", attempt=1, submitted_at=datetime.now(timezone.utc)
        ),
        receipt_id=created.receipt_id,
    )
    projection.complete_mutation(
        created.receipt_id, response_status=202, response_body={"accepted": True}, flow_run_id=_SYNC_FLOW_RUN_ID
    )
    stored = projection.lookup_configuration_version(binding[0], binding[1]).value
    assert stored is not None
    runtime = resolve_runtime_instance(
        ConfigurationPackage.model_validate(stored.declared_content), directory=str(tmp_path)
    )
    runtime._configuration_binding = binding
    harness = _Harness(projection, binding, tmp_path)
    events = harness.events

    def guard_session() -> _FakeGuardSession:
        return _FakeGuardSession(events, acquire_failure=harness.acquire_failure)

    def build_models(**_kwargs: object) -> RuntimeModelPlan:
        events.append("live-schema-read")
        return RuntimeModelPlan(
            branch="main",
            schema_fingerprint=SCHEMA_FINGERPRINT,
            destination=RuntimeSideModels(adapter_class=object, models={}),
            source=None,
        )

    def saved_plan(base: Path) -> Any:  # noqa: ANN401 - the engine's own SavedPlan record.
        return read_saved_plan(sync_name=runtime.name, run_id=_RUN_ID, config=runtime, base_directory=base)

    def fake_plan(*_args: object, **kwargs: object) -> Any:  # noqa: ANN401 - the engine's own SavedPlan record.
        events.append("plan")
        base = stage_root(kwargs)
        manifest = write_plan_artifact(
            run_dir=base / runtime.name / _RUN_ID,
            run_id=_RUN_ID,
            config_version=resolve_config_version(runtime),
            source_snapshot=[],
            deletes_computed=True,
            operations=[],
            configuration_binding=binding,
            schema_fingerprint=SCHEMA_FINGERPRINT,
            report=report,
        )
        harness.checksum = manifest.plan_checksum
        return saved_plan(base)

    def fake_execute_run(_instance: object, **kwargs: object) -> Any:  # noqa: ANN401 - result shape follows the stage.
        operation = kwargs.get("operation")
        events.append(f"execute-run:{operation}")
        base = stage_root(kwargs)
        if operation == "verify":
            return saved_plan(base)
        write_applied_sidecar(base / runtime.name / _RUN_ID)
        return RunResult(
            sync_name=runtime.name,
            operation="apply",
            run_id=_RUN_ID,
            status="no-change",
            changed=False,
            summary={"create": 0, "update": 0, "delete": 0},
            artifact_path=str(tmp_path / "runs" / runtime.name / _RUN_ID),
        )

    monkeypatch.setattr(service_flow, "_runtime", lambda: (str(tmp_path), projection))
    monkeypatch.setattr(service_flow, "_run_logger", lambda: (service_flow.logger, False))
    monkeypatch.setenv("PREFECT__WORKER_ID", WORKER_ID)
    monkeypatch.setattr(service_flow, "_prefect_flow_run_id", lambda: harness.flow_run_id)
    monkeypatch.setattr(service_flow, "_require_current_worker_identity", lambda *_args: None)
    monkeypatch.setattr(service_flow, "build_runtime_model_plan", build_models)
    monkeypatch.setattr(service_flow, "execute_run", fake_execute_run)
    monkeypatch.setattr(service_flow, "_plan", fake_plan)
    monkeypatch.setattr(service_flow, "_publish_plan", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(service_flow, "service_guard_session", guard_session)
    monkeypatch.setattr(service_flow, "service_guard_secrets", lambda: ())
    return harness


@pytest.fixture
def stopping(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> _Harness:
    """A `sync` whose plan leaves out two records."""
    return _harness(tmp_path, monkeypatch, report=_report())


# --- T036: the stop ----------------------------------------------------------------------------


def test_a_sync_whose_plan_leaves_out_records_stops_after_the_plan(stopping: _Harness) -> None:
    result = stopping.run_sync()

    assert result["outcome"] == _STOP_OUTCOME
    assert "execute-run:apply" not in stopping.events, "The stop comes before any destination write."
    run = stopping.stored()
    assert (run.phase, run.outcome) == ("planned", _STOP_OUTCOME)
    assert run.reconciliation_required is False
    link = run.prefect_executions[0]
    assert (link.terminal_state, link.terminal_outcome) == ("completed", "succeeded")
    assert run.finished_at is not None


def test_the_stage_result_carries_the_failure_and_its_next_action(stopping: _Harness) -> None:
    stopping.run_sync()

    failure = stopping.stored().results["failure"]
    assert failure["failure_type"] == "left_out_review_required"
    assert failure["stage"] == "sync"
    assert failure["left_out"] == 2
    next_action = failure["next_action"]
    assert _RUN_ID in next_action
    assert stopping.checksum is not None
    assert stopping.checksum in next_action
    assert "--confirm-left-out" in next_action
    assert f"infrahub-sync runs plan {_RUN_ID}" in next_action


class _Lines(logging.Handler):
    """Every line the stage logs, read from the stage's own logger."""

    def __init__(self) -> None:
        super().__init__(level=logging.INFO)
        self.lines: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.lines.append(record.getMessage())


def test_the_last_log_line_shows_the_next_action(stopping: _Harness, monkeypatch: pytest.MonkeyPatch) -> None:
    lines = _Lines()
    run_logger = logging.getLogger("test-sync-left-out-stop")
    run_logger.setLevel(logging.INFO)
    run_logger.addHandler(lines)
    monkeypatch.setattr(service_flow, "_run_logger", lambda: (run_logger, False))
    try:
        stopping.run_sync()
    finally:
        run_logger.removeHandler(lines)

    # The flow writes each line as "<module> | <text>".
    final = [line.partition(" | ")[2] for line in lines.lines if " | service run " in line]
    assert final, "The stage logged no final line."
    assert f"stage=sync outcome={_STOP_OUTCOME}" in final[-1]
    assert "--confirm-left-out" in final[-1]


def test_the_stopped_sync_admits_its_confirmed_apply(stopping: _Harness) -> None:
    """The stop's writeback gives back the write admission (research R11, T039)."""
    stopping.run_sync()

    admitted = stopping.admit_apply()

    assert admitted.state == "reserved", "The stopped sync still holds its write admission."


def test_a_sync_that_leaves_out_nothing_applies_as_today(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    harness = _harness(tmp_path, monkeypatch, report=None)

    harness.run_sync()

    assert "execute-run:apply" in harness.events
    run = harness.stored()
    assert run.phase == "applied"
    assert "failure" not in run.results
    refused = harness.admit_apply()
    assert refused.state == "accepted", "A sync that went on to write keeps its admission."


# --- T078: another writer holds the configuration between the stop and the confirmed apply ------


def _apply_stage(harness: _Harness) -> dict[str, Any]:
    assert harness.checksum is not None
    result = service_sync_run.fn(
        _RUN_ID,
        "apply",
        *harness.binding,
        expected_checksum=harness.checksum,
        confirm_writes=True,
        confirm_left_out=True,
    )
    assert isinstance(result, dict)
    return result


def test_a_confirmed_apply_is_refused_while_another_run_holds_the_configuration(stopping: _Harness) -> None:
    """Decision 2 of 2026-11-11: the apply waits for or is refused by the other writer, never writes beside it."""
    stopping.run_sync()
    admitted = stopping.admit_apply()
    assert admitted.state == "reserved", "The stopped sync still holds its write admission."
    stopping.submit_apply(admitted)
    stopping.events.clear()
    stopping.acquire_failure = _lock_timeout()

    with pytest.raises(RuntimeError, match="ApplyGuardContentionError"):
        _apply_stage(stopping)

    assert "live-schema-read" not in stopping.events
    assert "execute-run:apply" not in stopping.events
    run = stopping.stored()
    assert run.reconciliation_required is False
    assert run.phase != "applied"


def test_a_confirmed_apply_writes_once_the_configuration_is_free(stopping: _Harness) -> None:
    """The positive arm: the refusal above is about the other writer, not about the stopped run."""
    stopping.run_sync()
    admitted = stopping.admit_apply()
    assert admitted.state == "reserved", "The stopped sync still holds its write admission."
    stopping.submit_apply(admitted)
    stopping.events.clear()

    _apply_stage(stopping)

    assert stopping.events.index("guard-acquired") < stopping.events.index("execute-run:apply")
    assert stopping.stored().phase == "applied"
