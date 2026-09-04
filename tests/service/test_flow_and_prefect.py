from __future__ import annotations

import inspect
import logging
from datetime import datetime, timezone
from pathlib import Path
from threading import Event, Thread, current_thread
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, Literal, NoReturn, Protocol
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

import httpx
import pytest
from typing_extensions import Self

pytest.importorskip("prefect")
pytest.importorskip("opsmill_prefect_extras")

from opsmill_prefect_extras.deployments import apply_deployments
from opsmill_prefect_extras.workflows import assert_valid_definitions
from prefect.client.schemas.objects import State, StateType
from prefect.client.schemas.responses import (
    OrchestrationResult,
    SetStateStatus,
    StateAbortDetails,
    StateAcceptDetails,
    StateRejectDetails,
    StateWaitDetails,
)
from prefect.exceptions import MissingContextError, ObjectNotFound
from prefect.states import Cancelled, Cancelling, Failed, Pending, Running

from infrahub_sync.execution import RunResult
from infrahub_sync.orchestration import flow as direct_flow
from infrahub_sync.orchestration.flow import infrahub_sync_run
from infrahub_sync.plan.errors import OperationApplyFailedError
from infrahub_sync.plan.models import ApplyRecord, PlanManifest
from infrahub_sync.plan.review import SavedPlan
from infrahub_sync.product_store import PrefectExecutionLink, ProductRun, local_product_projection
from infrahub_sync.service import flow as service_flow
from infrahub_sync.service.deploy import CATALOGUE
from infrahub_sync.service.flow import service_sync_run
from infrahub_sync.service.orchestration import (
    SERVICE_DEFINITION,
    CancellationResult,
    Observation,
    PrefectOrchestration,
)
from tests.configuration.validation_packages import package
from tests.service.execution_fixtures import append_execution, bind_granting_guard

if TYPE_CHECKING:
    from prefect.client.schemas.actions import DeploymentUpdate

    from infrahub_sync.configuration import ConfigurationPackage


@pytest.fixture
def _claimed_worker_execution(monkeypatch: pytest.MonkeyPatch) -> None:
    """Explicitly give direct ``.fn`` tests one real durable worker claim."""
    worker_id = "8c1da53d-0e6b-4d3d-a0f1-97b6a9ccebf0"

    def claim(projection, run_id: str) -> tuple[str, str]:
        flow_run_id = str(uuid5(NAMESPACE_URL, run_id))
        run = projection.lookup_run(run_id).value
        if run is not None and not any(link.flow_run_id == flow_run_id for link in run.prefect_executions):
            append_execution(
                projection,
                run_id,
                PrefectExecutionLink(flow_run_id=flow_run_id, purpose="test", attempt=1),
            )
            assert projection.claim_execution(run_id, flow_run_id, worker_id=worker_id)
        return flow_run_id, worker_id

    monkeypatch.setattr(service_flow, "_claim_current_execution", claim)


@pytest.fixture(autouse=True)
def _stub_runtime_model_plan(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep lifecycle tests focused on flow behavior, not schema composition."""

    def build(*_args: object, **_kwargs: object) -> object:
        return object()

    monkeypatch.setattr(service_flow, "build_runtime_model_plan", build)


def _saved(run_id: str) -> SavedPlan:
    manifest = PlanManifest(
        format_version=2,
        run_id=run_id,
        created_at="2026-08-10T12:00:00+00:00",
        config_version="configuration-v1",
        source_snapshot=[],
        operations_count=0,
        delete_operations_computed=True,
        plan_checksum="a" * 64,
    )
    return SavedPlan(manifest=manifest, operations=[], checksum_ok=True, verification_notes=[])


def _instance(
    configuration: ConfigurationPackage,
    *,
    directory: str,
    resolve_source_credentials: bool = True,
) -> SimpleNamespace:
    """Build the flow's minimal runtime fake from a registered package."""
    del directory, resolve_source_credentials
    return SimpleNamespace(name=configuration.configuration.name)


class _RecordingRunLogger:
    def __init__(self) -> None:
        self.rendered: list[str] = []

    def log(self, level: int, msg: str, *args: object) -> None:
        del level
        self.rendered.append(msg % args)

    def info(self, msg: str, *args: object) -> None:
        self.rendered.append(msg % args)


class _LockDelegate(Protocol):
    def acquire(self) -> bool: ...

    def release(self) -> None: ...


class _LoggerOwnershipProbe:
    """Record lock acquisition order while delegating synchronization unchanged."""

    def __init__(self, delegate: _LockDelegate) -> None:
        self._delegate = delegate
        self.events: list[str] = []
        self.service_acquire_attempted = Event()

    def acquire(self) -> None:
        """Signal from inside the contender's acquisition attempt, then delegate."""
        thread_name = current_thread().name
        self.events.append(f"{thread_name}:acquire-attempted")
        if thread_name == "test-service-flow":
            self.service_acquire_attempted.set()
        self._delegate.acquire()
        self.events.append(f"{thread_name}:acquired")

    def release(self) -> None:
        """Record release before making ownership available to a contender."""
        self.events.append(f"{current_thread().name}:released")
        self._delegate.release()

    def __enter__(self) -> Self:
        self.acquire()
        return self

    def __exit__(self, *_args: object) -> None:
        self.release()


def _create_product_run(
    cache: Path,
    run_id: str,
    *,
    operation: Literal["plan", "sync", "verify", "apply"] = "plan",
):
    projection = local_product_projection(cache)
    version = projection.create_configuration(package())
    projection.create_run(
        ProductRun(
            run_id=run_id,
            operation=operation,
            configuration_reference=f"{version.config_id}@{version.registry_version}",
            config_id=version.config_id,
            registry_version=version.registry_version,
            package_checksum=version.package_checksum,
            actor="owner",
            started_at=datetime.now(timezone.utc),
            phase="accepted",
            summary={"sync_name": "inventory"},
        )
    )
    return projection


def _binding(projection, run_id: str) -> tuple[str, int, str]:
    run = projection.lookup_run(run_id).value
    assert run is not None
    assert run.configuration_binding is not None
    return run.configuration_binding


@pytest.mark.usefixtures("_claimed_worker_execution")
def test_worker_rejects_missing_registered_package_before_runtime_construction(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A bound worker cannot fall back to caller-local configuration files."""
    run_id = "run-missing-registered-package"
    projection = local_product_projection(tmp_path)
    projection.create_run(
        ProductRun(
            run_id=run_id,
            operation="plan",
            configuration_reference="config-001@1",
            config_id="config-001",
            registry_version=1,
            package_checksum="a" * 64,
            actor="owner",
            started_at=datetime.now(timezone.utc),
            phase="accepted",
        )
    )
    constructed = []
    monkeypatch.setattr(service_flow, "_runtime", lambda: (str(tmp_path), projection))
    bind_granting_guard(monkeypatch, service_flow)
    monkeypatch.setattr(service_flow, "_run_logger", lambda: (logging.getLogger("test-service"), False))
    monkeypatch.setattr(service_flow, "resolve_runtime_instance", lambda *_args, **_kwargs: constructed.append(True))

    with pytest.raises(RuntimeError, match="registered configuration version is unavailable"):
        service_sync_run.fn(run_id, "plan", "config-001", 1, "a" * 64)
    assert constructed == []


def test_service_and_direct_prefect_flow_schemas_are_separate_and_exact() -> None:
    assert tuple(inspect.signature(service_sync_run.fn).parameters) == (
        "run_id",
        "stage",
        "config_id",
        "registry_version",
        "package_checksum",
        "branch",
        "expected_checksum",
        "confirm_writes",
    )
    assert tuple(inspect.signature(infrahub_sync_run.fn).parameters) == (
        "sync_name",
        "operation",
        "confirm_writes",
        "branch",
    )
    assert CATALOGUE.keys() == (SERVICE_DEFINITION.key,)
    assert_valid_definitions(CATALOGUE)


def test_missing_context_uses_local_logger_without_constructing_a_bridge(monkeypatch: pytest.MonkeyPatch) -> None:
    def missing_context():
        raise MissingContextError

    def bridge_is_forbidden(_logger):
        msg = "RunLoggerBridge must not be constructed in the local fallback"
        raise AssertionError(msg)

    monkeypatch.setattr(service_flow, "get_run_logger", missing_context)
    monkeypatch.setattr(service_flow, "RunLoggerBridge", bridge_is_forbidden)

    run_logger, prefect_context = service_flow._run_logger()
    with service_flow._remote_log_bridge(run_logger, prefect_context=prefect_context):
        run_logger.info("local service execution")

    assert isinstance(run_logger, logging.Logger)
    assert prefect_context is False


def test_direct_and_service_log_bridges_serialize_ownership_and_restore_state(  # noqa: PLR0914, PLR0915
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Concurrent flow bridges must not share records or clobber logger state."""
    direct_canary = "direct-flow-secret-canary"
    service_canary = "service-flow-secret-canary"
    direct_logger = _RecordingRunLogger()
    service_logger = _RecordingRunLogger()
    source_logger = logging.getLogger(service_flow.SOURCE_LOGGER_NAME)
    child_logger = logging.getLogger(f"{service_flow.SOURCE_LOGGER_NAME}.concurrency-test")
    sentinel_handler = logging.NullHandler()
    original_handlers = list(source_logger.handlers)
    original_level = source_logger.level
    original_propagate = source_logger.propagate
    direct_entered = Event()
    release_direct = Event()
    service_entered = Event()
    release_service = Event()
    direct_failures: list[BaseException] = []
    service_failures: list[BaseException] = []

    def fail_direct_request(*_args: object, **_kwargs: object) -> NoReturn:
        direct_entered.set()
        assert release_direct.wait(timeout=5)
        failure_message = "direct flow failed"
        raise RuntimeError(failure_message)

    def run_direct() -> None:
        try:
            infrahub_sync_run.fn("inventory")
        except BaseException as exc:  # noqa: BLE001 - retain thread failure for the main test.
            direct_failures.append(exc)

    def run_service_bridge() -> None:
        try:
            with service_flow._remote_log_bridge(
                service_logger,
                prefect_context=True,
                secrets=(service_canary,),
            ):
                service_entered.set()
                child_logger.warning("service record used %s", service_canary)
                assert release_service.wait(timeout=5)
        except BaseException as exc:  # noqa: BLE001 - retain thread failure for the main test.
            service_failures.append(exc)

    monkeypatch.setattr("infrahub_sync.orchestration.flow.get_run_logger", lambda: direct_logger)
    monkeypatch.setattr("infrahub_sync.orchestration.flow.collect_secret_values", lambda: (direct_canary,))
    monkeypatch.setattr("infrahub_sync.orchestration.flow.run_remote_request", fail_direct_request)
    monkeypatch.setenv(service_flow.CONFIG_DIR_ENV, str(tmp_path))
    original_ownership_lock = direct_flow._REMOTE_LOGGER_OWNERSHIP_LOCK
    assert service_flow._REMOTE_LOGGER_OWNERSHIP_LOCK is original_ownership_lock
    ownership_probe = _LoggerOwnershipProbe(original_ownership_lock)
    monkeypatch.setattr(direct_flow, "_REMOTE_LOGGER_OWNERSHIP_LOCK", ownership_probe)
    monkeypatch.setattr(service_flow, "_REMOTE_LOGGER_OWNERSHIP_LOCK", ownership_probe)
    source_logger.handlers = [sentinel_handler]
    source_logger.setLevel(logging.ERROR)
    source_logger.propagate = True

    direct_thread = Thread(target=run_direct, name="test-direct-flow")
    service_thread = Thread(target=run_service_bridge, name="test-service-flow")
    try:
        direct_thread.start()
        assert direct_entered.wait(timeout=5)
        service_thread.start()
        assert ownership_probe.service_acquire_attempted.wait(timeout=5)
        child_logger.warning("direct record used %s", direct_canary)

        release_direct.set()
        direct_thread.join(timeout=5)
        assert not direct_thread.is_alive()
        assert service_entered.wait(timeout=5)
        release_service.set()
        service_thread.join(timeout=5)
        assert not service_thread.is_alive()

        rendered = "\n".join((*direct_logger.rendered, *service_logger.rendered))
        expected_acquisition_order = [
            "test-direct-flow:acquire-attempted",
            "test-direct-flow:acquired",
            "test-service-flow:acquire-attempted",
            "test-direct-flow:released",
            "test-service-flow:acquired",
            "test-service-flow:released",
        ]
        violations = [
            label
            for label, violated in (
                ("bridge ownership was not serialized", ownership_probe.events != expected_acquisition_order),
                (
                    "direct bridge received the service record",
                    any("service record" in line for line in direct_logger.rendered),
                ),
                (
                    "service bridge received the direct record",
                    any("direct record" in line for line in service_logger.rendered),
                ),
                ("direct canary reached a run logger", direct_canary in rendered),
                ("service canary reached a run logger", service_canary in rendered),
                ("source handlers were not restored", source_logger.handlers != [sentinel_handler]),
                ("source level was not restored", source_logger.level != logging.ERROR),
                ("source propagation was not restored", source_logger.propagate is not True),
            )
            if violated
        ]
        assert violations == []
        assert len(direct_failures) == 1
        assert isinstance(direct_failures[0], RuntimeError)
        assert str(direct_failures[0]) == "direct flow failed"
        assert service_failures == []
    finally:
        release_direct.set()
        release_service.set()
        for thread in (direct_thread, service_thread):
            if thread.ident is not None:
                thread.join(timeout=5)
        source_logger.handlers = original_handlers
        source_logger.setLevel(original_level)
        source_logger.propagate = original_propagate


@pytest.mark.usefixtures("_claimed_worker_execution")
def test_service_flow_redacts_worker_logs_exception_chain_and_failed_state(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    environment_canary = "worker-environment-token-canary"
    configuration_canary = "worker-configuration-token-canary"
    run_id = "run-service-secret-failure"
    projection = _create_product_run(tmp_path.resolve(), run_id)
    run_logger = _RecordingRunLogger()
    instance = SimpleNamespace(
        name="inventory",
        source=SimpleNamespace(settings={"token": configuration_canary}),
        destination=SimpleNamespace(settings={}),
        store=None,
    )
    monkeypatch.setenv("NETBOX_TOKEN", environment_canary)
    monkeypatch.setattr(service_flow, "_runtime", lambda: (str(tmp_path), projection))
    bind_granting_guard(monkeypatch, service_flow)
    monkeypatch.setattr(service_flow, "_run_logger", lambda: (run_logger, True))

    def resolve(
        _sync_name: str,
        *,
        directory: str,
        resolve_source_credentials: bool = True,
    ):
        del directory, resolve_source_credentials
        logging.getLogger("infrahub_sync.service.worker").warning(
            "resolution used %s",
            environment_canary,
        )
        return instance

    def fail_plan(_instance, *, run_id: str, branch: str | None, composed_sync: bool, **_kwargs: object):  # noqa: ARG001
        logging.getLogger("infrahub_sync.service.worker").error(
            "execution used %s",
            configuration_canary,
        )
        cause_message = f"transport rejected {environment_canary}"
        failure_message = f"adapter rejected {configuration_canary}"
        raise ValueError(failure_message) from ConnectionError(cause_message)

    monkeypatch.setattr(service_flow, "resolve_runtime_instance", resolve)
    monkeypatch.setattr(service_flow, "_plan", fail_plan)

    with pytest.raises(RuntimeError) as exc_info:
        service_sync_run.fn(run_id, "plan", *_binding(projection, run_id))

    failure = exc_info.value
    failed_state = Failed(message=str(failure), data=failure)
    chain: list[BaseException] = []
    current: BaseException | None = failure
    while current is not None:
        chain.append(current)
        current = current.__cause__
    scanned = repr((run_logger.rendered, chain, failed_state))
    assert environment_canary not in scanned
    assert configuration_canary not in scanned
    assert "***" in scanned
    assert failure.__context__ is None
    stored = projection.lookup_run(run_id).value
    assert stored is not None
    assert stored.phase == "plan-failed"
    assert stored.outcome == "failed"
    assert stored.results["plan_failure"] == {
        "stage": "plan",
        "outcome": "failed",
        "error_type": "ValueError",
    }
    assert environment_canary not in stored.model_dump_json()
    assert configuration_canary not in stored.model_dump_json()
    assert stored.prefect_executions[0].terminal_state == "failed"
    assert stored.prefect_executions[0].terminal_outcome == "failed"


@pytest.mark.usefixtures("_claimed_worker_execution")
def test_service_apply_failure_retains_partial_write_evidence(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    run_id = "run-service-apply-failure"
    projection = _create_product_run(tmp_path.resolve(), run_id)
    partial = ApplyRecord(applied_operations=("op-applied",), failed_operation="op-failed")
    monkeypatch.setattr(service_flow, "_runtime", lambda: (str(tmp_path), projection))
    bind_granting_guard(monkeypatch, service_flow)
    monkeypatch.setattr(service_flow, "_run_logger", lambda: (logging.getLogger("test-service"), False))
    monkeypatch.setattr(service_flow, "resolve_runtime_instance", _instance)
    monkeypatch.setattr(service_flow, "collect_secret_values", lambda _instance=None: ())
    monkeypatch.setattr(service_flow, "_verify_registered_apply", lambda **_kwargs: None)
    monkeypatch.setattr(service_flow, "_require_planned_schema", lambda **_kwargs: None)

    def fail_apply(*_args: object, **_kwargs: object) -> NoReturn:
        msg = "destination rejected operation"
        raise OperationApplyFailedError(msg, apply_record=partial)

    monkeypatch.setattr(service_flow, "execute_run", fail_apply)

    with pytest.raises(RuntimeError):
        service_sync_run.fn(
            run_id,
            "apply",
            *_binding(projection, run_id),
            expected_checksum="a" * 64,
            confirm_writes=True,
        )

    stored = projection.lookup_run(run_id).value
    assert stored is not None
    assert stored.phase == "apply-failed"
    assert stored.outcome == "failed"
    assert stored.summary["may_have_partially_written"] is True
    assert stored.results["apply_failure"] == {
        "stage": "apply",
        "outcome": "failed",
        "error_type": "OperationApplyFailedError",
        **partial.as_summary_keys(),
    }
    assert stored.prefect_executions[0].terminal_state == "failed"


@pytest.mark.usefixtures("_claimed_worker_execution")
def test_service_verify_failure_merges_evidence_and_terminalizes_exact_link(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Read-only verify failure retains the run lifecycle and closes its execution."""
    run_id = "run-service-verify-failure"
    projection = _create_product_run(tmp_path.resolve(), run_id, operation="verify")
    monkeypatch.setattr(service_flow, "_runtime", lambda: (str(tmp_path), projection))
    bind_granting_guard(monkeypatch, service_flow)
    monkeypatch.setattr(service_flow, "_run_logger", lambda: (logging.getLogger("test-service"), False))
    monkeypatch.setattr(service_flow, "resolve_runtime_instance", _instance)
    monkeypatch.setattr(service_flow, "collect_secret_values", lambda _instance=None: ())

    def fail_verify(*_args: object, **_kwargs: object) -> NoReturn:
        msg = "saved plan verification failed"
        raise ValueError(msg)

    monkeypatch.setattr(service_flow, "execute_run", fail_verify)

    with pytest.raises(RuntimeError):
        service_sync_run.fn(run_id, "verify", *_binding(projection, run_id))

    stored = projection.lookup_run(run_id).value
    assert stored is not None
    assert (stored.phase, stored.outcome, stored.finished_at) == ("accepted", None, None)
    assert stored.results["verify_failure"]["error_type"] == "ValueError"
    assert stored.prefect_executions[0].terminal_state == "failed"


@pytest.mark.usefixtures("_claimed_worker_execution")
def test_success_writeback_persistence_failure_is_not_recorded_as_business_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A failed success commit stays nonterminal for conservative reconciliation."""
    run_id = "run-service-success-persistence-failure"
    projection = _create_product_run(tmp_path.resolve(), run_id)
    saved = _saved(run_id)
    monkeypatch.setattr(service_flow, "_runtime", lambda: (str(tmp_path), projection))
    bind_granting_guard(monkeypatch, service_flow)
    monkeypatch.setattr(service_flow, "_run_logger", lambda: (logging.getLogger("test-service"), False))
    monkeypatch.setattr(service_flow, "resolve_runtime_instance", _instance)
    monkeypatch.setattr(service_flow, "collect_secret_values", lambda _instance=None: ())
    monkeypatch.setattr(service_flow, "_verify_registered_apply", lambda **_kwargs: None)
    monkeypatch.setattr(service_flow, "_require_planned_schema", lambda **_kwargs: None)
    monkeypatch.setattr(service_flow, "_plan", lambda *_args, **_kwargs: saved)
    commits: list[str] = []

    def fail_commit(*_args: object, **kwargs: object) -> bool:
        terminal_state = kwargs["terminal_state"]
        assert isinstance(terminal_state, str)
        commits.append(terminal_state)
        message = "injected persistence failure"
        raise OSError(message)

    monkeypatch.setattr(projection, "commit_claimed_execution", fail_commit)

    with pytest.raises(RuntimeError, match="injected persistence failure"):
        service_sync_run.fn(run_id, "plan", *_binding(projection, run_id))

    stored = projection.lookup_run(run_id).value
    assert stored is not None
    assert commits == ["completed"]
    assert (stored.phase, stored.outcome, stored.finished_at) == ("accepted", None, None)
    assert stored.prefect_executions[0].terminal_at is None


@pytest.mark.usefixtures("_claimed_worker_execution")
def test_success_writeback_commit_error_preserves_reread_committed_result(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """An ambiguous commit response returns the known result when durable reread proves success."""
    run_id = "run-service-success-committed-before-error"
    projection = _create_product_run(tmp_path.resolve(), run_id)
    saved = _saved(run_id)
    monkeypatch.setattr(service_flow, "_runtime", lambda: (str(tmp_path), projection))
    bind_granting_guard(monkeypatch, service_flow)
    monkeypatch.setattr(service_flow, "_run_logger", lambda: (logging.getLogger("test-service"), False))
    monkeypatch.setattr(service_flow, "resolve_runtime_instance", _instance)
    monkeypatch.setattr(service_flow, "collect_secret_values", lambda _instance=None: ())
    monkeypatch.setattr(service_flow, "_verify_registered_apply", lambda **_kwargs: None)
    monkeypatch.setattr(service_flow, "_require_planned_schema", lambda **_kwargs: None)
    monkeypatch.setattr(service_flow, "_plan", lambda *_args, **_kwargs: saved)
    commit = projection.commit_claimed_execution

    def commit_then_fail(*args: object, **kwargs: object) -> bool:
        assert commit(*args, **kwargs)
        message = "injected post-commit connection failure"
        raise OSError(message)

    monkeypatch.setattr(projection, "commit_claimed_execution", commit_then_fail)

    result = service_sync_run.fn(run_id, "plan", *_binding(projection, run_id))

    stored = projection.lookup_run(run_id).value
    assert stored is not None
    assert result["outcome"] == "no-change"
    assert (stored.phase, stored.outcome) == ("planned", "no-change")
    assert stored.prefect_executions[0].terminal_state == "completed"


@pytest.mark.usefixtures("_claimed_worker_execution")
def test_service_confirmed_sync_retains_the_semantic_sync_operation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    run_id = "run-service-semantic-sync"
    projection = _create_product_run(tmp_path.resolve(), run_id, operation="sync")
    saved = _saved(run_id)
    monkeypatch.setattr(service_flow, "_runtime", lambda: (str(tmp_path), projection))
    bind_granting_guard(monkeypatch, service_flow)
    monkeypatch.setattr(service_flow, "_run_logger", lambda: (logging.getLogger("test-service"), False))
    monkeypatch.setattr(service_flow, "resolve_runtime_instance", _instance)
    monkeypatch.setattr(service_flow, "collect_secret_values", lambda _instance=None: ())
    monkeypatch.setattr(service_flow, "_verify_registered_apply", lambda **_kwargs: None)
    monkeypatch.setattr(service_flow, "_require_planned_schema", lambda **_kwargs: None)

    def core(_instance: object, *, operation: str, **_kwargs: object) -> SavedPlan | RunResult:
        if operation in {"plan", "verify"}:
            return saved
        return RunResult(
            sync_name="inventory",
            operation="apply",
            run_id=run_id,
            status="no-change",
            changed=False,
            summary={"create": 0, "update": 0, "delete": 0},
            artifact_path=str(tmp_path / run_id),
        )

    monkeypatch.setattr(service_flow, "execute_run", core)

    result = service_sync_run.fn(
        run_id,
        "sync",
        *_binding(projection, run_id),
        confirm_writes=True,
    )

    assert result["operation"] == "sync"
    stored = projection.lookup_run(run_id).value
    assert stored is not None
    assert stored.operation == "sync"
    assert stored.results["operation"] == "sync"


@pytest.mark.usefixtures("_claimed_worker_execution")
def test_service_plan_worker_updates_the_api_created_run_and_publishes_review(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    run_id = "run-service-plan"
    projection = _create_product_run(tmp_path.resolve(), run_id)
    saved = _saved(run_id)
    seen: list[str] = []
    monkeypatch.setattr(service_flow, "_runtime", lambda: (str(tmp_path), projection))
    bind_granting_guard(monkeypatch, service_flow)
    monkeypatch.setattr(service_flow, "_run_logger", lambda: (logging.getLogger("test-service"), False))
    monkeypatch.setattr(service_flow, "resolve_runtime_instance", _instance)
    monkeypatch.setattr(service_flow, "collect_secret_values", lambda _instance=None: ())
    monkeypatch.setattr(service_flow, "_verify_registered_apply", lambda **_kwargs: None)
    monkeypatch.setattr(service_flow, "_require_planned_schema", lambda **_kwargs: None)

    def plan(_instance, *, run_id: str, branch: str | None, composed_sync: bool, **_kwargs: object):  # noqa: ARG001
        seen.append(run_id)
        assert composed_sync is False
        return saved

    monkeypatch.setattr(service_flow, "_plan", plan)

    result = service_sync_run.fn(
        run_id,
        "plan",
        *_binding(projection, run_id),
    )

    assert seen == [run_id]
    assert result["run_id"] == run_id
    stored = projection.lookup_run(run_id).value
    assert stored is not None
    assert stored.phase == "planned"
    assert [artifact.artifact_id for artifact in stored.artifact_refs] == ["plan-review"]
    assert stored.prefect_executions[0].terminal_state == "completed"
    assert stored.prefect_executions[0].terminal_outcome == "succeeded"


@pytest.mark.usefixtures("_claimed_worker_execution")
def test_service_verify_is_read_only_for_product_lifecycle(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    run_id = "run-service-verify"
    projection = _create_product_run(tmp_path.resolve(), run_id)
    before = projection.lookup_run(run_id).value
    saved = _saved(run_id)
    monkeypatch.setattr(service_flow, "_runtime", lambda: (str(tmp_path), projection))
    bind_granting_guard(monkeypatch, service_flow)
    monkeypatch.setattr(service_flow, "_run_logger", lambda: (logging.getLogger("test-service"), False))
    monkeypatch.setattr(service_flow, "resolve_runtime_instance", _instance)
    monkeypatch.setattr(service_flow, "collect_secret_values", lambda _instance=None: ())
    monkeypatch.setattr(service_flow, "execute_run", lambda *_args, **_kwargs: saved)

    result = service_sync_run.fn(
        run_id,
        "verify",
        *_binding(projection, run_id),
    )

    assert result == {
        "run_id": run_id,
        "stage": "verify",
        "outcome": "verified",
        "checksum": "a" * 64,
        "checksum_ok": True,
        "verification_notes": [],
    }
    after = projection.lookup_run(run_id).value
    assert after is not None
    assert before is not None
    assert after.model_dump(exclude={"results", "prefect_executions"}) == before.model_dump(
        exclude={"results", "prefect_executions"}
    )
    assert after.results == {"verification": result}
    assert after.prefect_executions[0].terminal_state == "completed"


@pytest.mark.usefixtures("_claimed_worker_execution")
def test_confirmed_service_sync_calls_plan_verify_apply_in_order_on_one_run(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    run_id = "run-service-sync"
    projection = _create_product_run(tmp_path.resolve(), run_id, operation="sync")
    saved = _saved(run_id)
    calls: list[tuple[str, str]] = []
    monkeypatch.setattr(service_flow, "_runtime", lambda: (str(tmp_path), projection))
    bind_granting_guard(monkeypatch, service_flow)
    monkeypatch.setattr(service_flow, "_run_logger", lambda: (logging.getLogger("test-service"), False))
    monkeypatch.setattr(service_flow, "resolve_runtime_instance", _instance)
    monkeypatch.setattr(service_flow, "collect_secret_values", lambda _instance=None: ())
    monkeypatch.setattr(service_flow, "_verify_registered_apply", lambda **_kwargs: None)
    monkeypatch.setattr(service_flow, "_require_planned_schema", lambda **_kwargs: None)

    def plan(_instance, *, run_id: str, branch: str | None, composed_sync: bool, **_kwargs: object):  # noqa: ARG001
        calls.append(("plan", run_id))
        assert composed_sync is True
        return saved

    def execute(_instance, *, operation: str, run_id: str, **kwargs: object) -> SavedPlan | RunResult:
        calls.append((operation, run_id))
        if operation == "verify":
            return saved
        assert operation == "apply"
        assert kwargs["expected_checksum"] == saved.manifest.plan_checksum
        assert kwargs["confirm_writes"] is True
        return RunResult(
            sync_name="inventory",
            operation="apply",
            run_id=run_id,
            status="no-change",
            changed=False,
            summary={"create": 0, "update": 0, "delete": 0},
            artifact_path=str(tmp_path / run_id),
        )

    monkeypatch.setattr(service_flow, "_plan", plan)
    monkeypatch.setattr(service_flow, "execute_run", execute)

    service_sync_run.fn(
        run_id,
        "sync",
        *_binding(projection, run_id),
        confirm_writes=True,
    )

    assert calls == [("plan", run_id), ("verify", run_id), ("apply", run_id)]
    assert projection.lookup_run(run_id).value is not None
    assert projection.lookup_run(run_id).value.phase == "applied"  # type: ignore[union-attr]


class _RemoteFlowRun:
    def __init__(self, run_id: UUID) -> None:
        self.id = run_id
        self.state = Pending()


class _RemoteClient:
    def __init__(self) -> None:
        self.deployment_id = uuid4()
        self.flow_run = _RemoteFlowRun(uuid4())
        self.keys: list[str | None] = []
        self.parameters: list[dict[str, Any]] = []

    async def read_deployment_by_name(self, name: str):
        assert name == SERVICE_DEFINITION.key
        return SimpleNamespace(id=self.deployment_id)

    async def create_flow_run_from_deployment(
        self,
        deployment_id: UUID,
        *,
        parameters: dict[str, Any],
        idempotency_key: str | None = None,
    ):
        assert deployment_id == self.deployment_id
        self.keys.append(idempotency_key)
        self.parameters.append(parameters)
        return self.flow_run

    async def read_flow_run(self, flow_run_id: UUID):
        assert flow_run_id == self.flow_run.id
        return self.flow_run

    async def set_flow_run_state(self, flow_run_id: UUID, state: State[object]) -> OrchestrationResult[object]:
        assert flow_run_id == self.flow_run.id
        self.flow_run.state = state
        return OrchestrationResult(
            state=state,
            status=SetStateStatus.ACCEPT,
            details=StateAcceptDetails(),
        )


@pytest.mark.asyncio
async def test_prefect_extras_executor_receives_opaque_key_unchanged() -> None:
    client = _RemoteClient()
    gateway = PrefectOrchestration(client)  # type: ignore[arg-type] - offline fake implements the protocol.
    parameters: dict[str, object] = {
        "run_id": "run-001",
        "stage": "plan",
        "config_id": "config-001",
        "registry_version": 1,
        "package_checksum": "a" * 64,
        "branch": None,
        "expected_checksum": None,
        "confirm_writes": False,
    }

    submission = await gateway.submit(parameters, idempotency_key="opaque-prefect-key")

    assert submission.flow_run_id == str(client.flow_run.id)
    assert client.keys == ["opaque-prefect-key"]
    assert client.parameters == [parameters]


class _ReadTransportFailureClient:
    async def read_flow_run(self, _flow_run_id: UUID):  # noqa: PLR6301 - protocol fake.
        request = httpx.Request("GET", "http://prefect.invalid/api/flow_runs/id")
        message = "Prefect is unavailable"
        raise httpx.ConnectError(message, request=request)


@pytest.mark.asyncio
async def test_prefect_read_transport_failure_becomes_missing_live_detail() -> None:
    gateway = PrefectOrchestration(
        _ReadTransportFailureClient()  # ty: ignore[invalid-argument-type] - read-only protocol fake.
    )

    observed = await gateway.observe(str(uuid4()))
    cancellation_client = _RemoteClient()
    cancelled = await PrefectOrchestration(cancellation_client).cancel(str(cancellation_client.flow_run.id))

    assert observed == Observation(
        available=False,
        state=None,
        reason="prefect-read-unavailable",
    )
    assert cancelled == CancellationResult(acknowledged=True)


class _CancellationClient:
    def __init__(self, result: object) -> None:
        self.flow_run_id = uuid4()
        self.result = result

    async def set_flow_run_state(self, flow_run_id: UUID, state: State[object]) -> object:
        assert flow_run_id == self.flow_run_id
        assert state.type is StateType.CANCELLING
        return self.result


_ACKNOWLEDGED_CANCELLATION = CancellationResult(acknowledged=True)
_UNACKNOWLEDGED_CANCELLATION = CancellationResult(
    acknowledged=False,
    reason="prefect-cancellation-unavailable",
)


@pytest.mark.parametrize(
    ("result", "expected"),
    [
        (
            OrchestrationResult(state=Cancelling(), status=SetStateStatus.ACCEPT, details=StateAcceptDetails()),
            _ACKNOWLEDGED_CANCELLATION,
        ),
        (
            OrchestrationResult(
                state=Cancelling(name="Cancellation Requested"),
                status=SetStateStatus.ACCEPT,
                details=StateAcceptDetails(),
            ),
            _UNACKNOWLEDGED_CANCELLATION,
        ),
        (
            OrchestrationResult(state=Cancelling(), status=SetStateStatus.REJECT, details=StateRejectDetails()),
            _UNACKNOWLEDGED_CANCELLATION,
        ),
        (
            OrchestrationResult(state=Cancelled(), status=SetStateStatus.REJECT, details=StateRejectDetails()),
            _UNACKNOWLEDGED_CANCELLATION,
        ),
        (
            OrchestrationResult(state=Running(), status=SetStateStatus.ACCEPT, details=StateAcceptDetails()),
            _UNACKNOWLEDGED_CANCELLATION,
        ),
        (
            OrchestrationResult(state=None, status=SetStateStatus.ACCEPT, details=StateAcceptDetails()),
            _UNACKNOWLEDGED_CANCELLATION,
        ),
        (
            OrchestrationResult(state=Running(), status=SetStateStatus.REJECT, details=StateRejectDetails()),
            _UNACKNOWLEDGED_CANCELLATION,
        ),
        (
            OrchestrationResult(state=None, status=SetStateStatus.REJECT, details=StateRejectDetails()),
            _UNACKNOWLEDGED_CANCELLATION,
        ),
        (
            OrchestrationResult(state=Cancelling(), status=SetStateStatus.ABORT, details=StateAbortDetails()),
            _UNACKNOWLEDGED_CANCELLATION,
        ),
        (
            OrchestrationResult(
                state=Cancelling(),
                status=SetStateStatus.WAIT,
                details=StateWaitDetails(delay_seconds=1),
            ),
            _UNACKNOWLEDGED_CANCELLATION,
        ),
        (SimpleNamespace(status=SetStateStatus.ACCEPT, state=Cancelling()), _UNACKNOWLEDGED_CANCELLATION),
        (
            OrchestrationResult.model_construct(
                state=Cancelling(),
                status="ACCEPT",
                details=StateAcceptDetails(),
            ),
            _UNACKNOWLEDGED_CANCELLATION,
        ),
        (
            OrchestrationResult.model_construct(
                state=Cancelling(),
                status=SetStateStatus.ACCEPT,
                details=StateRejectDetails(),
            ),
            _UNACKNOWLEDGED_CANCELLATION,
        ),
        (
            OrchestrationResult.model_construct(
                state=Cancelling(),
                status=SetStateStatus.REJECT,
                details=StateAcceptDetails(),
            ),
            _UNACKNOWLEDGED_CANCELLATION,
        ),
    ],
    ids=(
        "accept",
        "accept-custom-name",
        "reject-cancelling",
        "reject-cancelled",
        "accept-running",
        "accept-without-state",
        "reject-running",
        "reject-without-state",
        "abort",
        "wait",
        "non-orchestration-result",
        "malformed-status",
        "accept-mismatched-details",
        "reject-mismatched-details",
    ),
)
@pytest.mark.asyncio
async def test_prefect_cancel_accepts_only_the_documented_acknowledgement(
    result: object,
    expected: CancellationResult,
) -> None:
    client = _CancellationClient(result)

    cancellation = await PrefectOrchestration(client).cancel(str(client.flow_run_id))  # ty: ignore[invalid-argument-type]

    assert cancellation == expected


class _DeploymentClient:
    def __init__(self) -> None:
        self.created: list[dict[str, Any]] = []

    async def read_deployment_by_name(self, name: str) -> NoReturn:  # noqa: ARG002, PLR6301 - protocol fake.
        raise ObjectNotFound(RuntimeError("missing deployment"))

    async def create_flow_from_name(self, flow_name: str) -> UUID:  # noqa: ARG002, PLR6301 - protocol fake.
        return uuid4()

    async def create_deployment(self, flow_id: UUID, **payload: Any) -> UUID:  # noqa: ANN401, ARG002
        self.created.append(payload)
        return uuid4()

    async def update_deployment(  # noqa: PLR6301 - protocol fake.
        self,
        deployment_id: UUID,  # noqa: ARG002 - protocol signature.
        deployment: DeploymentUpdate,  # noqa: ARG002 - protocol signature.
    ) -> None:
        msg = "a missing deployment must be created, not updated"
        raise AssertionError(msg)


@pytest.mark.asyncio
async def test_prefect_extras_deployment_converges_the_service_catalogue_offline() -> None:
    client = _DeploymentClient()

    report = await apply_deployments(CATALOGUE, work_pool_name="service-pool", client=client)

    assert report.is_successful
    assert [result.status for result in report.results] == ["created"]
    assert client.created[0]["work_pool_name"] == "service-pool"
