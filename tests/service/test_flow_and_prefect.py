from __future__ import annotations

import inspect
import logging
from datetime import datetime, timezone
from hashlib import sha256
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

from infrahub_sdk.exceptions import GraphQLError as SDKGraphQLError
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
from infrahub_sync.plan.errors import (
    OperationApplyFailedError,
    PeerNotFoundError,
    PlanSchemaChangedError,
    StaleDestinationIdError,
    UnaccountedIdentityComponentError,
    UnkeyedCreateRefusedError,
)
from infrahub_sync.plan.models import ApplyRecord, PlanManifest
from infrahub_sync.plan.review import SavedPlan
from infrahub_sync.product_store import (
    ExecutionFinishWriteback,
    ExecutionMergeWriteback,
    MutationReceipt,
    PrefectExecutionLink,
    ProductRun,
    local_product_projection,
)
from infrahub_sync.service import flow as service_flow
from infrahub_sync.service.deploy import CATALOGUE
from infrahub_sync.service.failure_cause import RESUMABLE_WRITE_NEXT_ACTION
from infrahub_sync.service.flow import service_sync_run
from infrahub_sync.service.orchestration import (
    SERVICE_DEFINITION,
    CancellationResult,
    Observation,
    PrefectOrchestration,
)
from tests.configuration.validation_packages import package
from tests.logging_registry_fixtures import logging_registry_snapshot  # noqa: F401 - fixture, used by name
from tests.service.execution_fixtures import append_execution, bind_granting_guard

if TYPE_CHECKING:
    from prefect.client.schemas.actions import DeploymentUpdate

    from infrahub_sync.configuration import ConfigurationPackage

# What a faked `_verify_registered_apply` returns: the stage reads only the plan's operation
# count before the faked apply (spec 022, the apply progress observer's `total`).
_UNCHECKED_MANIFEST = SimpleNamespace(operations_count=1)


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


@pytest.fixture(autouse=True)
def _stub_checkpoints(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep lifecycle tests focused on flow behavior, not checkpoint membership.

    The internal handoff and its ordering are asserted in
    ``tests/service/test_plan_checkpoint.py`` against real bundles.
    """
    monkeypatch.setattr(service_flow, "publish_plan_checkpoint", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(service_flow, "rehydrate_plan_checkpoint", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(service_flow, "publish_final_checkpoint", lambda *_args, **_kwargs: None, raising=False)


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
        "configuration_name",
        "confirm_left_out",
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


@pytest.mark.usefixtures("logging_registry_snapshot")
@pytest.mark.parametrize("raise_exceptions", [True, False])
@pytest.mark.parametrize("failure_mode", ["formatting", "forwarding"])
def test_service_worker_log_bridge_never_leaks_a_secret_to_stderr_on_a_failed_record(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    failure_mode: str,
    raise_exceptions: bool,  # noqa: FBT001
) -> None:
    """Same acceptance criterion as the direct flow bridge, exercised via the service worker's own import path.

    `service_flow.RunLoggerBridge` is the same class the direct flow uses, but the
    service worker's `_remote_log_bridge` constructs and owns it independently, so
    the failure path is proven through this module's usage too rather than assumed
    from the direct flow's coverage.
    """
    canary = "service-worker-preflight-secret-canary"
    source_logger = logging.getLogger(service_flow.SOURCE_LOGGER_NAME)
    monkeypatch.setattr(source_logger, "propagate", False)
    monkeypatch.setattr(logging, "raiseExceptions", raise_exceptions)
    # A prior test elsewhere in the suite may have permanently attached the plain
    # CLI `StreamHandler` to this same process-global logger (`cli._setup_logging`
    # never removes it). That handler's own unrelated `handleError` leak is the
    # ticket's explicit exclusion, so it is cleared here to isolate this bridge.
    original_handlers = list(source_logger.handlers)
    run_logger = _RecordingRunLogger()
    if failure_mode == "forwarding":

        def _raise_on_log(*_args: object, **_kwargs: object) -> None:
            failure_message = "run logger rejected the record"
            raise RuntimeError(failure_message)

        monkeypatch.setattr(run_logger, "log", _raise_on_log)
    child_logger_name = f"{service_flow.SOURCE_LOGGER_NAME}.secret-leak-test"
    # `logging_registry_snapshot` restores whatever this name (and any
    # descendant's `.parent` reference) held in the process-wide registry
    # before the test ran, whether that was a `Logger`, a `PlaceHolder`, or
    # nothing.
    child_logger = logging.getLogger(child_logger_name)

    try:
        source_logger.handlers = []
        with service_flow._remote_log_bridge(run_logger, prefect_context=True, secrets=(canary,)):
            if failure_mode == "formatting":
                child_logger.warning(  # noqa: PLE1205
                    "secret in an over-supplied argument: %s",
                    canary,
                    "one argument too many",
                )
            else:
                child_logger.warning("secret in the message: %s", canary)
    finally:
        source_logger.handlers = original_handlers

    stderr = capsys.readouterr().err
    assert canary not in stderr
    assert stderr.count("could not be forwarded") == 1


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
    recorded = stored.results["plan_failure"]
    assert {key: recorded[key] for key in ("stage", "outcome", "error_type", "cause_type")} == {
        "stage": "plan",
        "outcome": "failed",
        "error_type": "ValueError",
        # The class this stage's failure was raised from. A name, never a message:
        # the canary assertions above and below cover the whole stored document.
        "cause_type": "ConnectionError",
    }
    # Spec 019 ADR 0021: the failure type and a fixed template cause, never the error's text.
    assert recorded["failure_type"] == "system_unreachable"
    assert recorded["system"] is None
    assert recorded["cause"] == "A system could not be reached"
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
    monkeypatch.setattr(service_flow, "_verify_registered_apply", lambda **_kwargs: _UNCHECKED_MANIFEST)
    monkeypatch.setattr(service_flow, "_require_planned_schema", lambda **_kwargs: None)

    failure = OperationApplyFailedError("destination rejected operation", apply_record=partial)

    def fail_apply(*_args: object, **_kwargs: object) -> NoReturn:
        raise failure

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
    # The record names a failing operation whose reach is unknown, so the run says the same
    # thing the record does. It used to say `failed` while carrying
    # `may_have_partially_written: True` — a verdict its own evidence contradicted (S6).
    assert stored.phase == "apply-interrupted"
    assert stored.outcome == "ambiguous"
    assert stored.summary["may_have_partially_written"] is True
    assert stored.results["apply_failure"] == {
        "stage": "apply",
        "outcome": "ambiguous",
        "error_type": "OperationApplyFailedError",
        "cause_type": None,
        # A fixed next action, never the destination's message. The run is interrupted and keeps
        # its saved plan, so the worker names `verify` and `runs resume` instead of the class's
        # own advice, a new plan (spec 022).
        "next_action": RESUMABLE_WRITE_NEXT_ACTION,
        **partial.as_summary_keys(),
        # Spec 019 ADR 0021: an apply failure with no known cause is an internal error at the
        # destination, with no cause text.
        "failure_type": "internal_error",
        "system": {"role": "destination", "adapter": "infrahub"},
        "error_code": None,
        "node_kind": None,
        "attribute": None,
        "infrahub_message": None,
        "record": None,
        "field": None,
        # The execution that wrote the record (spec 022, research R11).
        "flow_run_id": stored.prefect_executions[0].flow_run_id,
    }
    assert stored.prefect_executions[0].terminal_state == "interrupted"


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
    monkeypatch.setattr(service_flow, "_verify_registered_apply", lambda **_kwargs: _UNCHECKED_MANIFEST)
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
    monkeypatch.setattr(service_flow, "_verify_registered_apply", lambda **_kwargs: _UNCHECKED_MANIFEST)
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
    assert isinstance(result, dict)
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
    monkeypatch.setattr(service_flow, "_verify_registered_apply", lambda **_kwargs: _UNCHECKED_MANIFEST)
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

    assert isinstance(result, dict)
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
    monkeypatch.setattr(service_flow, "_verify_registered_apply", lambda **_kwargs: _UNCHECKED_MANIFEST)
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
    assert isinstance(result, dict)
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
    # The destination checks are `tests/service/test_verify_destination.py`'s subject; here
    # they pass, so this case is about what a passing verify leaves on the run.
    monkeypatch.setattr(service_flow, "_check_destination", lambda *_args, **_kwargs: ("checkpoint", "lineage"))

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
        "checks": ["checkpoint", "lineage"],
        "destination_checked": True,
    }
    after = projection.lookup_run(run_id).value
    assert after is not None
    assert before is not None
    # The verify result's event (spec 017, FR-012a) is evidence, not a lifecycle change.
    assert after.model_dump(exclude={"results", "prefect_executions", "audit_links"}) == before.model_dump(
        exclude={"results", "prefect_executions", "audit_links"}
    )
    assert after.results == {"verification": result}
    assert after.prefect_executions[0].terminal_state == "completed"
    assert [event.event_type for event in projection.audit_events(run_id)] == ["infrahub_sync.run.verified"]


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
    monkeypatch.setattr(service_flow, "_verify_registered_apply", lambda **_kwargs: _UNCHECKED_MANIFEST)
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
        self.tags = list(SERVICE_DEFINITION.tags)


class _RemoteClient:
    def __init__(self) -> None:
        self.deployment_id = uuid4()
        self.flow_run = _RemoteFlowRun(uuid4())
        self.keys: list[str | None] = []
        self.parameters: list[dict[str, Any]] = []
        self.names: list[str | None] = []

    async def read_deployment_by_name(self, name: str):
        assert name == SERVICE_DEFINITION.key
        return SimpleNamespace(id=self.deployment_id)

    async def create_flow_run_from_deployment(
        self,
        deployment_id: UUID,
        *,
        parameters: dict[str, Any],
        idempotency_key: str | None = None,
        name: str | None = None,
        tags: list[str] | None = None,
    ):
        assert deployment_id == self.deployment_id
        self.keys.append(idempotency_key)
        self.parameters.append(parameters)
        self.names.append(name)
        # Prefect gives the run the union of the deployment's tags and the requested ones.
        self.flow_run.tags = sorted({*SERVICE_DEFINITION.tags, *(tags or [])})
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
        "configuration_name": "netbox-to-infrahub",
    }

    submission = await gateway.submit(parameters, idempotency_key="opaque-prefect-key")

    assert submission.flow_run_id == str(client.flow_run.id)
    assert client.keys == ["opaque-prefect-key"]
    # The deployment fills the defaults of the parameters the request leaves out.
    assert client.parameters == [{**parameters, "confirm_left_out": False}]
    assert client.names == ["Plan sync of netbox-to-infrahub"]
    assert client.flow_run.tags == sorted(
        {
            *SERVICE_DEFINITION.tags,
            "run:run-001",
            "stage:plan",
            "config:config-001",
            "config-version:config-001@1",
        }
    )


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


# Everything the apply path reports as one `OperationApplyFailedError`. The
# wrapper is the same for all of them, so a gate whose subject is one refusal has
# only the recorded cause to read.
WRAPPED_APPLY_FAILURES = (
    UnkeyedCreateRefusedError("a create carries no usable convergence key"),
    PeerNotFoundError("a peer identity matched nothing at the destination"),
    UnaccountedIdentityComponentError("an identity component is unaccounted for"),
    SDKGraphQLError(errors=[{"message": "the destination refused the mutation"}]),
)


@pytest.mark.usefixtures("_claimed_worker_execution")
@pytest.mark.parametrize("cause", WRAPPED_APPLY_FAILURES, ids=lambda cause: type(cause).__name__)
def test_a_wrapped_apply_failure_records_the_class_it_was_raised_from(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    cause: Exception,
) -> None:
    """One wrapper, and the recorded cause is what tells these apart.

    Without it a create that cannot be proven keyed, a peer that matched nothing
    and a destination's own rejection are one indistinguishable
    `OperationApplyFailedError` in the run record -- and a gate asserting that
    *this* refusal happened has nothing to
    assert against.
    """
    run_id = f"run-cause-{type(cause).__name__.lower()}"
    projection = _create_product_run(tmp_path.resolve(), run_id)
    partial = ApplyRecord(failed_operation="op-failed")
    monkeypatch.setattr(service_flow, "_runtime", lambda: (str(tmp_path), projection))
    bind_granting_guard(monkeypatch, service_flow)
    monkeypatch.setattr(service_flow, "_run_logger", lambda: (logging.getLogger("test-service"), False))
    monkeypatch.setattr(service_flow, "resolve_runtime_instance", _instance)
    monkeypatch.setattr(service_flow, "collect_secret_values", lambda _instance=None: ())
    monkeypatch.setattr(service_flow, "_verify_registered_apply", lambda **_kwargs: _UNCHECKED_MANIFEST)
    monkeypatch.setattr(service_flow, "_require_planned_schema", lambda **_kwargs: None)

    def fail_apply(*_args: object, **_kwargs: object) -> NoReturn:
        # Wrapped exactly as `potenda` wraps an operational apply failure.
        msg = "applying an operation failed"
        raise OperationApplyFailedError(msg, apply_record=partial) from cause

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
    evidence = stored.results["apply_failure"]

    assert evidence["error_type"] == "OperationApplyFailedError"
    assert evidence["cause_type"] == type(cause).__name__


@pytest.mark.usefixtures("_claimed_worker_execution")
def test_the_recorded_cause_holds_infrahubs_message_and_no_other_error_text(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """ADR 0021 replaces "a class name, never a message".

    Infrahub's own message of a rejected write is recorded, in `infrahub_message` only. Every
    other part of the error -- the query, its variables, the extra data of the GraphQL error and
    the wrapper's message -- can carry a value the destination was sent, and reaches nothing.
    """
    run_id = "run-cause-carries-no-message"
    projection = _create_product_run(tmp_path.resolve(), run_id)
    infrahub_message = "status planned is not one of the allowed values"
    canary = "kaphaeric-secret-in-a-destination-message"
    monkeypatch.setattr(service_flow, "_runtime", lambda: (str(tmp_path), projection))
    bind_granting_guard(monkeypatch, service_flow)
    monkeypatch.setattr(service_flow, "_run_logger", lambda: (logging.getLogger("test-service"), False))
    monkeypatch.setattr(service_flow, "resolve_runtime_instance", _instance)
    monkeypatch.setattr(service_flow, "collect_secret_values", lambda _instance=None: ())
    monkeypatch.setattr(service_flow, "_verify_registered_apply", lambda **_kwargs: _UNCHECKED_MANIFEST)
    monkeypatch.setattr(service_flow, "_require_planned_schema", lambda **_kwargs: None)

    def fail_apply(*_args: object, **_kwargs: object) -> NoReturn:
        msg = f"applying an operation failed: {canary}"
        cause = SDKGraphQLError(
            errors=[{"message": infrahub_message, "extensions": {"data": {"detail": canary}}}],
            query=f"mutation {{ x(data: {{token: {canary!r}}}) }}",
            variables={"token": canary},
        )
        raise OperationApplyFailedError(msg, apply_record=ApplyRecord(failed_operation="op-failed")) from cause

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

    recorded = stored.results["apply_failure"]
    assert recorded["cause_type"] == "GraphQLError"
    assert recorded["failure_type"] == "destination_rejected_write"
    assert recorded["infrahub_message"] == infrahub_message
    assert canary not in stored.model_dump_json()


# ---------------------------------------------------------------------------------------
# S6 — a refusal proven not to have written is not an ambiguous run
# ---------------------------------------------------------------------------------------


def _apply_failure_outcome(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    run_id: str,
    record: ApplyRecord,
    cause: Exception,
) -> tuple[dict[str, Any], Any]:
    """Drive one wrapped apply failure through the service and return its evidence and run."""
    projection = _create_product_run(tmp_path.resolve(), run_id)
    monkeypatch.setattr(service_flow, "_runtime", lambda: (str(tmp_path), projection))
    bind_granting_guard(monkeypatch, service_flow)
    monkeypatch.setattr(service_flow, "_run_logger", lambda: (logging.getLogger("test-service"), False))
    monkeypatch.setattr(service_flow, "resolve_runtime_instance", _instance)
    monkeypatch.setattr(service_flow, "collect_secret_values", lambda _instance=None: ())
    monkeypatch.setattr(service_flow, "_verify_registered_apply", lambda **_kwargs: _UNCHECKED_MANIFEST)
    monkeypatch.setattr(service_flow, "_require_planned_schema", lambda **_kwargs: None)

    def fail_apply(*_args: object, **_kwargs: object) -> NoReturn:
        msg = "applying an operation failed"
        raise OperationApplyFailedError(msg, apply_record=record) from cause

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
    return stored.results["apply_failure"], stored


@pytest.mark.usefixtures("_claimed_worker_execution")
def test_a_refusal_proven_not_to_have_written_is_failed_and_needs_no_reconciliation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """S6: sending an operator to reconcile a destination that was never touched is a defect."""
    evidence, stored = _apply_failure_outcome(
        monkeypatch,
        tmp_path,
        run_id="run-s6-not-written",
        record=ApplyRecord(failed_operation="op-failed", failed_operation_wrote=False),
        cause=StaleDestinationIdError("the recorded id matches no object"),
    )

    assert evidence["outcome"] == "failed"
    assert evidence["failed_operation_wrote"] is False
    assert evidence["may_have_partially_written"] is False
    # `reconciliation_required` derives from exactly this verdict, so the phase is what
    # decides it; the derivation itself is asserted in the product-store suite.
    assert stored.phase == "apply-failed"


@pytest.mark.usefixtures("_claimed_worker_execution")
def test_a_failure_whose_reach_is_unknown_stays_ambiguous(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A transport failure can commit remotely before it fails, so it is still ambiguous."""
    evidence, stored = _apply_failure_outcome(
        monkeypatch,
        tmp_path,
        run_id="run-s6-unknown",
        record=ApplyRecord(failed_operation="op-failed"),
        cause=SDKGraphQLError(errors=[{"message": "the destination refused the mutation"}]),
    )

    assert evidence["outcome"] == "ambiguous"
    assert evidence["failed_operation_wrote"] is None
    assert evidence["may_have_partially_written"] is True
    assert stored.phase == "apply-interrupted"


@pytest.mark.usefixtures("_claimed_worker_execution")
def test_a_completed_record_is_left_to_the_dispatch_rule(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A post-write failure carries a **completed** record, which names no failing operation.

    The sharper per-operation reading therefore does not apply to it, and the
    `dispatch_started` rule decides it as before — which is why that rule is kept rather than
    replaced. Nothing dispatched in this harness, so the rule answers `failed` here; in a real
    apply that reached its write it answers interrupted/ambiguous.
    """
    evidence, stored = _apply_failure_outcome(
        monkeypatch,
        tmp_path,
        run_id="run-s6-post-write",
        record=ApplyRecord(applied_operations=("op-a", "op-b")),
        cause=SDKGraphQLError(errors=[{"message": "the sidecar transition failed"}]),
    )

    assert evidence["failed_operation"] is None, "A completed record names no failing operation."
    assert evidence["may_have_partially_written"] is False
    assert stored.phase == "apply-failed"


# ---------------------------------------------------------------------------------------
# The operator cause of a plan or apply failure is recorded
# ---------------------------------------------------------------------------------------

LINEAGE_TOKEN = "lineage-token-canary-5c0a9e"  # noqa: S105 -- a canary, not a credential
LINEAGE_PERMISSION = "object:*:Core:AccountGroup:view:allow_all"


def _sdk_403(permission: str = LINEAGE_PERMISSION) -> Exception:
    """The `AuthenticationError` the SDK raises for an HTTP 403, chained from a request carrying the token."""
    from infrahub_sdk import Config, InfrahubClientSync
    from infrahub_sdk.exceptions import AuthenticationError

    address = "http://infrahub.invalid"

    def requester(url: str, method: Any, headers: dict[str, Any], timeout: int, payload: dict | None = None) -> Any:  # noqa: ANN401
        del timeout, payload
        request = httpx.Request(method.value, url, headers=headers)
        body = {"errors": [{"message": f"You do not have the following permission: {permission}"}]}
        return httpx.Response(403, json=body, request=request)

    client = InfrahubClientSync(
        address=address, config=Config(address=address, api_token=LINEAGE_TOKEN, sync_requester=requester)
    )
    with pytest.raises(AuthenticationError) as caught:
        client.execute_graphql(query="query { AccountProfile { id } }")
    cause = caught.value.__cause__
    assert isinstance(cause, httpx.HTTPStatusError)
    assert LINEAGE_TOKEN in str(cause.request.headers), "the token check below is not vacuous"
    return caught.value


def _plan_load_failure(tmp_path: Path, *, resolve: Exception | None = None, load: Exception | None = None) -> Exception:
    """The exception `Potenda.load` raises for a plan whose lineage resolution or destination read fails."""
    from unittest.mock import MagicMock

    from infrahub_sync.potenda import Potenda

    source = MagicMock()
    source.top_level = []
    destination = MagicMock()
    destination.top_level = []
    if resolve is not None:
        # Assigned so the `runtime_checkable` protocol check finds `resolve_lineage` on Python
        # 3.12 and later.
        destination.resolve_lineage = MagicMock(side_effect=resolve)
    if load is not None:
        destination.load.side_effect = load
    engine = Potenda(
        source=source,
        destination=destination,
        config=None,  # ty: ignore[invalid-argument-type]
        top_level=[],
        run_dir=tmp_path / "engine",
        concurrent_load=False,
    )
    with pytest.raises(ValueError, match="An error occurred while loading the sync") as caught:
        engine.load()
    return caught.value


def _assert_ends_failed_with_its_cause(state: object, stored: ProductRun, stage: str) -> None:
    """A known failure returns a `Failed` state whose message is the recorded cause and next step (T026)."""
    assert isinstance(state, State)
    assert state.is_failed()
    evidence = stored.results[f"{stage}_failure"]
    next_step = evidence.get("next_action") or f"`infrahub-sync runs show {stored.run_id}`"
    assert state.message == f"{evidence['cause']} Next step: {next_step}"


def _record_plan_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, run_id: str, failure: Exception, *, known: bool = False
):
    """Run the plan stage so it fails with `failure`, and return the stored run.

    A `known` failure (one with a recorded cause) ends with a returned `Failed` state; any other
    raises its sanitized copy.
    """
    projection = _create_product_run(tmp_path.resolve(), run_id)
    monkeypatch.setattr(service_flow, "_runtime", lambda: (str(tmp_path), projection))
    bind_granting_guard(monkeypatch, service_flow)
    monkeypatch.setattr(service_flow, "_run_logger", lambda: (logging.getLogger("test-service"), False))
    monkeypatch.setattr(service_flow, "resolve_runtime_instance", _instance)
    monkeypatch.setattr(service_flow, "collect_secret_values", lambda _instance=None: ())

    def fail_plan(*_args: object, **_kwargs: object) -> NoReturn:
        raise failure

    monkeypatch.setattr(service_flow, "_plan", fail_plan)
    if known:
        state = service_sync_run.fn(run_id, "plan", *_binding(projection, run_id))
    else:
        with pytest.raises(RuntimeError):
            service_sync_run.fn(run_id, "plan", *_binding(projection, run_id))
    stored = projection.lookup_run(run_id).value
    assert stored is not None
    if known:
        _assert_ends_failed_with_its_cause(state, stored, "plan")
    return stored


def _record_apply_failure(  # noqa: PLR0913 - the stage inputs, its secrets and whether its failure is known
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    run_id: str,
    failure: Exception,
    *,
    secrets: tuple[str, ...] = (),
    known: bool = False,
):
    """Run the apply stage so the engine fails with `failure`, and return the stored run.

    A `known` failure (one with a recorded cause) ends with a returned `Failed` state; any other
    raises its sanitized copy.
    """
    projection = _create_product_run(tmp_path.resolve(), run_id)
    monkeypatch.setattr(service_flow, "_runtime", lambda: (str(tmp_path), projection))
    bind_granting_guard(monkeypatch, service_flow)
    monkeypatch.setattr(service_flow, "_run_logger", lambda: (logging.getLogger("test-service"), False))
    monkeypatch.setattr(service_flow, "resolve_runtime_instance", _instance)
    monkeypatch.setattr(service_flow, "collect_secret_values", lambda _instance=None: secrets)
    monkeypatch.setattr(service_flow, "_verify_registered_apply", lambda **_kwargs: _UNCHECKED_MANIFEST)
    monkeypatch.setattr(service_flow, "_require_planned_schema", lambda **_kwargs: None)

    def fail_apply(*_args: object, **_kwargs: object) -> NoReturn:
        raise failure

    monkeypatch.setattr(service_flow, "execute_run", fail_apply)
    binding = _binding(projection, run_id)
    if known:
        state = service_sync_run.fn(run_id, "apply", *binding, expected_checksum="a" * 64, confirm_writes=True)
    else:
        with pytest.raises(RuntimeError):
            service_sync_run.fn(run_id, "apply", *binding, expected_checksum="a" * 64, confirm_writes=True)
    stored = projection.lookup_run(run_id).value
    assert stored is not None
    if known:
        _assert_ends_failed_with_its_cause(state, stored, "apply")
    return stored


MISSING_PERMISSION_EVIDENCE = {
    "infrahub_error": {"problem": "missing_permission", "http_status": 403, "permissions": [LINEAGE_PERMISSION]},
    "cause": (
        f"a missing permission: the import account needs the permission `{LINEAGE_PERMISSION}` "
        "(AuthenticationError, HTTP 403)"
    ),
}


@pytest.mark.usefixtures("_claimed_worker_execution")
@pytest.mark.parametrize("where", ["destination_read", "lineage_resolution"])
def test_a_plan_records_a_403_as_a_missing_permission(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, where: str
) -> None:
    """The load wraps the 403 in `ValueError`s, and the run still records the permission."""
    sdk_error = _sdk_403()
    failure = (
        _plan_load_failure(tmp_path, load=sdk_error)
        if where == "destination_read"
        else _plan_load_failure(tmp_path, resolve=sdk_error)
    )

    stored = _record_plan_failure(monkeypatch, tmp_path, f"run-plan-403-{where}", failure, known=True)

    evidence = stored.results["plan_failure"]
    assert evidence["error_type"] == "ValueError"
    assert {key: evidence[key] for key in MISSING_PERMISSION_EVIDENCE} == MISSING_PERMISSION_EVIDENCE
    assert "refused the token" not in stored.model_dump_json()
    assert LINEAGE_TOKEN not in stored.model_dump_json()


@pytest.mark.usefixtures("_claimed_worker_execution")
def test_an_apply_records_a_403_during_lineage_resolution_as_a_missing_permission(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The lineage is resolved outside the operation loop, and its 403 is still classified."""
    stored = _record_apply_failure(monkeypatch, tmp_path, "run-apply-403-lineage", _sdk_403(), known=True)

    evidence = stored.results["apply_failure"]
    assert evidence["error_type"] == "AuthenticationError"
    assert {key: evidence[key] for key in MISSING_PERMISSION_EVIDENCE} == MISSING_PERMISSION_EVIDENCE
    assert LINEAGE_TOKEN not in stored.model_dump_json()


@pytest.mark.usefixtures("_claimed_worker_execution")
def test_a_plan_records_a_missing_named_node_with_its_cause_and_next_action(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The cause names the setting, the kind and the name, through the load's `ValueError` wrapping."""
    from infrahub_sync.plan.errors import NamedLineageNodeNotFoundError

    refusal = NamedLineageNodeNotFoundError(setting="owner", kind="CoreAccountGroup", name="network-team")
    failure = _plan_load_failure(tmp_path, resolve=refusal)

    stored = _record_plan_failure(monkeypatch, tmp_path, "run-plan-missing-owner", failure, known=True)

    evidence = stored.results["plan_failure"]
    assert evidence["cause"] == "The setting `owner` names `CoreAccountGroup` `network-team`, which does not exist."
    assert evidence["next_action"] == refusal.next_action
    assert "infrahub_error" not in evidence


@pytest.mark.usefixtures("_claimed_worker_execution")
def test_an_apply_records_an_account_mismatch_with_both_accounts_and_its_next_action(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The recorded cause names both accounts; control characters are removed and secrets masked."""
    from infrahub_sync.plan.errors import ImportAccountMismatchError

    message = (
        "The saved plan for run 'r' was made with the import account 'netbox' (id a-1), and this apply runs "
        f"with the import account 'other\x1b[2J' (id a-2). {LINEAGE_TOKEN}"
    )
    stored = _record_apply_failure(
        monkeypatch,
        tmp_path,
        "run-apply-mismatch",
        ImportAccountMismatchError(message),
        secrets=(LINEAGE_TOKEN,),
        known=True,
    )

    evidence = stored.results["apply_failure"]
    assert evidence["error_type"] == "ImportAccountMismatchError"
    assert evidence["cause"].startswith("The saved plan for run 'r' was made with the import account 'netbox' (id a-1)")
    assert "'other [2J' (id a-2)" in evidence["cause"]
    assert "\x1b" not in evidence["cause"]
    assert evidence["next_action"] == ImportAccountMismatchError.next_action
    assert LINEAGE_TOKEN not in stored.model_dump_json()


@pytest.mark.usefixtures("_claimed_worker_execution")
def test_an_unclassified_failure_records_no_cause_text(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Only the repository's own refusals and the classification are recorded as text."""
    stored = _record_plan_failure(
        monkeypatch, tmp_path, "run-plan-unclassified", ValueError(f"server said {LINEAGE_TOKEN}")
    )

    evidence = stored.results["plan_failure"]
    assert "cause" not in evidence
    assert "next_action" not in evidence
    assert LINEAGE_TOKEN not in stored.model_dump_json()


@pytest.mark.usefixtures("_claimed_worker_execution")
def test_an_error_raised_while_handling_another_does_not_take_its_next_action(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Only the explicit `raise ... from` chain lends a next action, never the error being handled."""
    failure = RuntimeError(f"cleanup failed {LINEAGE_TOKEN}")
    failure.__context__ = PlanSchemaChangedError("schema changed")

    stored = _record_apply_failure(monkeypatch, tmp_path, "run-apply-context", failure)

    assert "next_action" not in stored.results["apply_failure"]


@pytest.mark.usefixtures("_claimed_worker_execution")
def test_an_error_raised_from_a_refusal_takes_its_next_action(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """A wrapper raised `from` a refusal records the refusal's next action."""
    failure = ValueError(f"apply failed {LINEAGE_TOKEN}")
    failure.__cause__ = PlanSchemaChangedError("schema changed")
    failure.__suppress_context__ = True

    stored = _record_apply_failure(monkeypatch, tmp_path, "run-apply-cause", failure)

    assert stored.results["apply_failure"]["next_action"] == PlanSchemaChangedError.next_action


@pytest.mark.parametrize(
    ("failure", "expected_next_action"),
    [
        pytest.param(
            PlanSchemaChangedError(f"schema changed {LINEAGE_TOKEN}"),
            "create and review a new plan before applying again",
            id="schema-changed",
        ),
        pytest.param(
            service_flow.RegisteredPlanVerificationError(
                f"refused {LINEAGE_TOKEN}", "rebuild", guidance="format_version: rebuild it"
            ),
            "re-run plan for this sync, review the new plan, then apply that run",
            id="registered-rebuild",
        ),
        pytest.param(
            service_flow.RegisteredPlanVerificationError(
                f"refused {LINEAGE_TOKEN}", "compatible_version", guidance="format_version: use another version"
            ),
            "re-run plan with this version and review the new plan, "
            "or apply the reviewed plan with the version that wrote it",
            id="registered-compatible-version",
        ),
        pytest.param(
            service_flow.RegisteredPlanVerificationError(
                f"refused {LINEAGE_TOKEN}",
                None,
                guidance="checksum: Re-run `plan` for this sync to rebuild the snapshot and its plan artifact.",
            ),
            "checksum: Re-run `plan` for this sync to rebuild the snapshot and its plan artifact.",
            id="registered-no-signal",
        ),
    ],
)
@pytest.mark.usefixtures("_claimed_worker_execution")
def test_an_apply_records_the_next_action_its_error_declares_and_not_its_message(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, failure: Exception, expected_next_action: str | None
) -> None:
    """The recorded next action is the text the CLI prints as the hint; the message stays out."""
    stored = _record_apply_failure(monkeypatch, tmp_path, "run-apply-next-action", failure)

    evidence = stored.results["apply_failure"]
    assert evidence.get("next_action") == expected_next_action
    assert "cause" not in evidence
    assert LINEAGE_TOKEN not in stored.model_dump_json()


_EVENT_WORKER_ID = "8c1da53d-0e6b-4d3d-a0f1-97b6a9ccebf0"


def _stage_execution(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    stage: Literal["plan", "verify", "apply", "sync"],
    *,
    requester: str | None,
) -> tuple[Any, str, str]:
    """Return a projection holding one claimed ``stage`` execution of a run ``initiator`` started.

    With ``requester``, that actor's accepted receipt holds the execution's flow run ID;
    without, no receipt holds it.
    """
    run_id = f"run-events-{stage}"
    flow_run_id = f"flow-events-{stage}"
    projection = local_product_projection(tmp_path)
    version = projection.create_configuration(package())
    projection.create_run(
        ProductRun(
            run_id=run_id,
            operation=stage,
            configuration_reference=f"{version.config_id}@{version.registry_version}",
            config_id=version.config_id,
            registry_version=version.registry_version,
            package_checksum=version.package_checksum,
            actor="initiator",
            started_at=datetime.now(timezone.utc),
            phase="accepted",
            summary={"sync_name": "inventory"},
        )
    )
    link = PrefectExecutionLink(flow_run_id=flow_run_id, purpose=stage, attempt=1)
    if requester is None:
        append_execution(projection, run_id, link)
    else:
        now = datetime.now(timezone.utc)
        receipt = MutationReceipt(
            receipt_id=f"m-{uuid4().hex}",
            actor=requester,
            key_digest=sha256(stage.encode()).hexdigest(),
            operation=stage,
            target_run_id=run_id,
            request_fingerprint=sha256(f"{stage}-request".encode()).hexdigest(),
            reason=f"{stage} requested",
            resource_id=run_id,
            run_id=run_id,
            prefect_key=sha256(f"{stage}-prefect".encode()).hexdigest(),
            created_at=now,
            updated_at=now,
        )
        projection.reserve_mutation(receipt, admit_write=stage in {"apply", "sync"})
        projection.add_prefect_execution(run_id, link, receipt_id=receipt.receipt_id)
        projection.complete_mutation(
            receipt.receipt_id, response_status=202, response_body={"run": {"run_id": run_id}}, flow_run_id=flow_run_id
        )
    assert projection.claim_execution(run_id, flow_run_id, worker_id=_EVENT_WORKER_ID)

    def outside_the_result(*_args: object, **_kwargs: object) -> NoReturn:
        msg = "a stage-result event must be written with the result"
        raise AssertionError(msg)

    monkeypatch.setattr(projection, "record_audit", outside_the_result)
    return projection, run_id, flow_run_id


@pytest.mark.parametrize(
    ("stage", "event_type"),
    [
        ("plan", "infrahub_sync.run.planned"),
        ("verify", "infrahub_sync.run.verified"),
        ("apply", "infrahub_sync.run.applied"),
        ("sync", "infrahub_sync.run.synced"),
    ],
)
def test_a_successful_stage_records_its_result_event_with_the_requesting_actor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stage: Literal["plan", "verify", "apply", "sync"], event_type: str
) -> None:
    """FR-012a and FR-014: the success and its event commit together, naming who requested the stage."""
    projection, run_id, flow_run_id = _stage_execution(tmp_path, monkeypatch, stage, requester="admin")
    writeback = (
        ExecutionMergeWriteback(results={})
        if stage == "verify"
        else ExecutionFinishWriteback(
            phase="planned", outcome="planned", finished_at=datetime.now(timezone.utc), summary={}, results={}
        )
    )

    service_flow._commit_success(
        projection,
        run_id,
        flow_run_id,
        stage=stage,
        worker_id=_EVENT_WORKER_ID,
        writeback=writeback,
        secrets=(),
        run_logger=logging.getLogger("test-service"),
    )

    (event,) = projection.audit_events(run_id)
    assert (event.event_type, event.operation, event.outcome, event.actor, event.reason) == (
        event_type,
        stage,
        "succeeded",
        "admin",
        f"{stage} requested",
    )
    config_id, registry_version, _checksum = _binding(projection, run_id)
    assert (event.config_id, event.registry_version) == (config_id, registry_version)
    assert [related.kind for related in event.related] == ["account", "config", "config_version"]


@pytest.mark.parametrize(("dispatch_started", "outcome"), [(False, "failed"), (True, "ambiguous")])
def test_a_failed_or_interrupted_stage_records_a_run_failed_event_with_the_runs_actor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, dispatch_started: bool, outcome: str
) -> None:
    """FR-012a: with no receipt holding the flow run ID, the event names the run's actor."""
    projection, run_id, flow_run_id = _stage_execution(tmp_path, monkeypatch, "apply", requester=None)

    recorded = service_flow._record_failure(
        run_id,
        "apply",
        RuntimeError("apply failed"),
        _RecordingRunLogger(),
        (),
        projection,
        flow_run_id,
        _EVENT_WORKER_ID,
        dispatch_started=dispatch_started,
        record=None,
    )

    assert recorded is not None
    assert recorded.outcome == outcome
    (event,) = projection.audit_events(run_id)
    assert (event.event_type, event.operation, event.outcome, event.actor) == (
        "infrahub_sync.run.failed",
        "apply",
        outcome,
        "initiator",
    )
