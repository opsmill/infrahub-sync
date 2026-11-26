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

import ast
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from typing import TYPE_CHECKING, Any

import pytest

pytest.importorskip("prefect")
pytest.importorskip("opsmill_prefect_extras")
psycopg: Any = pytest.importorskip("psycopg")

from pathlib import Path  # noqa: E402

from infrahub_sync import execution  # noqa: E402
from infrahub_sync.configuration import ConfigurationPackage  # noqa: E402
from infrahub_sync.configuration.runtime import resolve_runtime_instance  # noqa: E402
from infrahub_sync.execution import RunResult, RunValidationError, execute_run  # noqa: E402
from infrahub_sync.plan.config_version import resolve_config_version  # noqa: E402
from infrahub_sync.plan.models import ApplyRecord  # noqa: E402
from infrahub_sync.plan.review import read_saved_plan  # noqa: E402
from infrahub_sync.plan.writer import write_plan_artifact  # noqa: E402
from infrahub_sync.product_store import (  # noqa: E402
    ExecutionMergeWriteback,
    MutationReceipt,
    PrefectExecutionLink,
    ProductRun,
    local_product_projection,
)
from infrahub_sync.runtime_schema import RuntimeModelPlan, RuntimeSideModels  # noqa: E402
from infrahub_sync.service import flow as service_flow  # noqa: E402
from infrahub_sync.service.flow import service_sync_run  # noqa: E402
from tests.configuration.validation_packages import (  # noqa: E402
    DESTINATION_CREDENTIAL,
    SOURCE_CREDENTIAL,
    credential_package,
)
from tests.helpers.stored_credentials import create_stored_credential_names  # noqa: E402
from tests.service.execution_fixtures import (  # noqa: E402
    publish_authored_plan,
    serve_released_credentials,
    stage_root,
    write_applied_sidecar,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

# The fields that the Sync API releases for each block of the registered package.
RELEASED = {"source": {"token": "managed-netbox-canary"}, "destination": {"token": "managed-infrahub-canary"}}
FLOW_RUN_ID = "b0c0a2d4-6f31-4a58-9b0e-2d4e6f318a55"
WORKER_ID = "4d9a1c77-2f8e-4d3b-9c11-6a0e7b2f9d40"
SCHEMA_FINGERPRINT = "d" * 64


class _FakeCursor:
    def __init__(self, row: tuple[Any, ...] | None) -> None:
        self._row = row

    def fetchone(self) -> tuple[Any, ...] | None:
        row, self._row = self._row, None
        return row


class _FakeGuardSession:
    """One scripted direct-PostgreSQL session that records the guard's statements."""

    def __init__(
        self,
        events: list[str],
        *,
        acquire_failure: BaseException | None = None,
        release_failure: BaseException | None = None,
        holder_label: str | None = None,
    ) -> None:
        self.events = events
        self.held = True
        self._acquire_failure = acquire_failure
        self._release_failure = release_failure
        self._holder_label = holder_label

    def execute(self, query: str, params: Sequence[Any] | None = None) -> _FakeCursor:
        _ = params
        if "pg_stat_activity" in query:
            return _FakeCursor(None if self._holder_label is None else (self._holder_label,))
        if "pg_advisory_lock" in query:
            if self._acquire_failure is not None:
                raise self._acquire_failure
            self.events.append("guard-acquired")
            return _FakeCursor((None,))
        if "pg_locks" in query:
            self.events.append("guard-proved")
            return _FakeCursor((1234, self.held))
        if "pg_advisory_unlock" in query:
            if self._release_failure is not None:
                raise self._release_failure
            self.events.append("guard-released")
            return _FakeCursor((True, 1234))
        return _FakeCursor(("configured",))

    def close(self) -> None:
        """Close the dedicated session."""


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


def _prepare(  # noqa: PLR0913, PLR0915 - one harness knob per collaborator a case scripts.
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    stage: str = "apply",
    acquire_failure: BaseException | None = None,
    release_failure: BaseException | None = None,
    commit_failure: BaseException | None = None,
    engine: Any = None,  # noqa: ANN401 - the double receives the engine's own keyword mapping.
    holder_label: str | None = None,
) -> _ManagedStage:
    """Wire one bound managed run whose guard, schema read, and engine are observable."""
    monkeypatch.setenv("INFRAHUB_SYNC_CACHE_DIR", str(tmp_path / "runs"))
    events: list[str] = []
    projection = local_product_projection(tmp_path / "product")
    create_stored_credential_names(projection, SOURCE_CREDENTIAL, DESTINATION_CREDENTIAL)
    serve_released_credentials(monkeypatch, service_flow, RELEASED, projection=projection)
    registered = projection.create_configuration(credential_package())
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
        ConfigurationPackage.model_validate(stored.declared_content), directory=str(tmp_path), released=RELEASED
    )
    runtime._configuration_binding = binding
    authored = tmp_path / "runs" / runtime.name / run_id
    manifest = write_plan_artifact(
        run_dir=authored,
        run_id=run_id,
        config_version=resolve_config_version(runtime),
        source_snapshot=[],
        deletes_computed=True,
        operations=[],
        configuration_binding=binding,
        schema_fingerprint=SCHEMA_FINGERPRINT,
    )
    if stage == "apply":
        # Only an apply has a predecessor: a sync publishes the plan it generates itself.
        publish_authored_plan(projection, run_id, run_directory=authored, manifest=manifest)
    prepared = _ManagedStage(run_id, binding, manifest.plan_checksum, events)
    prepared.projection = projection

    def guard_session() -> _FakeGuardSession:
        session = _FakeGuardSession(
            events, acquire_failure=acquire_failure, release_failure=release_failure, holder_label=holder_label
        )
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

    def saved_plan(base: Path) -> Any:  # noqa: ANN401 - the engine's own SavedPlan record.
        return read_saved_plan(sync_name=runtime.name, run_id=run_id, config=runtime, base_directory=base)

    def fake_execute_run(_instance: object, **kwargs: object) -> Any:  # noqa: ANN401 - result shape follows the stage.
        operation = kwargs.get("operation")
        events.append(f"execute-run:{operation}")
        base = stage_root(kwargs)
        if operation == "verify":
            return saved_plan(base)
        if engine is not None:
            engine(kwargs)
        # The engine leaves the applied sidecar the final checkpoint carries.
        write_applied_sidecar(base / runtime.name / run_id)
        return RunResult(
            sync_name=runtime.name,
            operation="apply",
            run_id=run_id,
            status="no-change",
            changed=False,
            summary={"create": 0, "update": 0, "delete": 0},
            artifact_path=str(tmp_path / "runs" / runtime.name / run_id),
        )

    def fake_plan(*_args: object, **kwargs: object) -> Any:  # noqa: ANN401 - the engine's own SavedPlan record.
        """Plan into this stage's own directory, the way the real engine does."""
        events.append("plan")
        base = stage_root(kwargs)
        write_plan_artifact(
            run_dir=base / runtime.name / run_id,
            run_id=run_id,
            config_version=resolve_config_version(runtime),
            source_snapshot=[],
            deletes_computed=True,
            operations=[],
            configuration_binding=binding,
            schema_fingerprint=SCHEMA_FINGERPRINT,
        )
        return saved_plan(base)

    monkeypatch.setattr(service_flow, "_runtime", lambda: projection)
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
    if commit_failure is not None:
        prepared.projection = _FailingSuccessCommit(projection, commit_failure)
        monkeypatch.setattr(service_flow, "_runtime", lambda: prepared.projection)
    return prepared


class _FailingSuccessCommit:
    """A projection whose *success* commit fails, and whose every other call passes through.

    Only the succeeded verdict is refused, so the failure boundary's own terminal commit —
    the ambiguous one — still reaches the store, which is exactly the path under test.
    """

    def __init__(self, projection: Any, failure: BaseException) -> None:  # noqa: ANN401 - the real projection.
        self._projection = projection
        self._failure = failure

    def commit_claimed_execution(self, *args: Any, **kwargs: Any) -> bool:  # noqa: ANN401 - the store's own shape.
        if kwargs.get("terminal_outcome") == "succeeded":
            raise self._failure
        return self._projection.commit_claimed_execution(*args, **kwargs)

    def __getattr__(self, name: str) -> Any:  # noqa: ANN401 - every other call is the real one.
        return getattr(self._projection, name)


def _stored(prepared: _ManagedStage) -> ProductRun:
    run = prepared.projection.lookup_run(prepared.run_id).value
    assert run is not None
    return run


def _drive(prepared: _ManagedStage, stage: str) -> dict[str, Any]:
    """Run whichever managed write stage this case prepared."""
    if stage == "apply":
        return _apply(prepared)
    result = service_sync_run.fn(prepared.run_id, "sync", *prepared.binding, confirm_writes=True)
    assert isinstance(result, dict)
    return result


def _apply(prepared: _ManagedStage) -> dict[str, Any]:
    result = service_sync_run.fn(
        prepared.run_id, "apply", *prepared.binding, expected_checksum=prepared.checksum, confirm_writes=True
    )
    assert isinstance(result, dict)
    return result


def test_a_managed_apply_holds_the_guard_before_its_live_schema_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A schema snapshot taken before a contended wait may describe a stale destination."""
    prepared = _prepare(tmp_path, monkeypatch)

    _apply(prepared)

    assert prepared.events.index("guard-acquired") < prepared.events.index("live-schema-read")
    assert prepared.events.index("live-schema-read") < prepared.events.index("execute-run:apply")
    assert prepared.events[-1] == "guard-released"


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


_HOLDER_FLOW_RUN_ID = "c1d2e3f4-0000-4000-8000-000000000001"


def _open_write_of_another_run(
    prepared: _ManagedStage, run_id: str, binding: tuple[str, int, str] | None = None
) -> None:
    """Record another run whose `sync` execution has not ended, of `binding` or this stage's configuration."""
    projection = prepared.projection
    config_id, registry_version, package_checksum = prepared.binding if binding is None else binding
    projection.create_run(
        ProductRun(
            run_id=run_id,
            operation="sync",
            configuration_reference=f"{config_id}@{registry_version}",
            config_id=config_id,
            registry_version=registry_version,
            package_checksum=package_checksum,
            actor="owner",
            started_at=datetime.now(timezone.utc),
            phase="accepted",
        )
    )
    admitted, _created = projection.reserve_mutation(
        _receipt(f"m-{run_id}", run_id=run_id, operation="sync"), admit_write=True
    )
    projection.add_prefect_execution(
        run_id,
        PrefectExecutionLink(
            flow_run_id=_HOLDER_FLOW_RUN_ID, purpose="sync", attempt=1, submitted_at=datetime.now(timezone.utc)
        ),
        receipt_id=admitted.receipt_id,
    )
    projection.complete_mutation(
        admitted.receipt_id, response_status=202, response_body={"accepted": True}, flow_run_id=_HOLDER_FLOW_RUN_ID
    )
    assert projection.claim_execution(run_id, _HOLDER_FLOW_RUN_ID, worker_id=WORKER_ID)


@pytest.mark.parametrize("stage", ["apply", "sync"])
def test_guard_contention_names_the_run_that_was_writing_to_the_configuration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stage: str
) -> None:
    """INFP-647: a refused concurrent write identifies the active run, and only by its ID."""
    prepared = _prepare(
        tmp_path,
        monkeypatch,
        stage=stage,
        acquire_failure=_lock_timeout(),
        holder_label="infrahub-sync-guard:managed-holder",
    )
    _open_write_of_another_run(prepared, "managed-holder")

    with pytest.raises(RuntimeError, match="ApplyGuardContentionError"):
        _drive(prepared, stage)

    failure = _stored(prepared).results[f"{stage}_failure"]
    assert failure["error_type"] == "ApplyGuardContentionError"
    assert failure["failure_type"] == "write_guard_contended"
    assert failure["active_run_id"] == "managed-holder"
    assert "Sync run managed-holder was writing to this configuration" in failure["cause"]
    assert "`infrahub-sync runs show managed-holder`" in failure["next_action"]
    assert failure["next_action"].endswith("then plan again.")
    assert "managed-netbox-canary" not in str(failure)
    assert "managed-infrahub-canary" not in str(failure)


@pytest.mark.parametrize("stage", ["apply", "sync"])
def test_the_configuration_write_guard_receives_the_stage_run_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stage: str
) -> None:
    """Critique E1: the managed write path always labels its guard session with its own run."""
    prepared = _prepare(tmp_path, monkeypatch, stage=stage)
    original = service_flow._configuration_write_guard
    calls: list[tuple[str, str]] = []

    def recording_guard(configuration_id: str, run_id: str) -> Any:  # noqa: ANN401 - the guard's own context manager.
        calls.append((configuration_id, run_id))
        return original(configuration_id, run_id)

    monkeypatch.setattr(service_flow, "_configuration_write_guard", recording_guard)

    _drive(prepared, stage)

    assert calls == [(prepared.binding[0], prepared.run_id)]


@pytest.mark.parametrize("stage", ["apply", "sync"])
def test_a_contended_failure_looks_up_the_holder_in_the_run_records_exactly_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stage: str
) -> None:
    """One contended failure costs one run-record read for its candidate, and no more."""
    prepared = _prepare(
        tmp_path,
        monkeypatch,
        stage=stage,
        acquire_failure=_lock_timeout(),
        holder_label="infrahub-sync-guard:managed-holder",
    )
    _open_write_of_another_run(prepared, "managed-holder")
    original = prepared.projection.write_holder_state
    calls: list[tuple[str, str]] = []

    def counting(run_id: str, config_id: str) -> Any:  # noqa: ANN401 - the store's own state record.
        calls.append((run_id, config_id))
        return original(run_id, config_id)

    monkeypatch.setattr(prepared.projection, "write_holder_state", counting)

    with pytest.raises(RuntimeError, match="ApplyGuardContentionError"):
        _drive(prepared, stage)

    assert calls == [("managed-holder", prepared.binding[0])]


def _end_write_of_another_run(prepared: _ManagedStage, run_id: str, ended_at: datetime) -> None:
    """Record the verdict of the write `_open_write_of_another_run` opened, ended at `ended_at`."""
    assert prepared.projection.commit_claimed_execution(
        run_id,
        _HOLDER_FLOW_RUN_ID,
        worker_id=WORKER_ID,
        terminal_at=ended_at,
        terminal_state="failed",
        terminal_outcome="failed",
        writeback=ExecutionMergeWriteback(results={}),
    )


_ENDED_CAUSE_FRAGMENT = "although its write had ended before that wait began"


def test_a_holder_whose_write_ended_before_the_wait_began_is_reported_as_ended(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A run that ended its write an hour before this write started waiting, and still holds the guard."""
    prepared = _prepare(
        tmp_path, monkeypatch, acquire_failure=_lock_timeout(), holder_label="infrahub-sync-guard:managed-holder"
    )
    _open_write_of_another_run(prepared, "managed-holder")
    _end_write_of_another_run(prepared, "managed-holder", datetime.now(timezone.utc) - timedelta(hours=1))

    with pytest.raises(RuntimeError, match="ApplyGuardContentionError"):
        _apply(prepared)

    failure = _stored(prepared).results["apply_failure"]
    assert failure["active_run_id"] == "managed-holder"
    assert _ENDED_CAUSE_FRAGMENT in failure["cause"]
    assert "stop or restart that worker" in failure["next_action"]


def test_a_holder_whose_write_ended_during_the_wait_is_reported_as_writing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Finding 1: a holder may release the lock and record its verdict after this write's lock timeout.

    Its write ended after this write started waiting, so the failure must not tell the operator to
    stop a worker that is healthy. The guard's clock is fixed at the wait start, and the holder's
    write ends one second later.
    """
    from infrahub_sync.service import apply_guard

    wait_started_at = datetime.now(timezone.utc) - timedelta(minutes=5)
    monkeypatch.setattr(apply_guard, "_utc_now", lambda: wait_started_at)
    prepared = _prepare(
        tmp_path, monkeypatch, acquire_failure=_lock_timeout(), holder_label="infrahub-sync-guard:managed-holder"
    )
    _open_write_of_another_run(prepared, "managed-holder")
    _end_write_of_another_run(prepared, "managed-holder", wait_started_at + timedelta(seconds=1))

    with pytest.raises(RuntimeError, match="ApplyGuardContentionError"):
        _apply(prepared)

    failure = _stored(prepared).results["apply_failure"]
    assert failure["active_run_id"] == "managed-holder"
    assert "Sync run managed-holder was writing to this configuration" in failure["cause"]
    assert _ENDED_CAUSE_FRAGMENT not in failure["cause"]
    assert "`infrahub-sync runs show managed-holder`" in failure["next_action"]


def test_a_holder_with_no_open_write_and_no_write_end_time_is_reported_as_writing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Without a time at which the holder's write ended, the records cannot show that it ended first."""
    prepared = _prepare(
        tmp_path, monkeypatch, acquire_failure=_lock_timeout(), holder_label="infrahub-sync-guard:managed-holder"
    )
    config_id, registry_version, package_checksum = prepared.binding
    prepared.projection.create_run(
        ProductRun(
            run_id="managed-holder",
            operation="sync",
            configuration_reference=f"{config_id}@{registry_version}",
            config_id=config_id,
            registry_version=registry_version,
            package_checksum=package_checksum,
            actor="owner",
            started_at=datetime.now(timezone.utc),
            phase="accepted",
        )
    )

    with pytest.raises(RuntimeError, match="ApplyGuardContentionError"):
        _apply(prepared)

    failure = _stored(prepared).results["apply_failure"]
    assert failure["active_run_id"] == "managed-holder"
    assert "Sync run managed-holder was writing to this configuration" in failure["cause"]
    assert _ENDED_CAUSE_FRAGMENT not in failure["cause"]


def test_a_contention_error_without_a_wait_start_assumes_the_largest_deadline() -> None:
    """Without the guard's stamp, the wait is taken to have started as early as it could have."""
    from infrahub_sync.service.apply_guard import (
        HOLDER_LOOKUP_TIMEOUT_SECONDS,
        MAX_DEADLINE_SECONDS,
        ApplyGuardContentionError,
    )

    stamped = datetime(2026, 11, 23, 9, tzinfo=timezone.utc)
    assert service_flow._wait_started_at(ApplyGuardContentionError("x", wait_started_at=stamped)) == stamped

    before = datetime.now(timezone.utc)
    started = service_flow._wait_started_at(ApplyGuardContentionError("x"))
    after = datetime.now(timezone.utc)
    earliest = timedelta(seconds=MAX_DEADLINE_SECONDS + HOLDER_LOOKUP_TIMEOUT_SECONDS)
    assert before - earliest <= started <= after - earliest


def test_guard_contention_without_another_write_says_the_active_run_is_not_known(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The failing run's own open execution is never named as the run that holds the guard."""
    prepared = _prepare(tmp_path, monkeypatch, acquire_failure=_lock_timeout())

    with pytest.raises(RuntimeError, match="ApplyGuardContentionError"):
        _apply(prepared)

    failure = _stored(prepared).results["apply_failure"]
    assert failure["failure_type"] == "write_guard_contended"
    assert failure["active_run_id"] is None
    assert "Which Sync run holds the guard is not known" in failure["cause"]


def test_a_failed_lookup_of_the_active_run_still_records_the_contention(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The lookup is best effort: its failure must not cost the run its failure record."""
    prepared = _prepare(
        tmp_path, monkeypatch, acquire_failure=_lock_timeout(), holder_label="infrahub-sync-guard:managed-holder"
    )

    def unavailable(*_args: object, **_kwargs: object) -> object:
        msg = "the product store did not answer"
        raise RuntimeError(msg)

    monkeypatch.setattr(prepared.projection, "write_holder_state", unavailable)

    with pytest.raises(RuntimeError, match="ApplyGuardContentionError"):
        _apply(prepared)

    run = _stored(prepared)
    assert run.outcome == "failed"
    failure = run.results["apply_failure"]
    assert (failure["failure_type"], failure["active_run_id"]) == ("write_guard_contended", None)


@pytest.mark.parametrize("candidate", ["failing-run", "unknown-run", "other-configuration"])
def test_a_candidate_the_run_records_do_not_confirm_is_recorded_as_not_known(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, candidate: str
) -> None:
    """FR-003, FR-005, R5: only a run of the same configuration, other than the failing one, is named.

    The failing run's own ID is refused before the run records are read. A label naming a run
    that does not exist, or a run of another configuration with an open write, is refused by the
    run records (`write_holder_state` returns `None`).
    """
    label_run_id = {
        "failing-run": "managed-apply",
        "unknown-run": "managed-unknown",
        "other-configuration": "managed-elsewhere",
    }[candidate]
    prepared = _prepare(
        tmp_path, monkeypatch, acquire_failure=_lock_timeout(), holder_label=f"infrahub-sync-guard:{label_run_id}"
    )
    assert prepared.run_id == "managed-apply"
    if candidate == "other-configuration":
        other = prepared.projection.create_configuration(credential_package(name="other-configuration"))
        assert other.config_id != prepared.binding[0]
        _open_write_of_another_run(
            prepared, label_run_id, (other.config_id, other.registry_version, other.package_checksum)
        )
    original = prepared.projection.write_holder_state
    answers: list[object] = []

    def recording(run_id: str, config_id: str) -> Any:  # noqa: ANN401 - the store's own state record.
        state = original(run_id, config_id)
        answers.append(state)
        return state

    monkeypatch.setattr(prepared.projection, "write_holder_state", recording)

    with pytest.raises(RuntimeError, match="ApplyGuardContentionError"):
        _apply(prepared)

    run = _stored(prepared)
    assert run.outcome == "failed"
    failure = run.results["apply_failure"]
    assert (failure["failure_type"], failure["active_run_id"]) == ("write_guard_contended", None)
    assert "Which Sync run holds the guard is not known" in failure["cause"]
    assert label_run_id not in str(failure)
    assert answers == ([] if candidate == "failing-run" else [None])


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


def test_the_managed_write_path_takes_no_local_pipeline_lock() -> None:
    """One correctness guard, not two: the file lock is gone from the supported path.

    Asserted on the shared execution surface as well, because the composition the managed
    path used to reach the pipeline lock through no longer exists at all.
    """
    assert service_flow.__file__ is not None
    flow_source = Path(service_flow.__file__).read_text(encoding="utf-8")

    assert "bounded_run_lock" not in flow_source
    assert not hasattr(execution, "bounded_run_lock")


def test_the_direct_sync_writer_is_refused_instead_of_running_unguarded() -> None:
    """The unsupported direct write path cannot prove any per-operation ownership."""
    with pytest.raises(RunValidationError, match="sync"):
        execute_run(object(), operation="sync", confirm_writes=True)  # ty: ignore[no-matching-overload]


def test_the_apply_overload_requires_its_boundary_and_its_completion_sink() -> None:
    """The declaration the type checker reads is what refuses an unguarded apply first.

    Plan and verify write nothing, so making either keyword unconditionally required would
    be worse than the defect it fixes. Declaring both required on the apply overload alone
    — and leaving `apply` out of every other overload — is what makes the gate's own type
    check reject an apply that proves nothing or reports nowhere, before any of this runs.
    """
    module = ast.parse(Path(execution.__file__).read_text(encoding="utf-8"))
    overloads = [
        node
        for node in module.body
        if isinstance(node, ast.FunctionDef)
        and node.name == "execute_run"
        and any(isinstance(item, ast.Name) and item.id == "overload" for item in node.decorator_list)
    ]
    assert overloads, "execute_run declares no overloads for the type checker to read"

    accepting_apply = [node for node in overloads if "apply" in _operation_literals(node)]
    assert len(accepting_apply) == 1, (
        f"{len(accepting_apply)} overloads accept operation='apply'; one of them would let an "
        f"unguarded apply type-check"
    )
    keywords = accepting_apply[0].args.kwonlyargs
    defaults = accepting_apply[0].args.kw_defaults
    declared = {argument.arg for argument in keywords}
    required = {argument.arg for argument, default in zip(keywords, defaults, strict=True) if default is None}

    for name, what in (("ownership", "write-ownership boundary"), ("record_applied", "completion sink")):
        assert name in declared, f"the apply overload does not declare a {what}"
        assert name in required, f"the apply overload's {what} has a default, so it can be omitted"


def _operation_literals(node: ast.FunctionDef) -> set[str]:
    """Return the operation names one overload's `operation` annotation admits."""
    annotation = next((argument.annotation for argument in node.args.kwonlyargs if argument.arg == "operation"), None)
    return (
        {
            element.value
            for element in ast.walk(annotation)
            if isinstance(element, ast.Constant) and isinstance(element.value, str)
        }
        if annotation is not None
        else set()
    )


def test_a_managed_apply_without_an_ownership_boundary_is_refused_at_run_time() -> None:
    """The runtime refusal stands behind the type-level one, for a caller that ignores it."""
    with pytest.raises(RunValidationError, match="ownership"):
        execute_run(  # ty: ignore[no-matching-overload]
            object(),
            operation="apply",
            run_id="managed-apply",
            confirm_writes=True,
        )


def test_a_stale_approved_checksum_is_refused_before_any_guard_session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A request that was never going to succeed must not make another writer wait."""
    prepared = _prepare(tmp_path, monkeypatch)

    with pytest.raises(RuntimeError, match="checksum"):
        service_sync_run.fn(
            prepared.run_id, "apply", *prepared.binding, expected_checksum="b" * 64, confirm_writes=True
        )

    assert prepared.session is None
    assert prepared.events == []
    run = _stored(prepared)
    assert run.reconciliation_required is False


def _dispatch_and_complete(kwargs: dict[str, Any]) -> None:
    """Stand in for the engine: dispatch once, then report what it completed.

    `_run_apply_lifecycle` reports the completed record through its own sink before its own
    sidecar write, which is the only reason a failure raised *after* the engine returned can
    still say what was written. The double does the same thing at the same point.
    """
    ownership = kwargs["ownership"]
    ownership.before_operation()
    ownership.after_final_operation()
    kwargs["record_applied"](ApplyRecord(applied_operations=("op-live-1",), skipped_delete_operations=("op-live-2",)))


@pytest.mark.parametrize("stage", ["apply", "sync"])
def test_a_release_that_cannot_be_confirmed_after_a_dispatch_is_ambiguous(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stage: str
) -> None:
    """An unconfirmable unlock after a write is uncertainty, and it still names the write.

    The engine returned cleanly and the applied sidecar is written, so nothing in the
    failure that unwinds carries a record — only the scope does. Losing it here would leave
    an operator an ambiguous run with no account of what reached the destination.
    """
    prepared = _prepare(
        tmp_path,
        monkeypatch,
        stage=stage,
        release_failure=psycopg.OperationalError("the guard session died before it could unlock"),
        engine=_dispatch_and_complete,
    )

    with pytest.raises(RuntimeError, match="ApplyGuardReleaseError"):
        _drive(prepared, stage)

    run = _stored(prepared)
    assert run.reconciliation_required is True
    assert run.outcome == "ambiguous"
    assert run.prefect_executions[0].terminal_state == "interrupted"
    failure = run.results[f"{stage}_failure"]
    assert failure["applied_operations"] == ["op-live-1"]
    assert failure["skipped_delete_operations"] == ["op-live-2"]


@pytest.mark.parametrize("stage", ["apply", "sync"])
def test_a_success_writeback_that_stores_nothing_after_a_dispatch_is_ambiguous(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stage: str
) -> None:
    """A product commit that did not land is not a run left for someone else to notice.

    The guard released cleanly and the write is done; only the success record failed. The
    run has to reach a terminal ambiguous verdict here, carrying the same record, rather
    than sitting non-terminal until a liveness pass eventually interrupts it.
    """
    prepared = _prepare(
        tmp_path,
        monkeypatch,
        stage=stage,
        commit_failure=RuntimeError("the product store refused the success commit"),
        engine=_dispatch_and_complete,
    )

    with pytest.raises(RuntimeError, match="refused the success commit"):
        _drive(prepared, stage)

    run = _stored(prepared)
    assert run.reconciliation_required is True
    assert run.outcome == "ambiguous"
    assert run.prefect_executions[0].terminal_state == "interrupted"
    failure = run.results[f"{stage}_failure"]
    assert failure["applied_operations"] == ["op-live-1"]
    assert failure["skipped_delete_operations"] == ["op-live-2"]
