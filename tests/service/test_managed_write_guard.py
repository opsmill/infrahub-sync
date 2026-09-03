"""The managed write path holds the configuration guard, and says what it may have done.

Two properties are under test together, because neither is safe alone. First, ordering:
the guard is held before the live schema read a managed write validates against, so a plan
cannot be checked against a snapshot taken while another writer still owned the
configuration. Second, classification: once the engine has proven ownership and started a
destination operation, no caught failure may be reported as a clean failure — it becomes
ambiguous, and the product run records that reconciliation is required.

The guard runs for real against one scripted direct-session fake, the same shape the guard's
own unit suite drives. Its provider facts are proven against a real server in
`tests/integration/test_managed_write_guard_integration.py`.
"""

from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
from typing import TYPE_CHECKING, Any

import pytest

pytest.importorskip("prefect")
pytest.importorskip("opsmill_prefect_extras")
psycopg: Any = pytest.importorskip("psycopg")

from pathlib import Path  # noqa: E402

from infrahub_sync.configuration import ConfigurationPackage  # noqa: E402
from infrahub_sync.configuration.runtime import resolve_runtime_instance  # noqa: E402
from infrahub_sync.execution import RunResult, RunValidationError, execute_run  # noqa: E402
from infrahub_sync.plan.config_version import resolve_config_version  # noqa: E402
from infrahub_sync.plan.review import read_saved_plan  # noqa: E402
from infrahub_sync.plan.writer import write_plan_artifact  # noqa: E402
from infrahub_sync.product_store import (  # noqa: E402
    MutationReceipt,
    PrefectExecutionLink,
    ProductRun,
    local_product_projection,
)
from infrahub_sync.runtime_schema import RuntimeModelPlan, RuntimeSideModels  # noqa: E402
from infrahub_sync.service import flow as service_flow  # noqa: E402
from infrahub_sync.service.flow import service_sync_run  # noqa: E402
from tests.configuration.validation_packages import package  # noqa: E402

if TYPE_CHECKING:
    from collections.abc import Sequence

FLOW_RUN_ID = "b0c0a2d4-6f31-4a58-9b0e-2d4e6f318a55"
WORKER_ID = "4d9a1c77-2f8e-4d3b-9c11-6a0e7b2f9d40"
SCHEMA_FINGERPRINT = "d" * 64


class _FakeCursor:
    def __init__(self, row: tuple[Any, ...] | None) -> None:
        self._row = row

    def fetchone(self) -> tuple[Any, ...] | None:
        return self._row


class _FakeGuardSession:
    """One scripted direct-PostgreSQL session that records the guard's statements."""

    def __init__(self, events: list[str], *, acquire_failure: BaseException | None = None) -> None:
        self.events = events
        self.closed = False
        self.held = True
        self._acquire_failure = acquire_failure

    def execute(self, query: str, params: Sequence[Any] | None = None) -> _FakeCursor:
        _ = params
        if "pg_advisory_lock" in query:
            if self._acquire_failure is not None:
                raise self._acquire_failure
            self.events.append("guard-acquired")
            return _FakeCursor((None,))
        if "pg_locks" in query:
            self.events.append("guard-proved")
            return _FakeCursor((1234, self.held))
        if "pg_advisory_unlock" in query:
            self.events.append("guard-released")
            return _FakeCursor((True, 1234))
        return _FakeCursor(("configured",))

    def close(self) -> None:
        self.closed = True


def _lock_timeout() -> BaseException:
    """The exact driver failure PostgreSQL raises when `lock_timeout` expires."""
    return psycopg.errors.LockNotAvailable("canceling statement due to lock timeout")


def _receipt(receipt_id: str, *, run_id: str, operation: str) -> MutationReceipt:
    now = datetime.now(timezone.utc)
    return MutationReceipt(
        receipt_id=receipt_id,
        actor="owner",
        key_digest=sha256(receipt_id.encode()).hexdigest(),
        operation=operation,
        target_run_id=run_id,
        request_fingerprint=sha256(f"{operation}:{run_id}".encode()).hexdigest(),
        reason="managed write guard contract",
        resource_id=run_id,
        run_id=run_id,
        prefect_key=sha256(f"prefect:{receipt_id}".encode()).hexdigest(),
        created_at=now,
        updated_at=now,
    )


class _ManagedStage:
    """One prepared managed write stage, with every collaborator recorded."""

    def __init__(self, run_id: str, binding: tuple[str, int, str], checksum: str, events: list[str]) -> None:
        self.run_id = run_id
        self.binding = binding
        self.checksum = checksum
        self.events = events
        self.session: _FakeGuardSession | None = None
        self.projection: Any = None


def _prepare(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    stage: str = "apply",
    acquire_failure: BaseException | None = None,
    engine: Any = None,  # noqa: ANN401 - the double receives the engine's own keyword mapping.
) -> _ManagedStage:
    """Wire one bound managed run whose guard, schema read, and engine are observable."""
    monkeypatch.setenv("NETBOX_TOKEN", "managed-netbox-canary")
    monkeypatch.setenv("INFRAHUB_API_TOKEN", "managed-infrahub-canary")
    monkeypatch.setenv("INFRAHUB_SYNC_CACHE_DIR", str(tmp_path / "runs"))
    events: list[str] = []
    projection = local_product_projection(tmp_path / "product")
    registered = projection.create_configuration(package())
    binding = (registered.config_id, registered.registry_version, registered.package_checksum)
    run_id = f"managed-{stage}"
    projection.create_run(
        ProductRun(
            run_id=run_id,
            operation="apply" if stage == "apply" else "sync",
            configuration_reference=f"{binding[0]}@{binding[1]}",
            config_id=binding[0],
            registry_version=binding[1],
            package_checksum=binding[2],
            actor="owner",
            started_at=datetime.now(timezone.utc),
            phase="planned",
        )
    )
    admitted, _created = projection.reserve_mutation(
        _receipt("m-write", run_id=run_id, operation=stage), admit_write=True
    )
    projection.add_prefect_execution(
        run_id,
        PrefectExecutionLink(
            flow_run_id=FLOW_RUN_ID, purpose=stage, attempt=1, submitted_at=datetime.now(timezone.utc)
        ),
        receipt_id=admitted.receipt_id,
    )
    stored = projection.lookup_configuration_version(binding[0], binding[1]).value
    assert stored is not None
    runtime = resolve_runtime_instance(
        ConfigurationPackage.model_validate(stored.declared_content), directory=str(tmp_path)
    )
    runtime._configuration_binding = binding
    manifest = write_plan_artifact(
        run_dir=tmp_path / "runs" / runtime.name / run_id,
        run_id=run_id,
        config_version=resolve_config_version(runtime),
        source_snapshot=[],
        deletes_computed=True,
        operations=[],
        configuration_binding=binding,
        schema_fingerprint=SCHEMA_FINGERPRINT,
    )
    prepared = _ManagedStage(run_id, binding, manifest.plan_checksum, events)
    prepared.projection = projection

    def guard_session() -> _FakeGuardSession:
        session = _FakeGuardSession(events, acquire_failure=acquire_failure)
        prepared.session = session
        return session

    def build_models(**_kwargs: object) -> RuntimeModelPlan:
        events.append("live-schema-read")
        return RuntimeModelPlan(
            branch="main",
            schema_fingerprint=SCHEMA_FINGERPRINT,
            destination=RuntimeSideModels(adapter_class=object, models={}),
            source=None,
        )

    def saved_plan() -> Any:  # noqa: ANN401 - the engine's own SavedPlan record.
        return read_saved_plan(sync_name=runtime.name, run_id=run_id, config=runtime)

    def fake_execute_run(_instance: object, **kwargs: object) -> Any:  # noqa: ANN401 - result shape follows the stage.
        operation = kwargs.get("operation")
        events.append(f"execute-run:{operation}")
        if operation == "verify":
            return saved_plan()
        if engine is not None:
            engine(kwargs)
        return RunResult(
            sync_name=runtime.name,
            operation="apply",
            run_id=run_id,
            status="no-change",
            changed=False,
            summary={"create": 0, "update": 0, "delete": 0},
            artifact_path=str(tmp_path / "runs" / runtime.name / run_id),
        )

    def fake_plan(*_args: object, **_kwargs: object) -> Any:  # noqa: ANN401 - the engine's own SavedPlan record.
        events.append("plan")
        return saved_plan()

    monkeypatch.setattr(service_flow, "_runtime", lambda: (str(tmp_path), projection))
    monkeypatch.setattr(service_flow, "_run_logger", lambda: (service_flow.logger, False))
    monkeypatch.setenv("PREFECT__WORKER_ID", WORKER_ID)
    monkeypatch.setattr(service_flow, "_prefect_flow_run_id", lambda: FLOW_RUN_ID)
    monkeypatch.setattr(service_flow, "_require_current_worker_identity", lambda *_args: None)
    monkeypatch.setattr(service_flow, "build_runtime_model_plan", build_models)
    monkeypatch.setattr(service_flow, "execute_run", fake_execute_run)
    monkeypatch.setattr(service_flow, "_plan", fake_plan)
    monkeypatch.setattr(service_flow, "_publish_plan", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(service_flow, "service_guard_session", guard_session)
    monkeypatch.setattr(service_flow, "service_guard_secrets", lambda: ())
    return prepared


def _stored(prepared: _ManagedStage) -> ProductRun:
    run = prepared.projection.lookup_run(prepared.run_id).value
    assert run is not None
    return run


def _apply(prepared: _ManagedStage) -> dict[str, Any]:
    return service_sync_run.fn(
        prepared.run_id, "apply", *prepared.binding, expected_checksum=prepared.checksum, confirm_writes=True
    )


def test_a_managed_apply_holds_the_guard_before_its_live_schema_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A schema snapshot taken before a contended wait may describe a stale destination."""
    prepared = _prepare(tmp_path, monkeypatch)

    _apply(prepared)

    assert prepared.events.index("guard-acquired") < prepared.events.index("live-schema-read")
    assert prepared.events.index("live-schema-read") < prepared.events.index("execute-run:apply")
    assert prepared.events[-1] == "guard-released"


def test_a_managed_apply_confirms_release_before_the_product_success_writeback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A success published while the key may still be held is a success nobody can trust."""
    prepared = _prepare(tmp_path, monkeypatch)

    _apply(prepared)

    run = _stored(prepared)
    assert (run.phase, run.outcome) == ("applied", "no-change")
    assert prepared.session is not None
    assert prepared.session.closed is True
    assert run.reconciliation_required is False


def test_guard_contention_touches_no_destination_and_creates_no_success_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Another writer's hold is a clean refusal: nothing read, nothing written, no ambiguity."""
    prepared = _prepare(tmp_path, monkeypatch, acquire_failure=_lock_timeout())

    with pytest.raises(RuntimeError, match="ApplyGuardContentionError"):
        _apply(prepared)

    assert "live-schema-read" not in prepared.events
    assert "execute-run:apply" not in prepared.events
    run = _stored(prepared)
    assert run.reconciliation_required is False
    assert run.outcome == "failed"


def test_a_failure_before_the_first_dispatch_stays_an_ordinary_failed_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A refusal under the guard that never dispatched is not an uncertain write."""

    def refuse(_kwargs: dict[str, object]) -> None:
        msg = "the destination refused the plan before any operation"
        raise ValueError(msg)

    prepared = _prepare(tmp_path, monkeypatch, engine=refuse)

    with pytest.raises(RuntimeError, match="before any operation"):
        _apply(prepared)

    run = _stored(prepared)
    assert run.reconciliation_required is False
    assert run.outcome == "failed"
    link = run.prefect_executions[0]
    assert (link.terminal_state, link.terminal_outcome) == ("failed", "failed")


def test_a_failure_after_the_first_dispatch_becomes_durable_ambiguity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Once one operation has been dispatched, no caught failure may claim a clean stop."""

    def dispatch_then_fail(kwargs: dict[str, Any]) -> None:
        kwargs["ownership"].before_operation()
        msg = "the destination failed after the first operation"
        raise ValueError(msg)

    prepared = _prepare(tmp_path, monkeypatch, engine=dispatch_then_fail)

    with pytest.raises(RuntimeError, match="after the first operation"):
        _apply(prepared)

    run = _stored(prepared)
    assert run.reconciliation_required is True
    assert run.outcome == "ambiguous"
    link = run.prefect_executions[0]
    assert (link.terminal_state, link.terminal_outcome) == ("interrupted", "ambiguous")


def test_a_lost_session_after_the_first_dispatch_becomes_durable_ambiguity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A guard that cannot prove itself after a dispatch leaves the destination uncertain."""
    prepared_holder: list[_ManagedStage] = []

    def dispatch_then_lose(kwargs: dict[str, Any]) -> None:
        ownership = kwargs["ownership"]
        ownership.before_operation()
        session = prepared_holder[0].session
        assert session is not None
        session.held = False
        ownership.before_operation()

    prepared = _prepare(tmp_path, monkeypatch, engine=dispatch_then_lose)
    prepared_holder.append(prepared)

    with pytest.raises(Exception):  # noqa: B017 - the guard's own ownership failure.
        _apply(prepared)

    run = _stored(prepared)
    assert run.reconciliation_required is True
    assert run.outcome == "ambiguous"


def test_a_managed_sync_holds_one_guard_across_planning_and_applying(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A plan generated outside the guard could go stale behind another writer."""
    prepared = _prepare(tmp_path, monkeypatch, stage="sync")

    service_sync_run.fn(prepared.run_id, "sync", *prepared.binding, confirm_writes=True)

    acquired = prepared.events.index("guard-acquired")
    assert acquired < prepared.events.index("plan")
    assert acquired < prepared.events.index("live-schema-read")
    assert acquired < prepared.events.index("execute-run:apply")
    assert prepared.events[-1] == "guard-released"


def test_a_managed_write_without_a_registered_binding_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A guard keyed on a configuration cannot serialize a run that names none."""
    monkeypatch.setenv("PREFECT__WORKER_ID", WORKER_ID)
    monkeypatch.setattr(service_flow, "_prefect_flow_run_id", lambda: FLOW_RUN_ID)
    monkeypatch.setattr(service_flow, "_require_current_worker_identity", lambda *_args: None)
    monkeypatch.setenv("INFRAHUB_SYNC_CACHE_DIR", str(tmp_path / "runs"))
    projection = local_product_projection(tmp_path / "product")
    run_id = "legacy-apply"
    projection.create_run(
        ProductRun(
            run_id=run_id,
            operation="apply",
            configuration_reference="legacy-config-version",
            actor="owner",
            started_at=datetime.now(timezone.utc),
            phase="planned",
            summary={"sync_name": "legacy-inventory"},
        )
    )
    reserved, _created = projection.reserve_mutation(
        _receipt("m-legacy", run_id=run_id, operation="apply"), admit_write=True
    )
    projection.add_prefect_execution(
        run_id,
        PrefectExecutionLink(
            flow_run_id=FLOW_RUN_ID, purpose="apply", attempt=1, submitted_at=datetime.now(timezone.utc)
        ),
        receipt_id=reserved.receipt_id,
    )
    guard_sessions: list[object] = []
    monkeypatch.setattr(service_flow, "_runtime", lambda: (str(tmp_path), projection))
    monkeypatch.setattr(service_flow, "_run_logger", lambda: (service_flow.logger, False))
    monkeypatch.setattr(service_flow, "service_guard_session", lambda: guard_sessions.append(object()))

    with pytest.raises(RuntimeError, match="registered configuration binding"):
        service_sync_run.fn(run_id, "apply", expected_checksum="a" * 64, confirm_writes=True)

    assert guard_sessions == []


def test_the_managed_write_path_takes_no_local_pipeline_lock() -> None:
    """One correctness guard, not two: the file lock is gone from the supported path."""
    assert service_flow.__file__ is not None
    source = Path(service_flow.__file__).read_text(encoding="utf-8")

    assert "bounded_run_lock" not in source


def test_the_direct_sync_writer_is_refused_instead_of_running_unguarded() -> None:
    """The unsupported direct write path cannot prove any per-operation ownership."""
    with pytest.raises(RunValidationError, match="sync"):
        execute_run(object(), operation="sync", confirm_writes=True)  # ty: ignore[no-matching-overload]


def test_a_managed_apply_without_an_ownership_boundary_is_refused() -> None:
    """`execute_run` refuses a write it cannot prove, before it builds anything."""
    with pytest.raises(RunValidationError, match="ownership"):
        execute_run(  # ty: ignore[no-matching-overload]
            object(),
            operation="apply",
            run_id="managed-apply",
            confirm_writes=True,
        )
