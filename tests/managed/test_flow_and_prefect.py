from __future__ import annotations

import inspect
import logging
from contextlib import nullcontext
from datetime import datetime, timezone
from pathlib import Path
from threading import Event, Thread, current_thread
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, Literal, NoReturn, Protocol
from uuid import UUID, uuid4

import httpx
import pytest
from typing_extensions import Self

pytest.importorskip("prefect")
pytest.importorskip("opsmill_prefect_extras")

from opsmill_prefect_extras.deployments import apply_deployments
from opsmill_prefect_extras.workflows import assert_valid_definitions
from prefect.exceptions import MissingContextError, ObjectNotFound
from prefect.states import Failed, Pending

from infrahub_sync.execution import RunResult
from infrahub_sync.managed import flow as managed_flow
from infrahub_sync.managed.deploy import CATALOGUE
from infrahub_sync.managed.flow import managed_sync_run
from infrahub_sync.managed.orchestration import MANAGED_DEFINITION, Observation, PrefectOrchestration
from infrahub_sync.orchestration import flow as direct_flow
from infrahub_sync.orchestration.flow import infrahub_sync_run
from infrahub_sync.plan.errors import OperationApplyFailedError
from infrahub_sync.plan.models import ApplyRecord, PlanManifest
from infrahub_sync.plan.review import SavedPlan
from infrahub_sync.product_store import ProductRun, local_product_projection

if TYPE_CHECKING:
    from prefect.client.schemas.actions import DeploymentUpdate
    from prefect.client.schemas.objects import State


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


def _instance(sync_name: str, *, directory: str) -> SimpleNamespace:  # noqa: ARG001 - resolver protocol fake.
    return SimpleNamespace(name=sync_name)


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
        self.managed_acquire_attempted = Event()

    def acquire(self) -> None:
        """Signal from inside the contender's acquisition attempt, then delegate."""
        thread_name = current_thread().name
        self.events.append(f"{thread_name}:acquire-attempted")
        if thread_name == "test-managed-flow":
            self.managed_acquire_attempted.set()
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
    projection.create_run(
        ProductRun(
            run_id=run_id,
            operation=operation,
            configuration_reference="sha256:configuration",
            actor="owner",
            started_at=datetime.now(timezone.utc),
            phase="accepted",
            summary={"sync_name": "inventory"},
        )
    )
    return projection


def test_managed_and_direct_prefect_flow_schemas_are_separate_and_exact() -> None:
    assert tuple(inspect.signature(managed_sync_run.fn).parameters) == (
        "run_id",
        "sync_name",
        "stage",
        "configuration_reference",
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
    assert CATALOGUE.keys() == (MANAGED_DEFINITION.key,)
    assert_valid_definitions(CATALOGUE)


def test_flow_working_directory_is_required_absolute_and_existing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from infrahub_sync.managed import deploy

    monkeypatch.delenv(deploy.FLOW_WORKING_DIRECTORY_ENV, raising=False)
    with pytest.raises(ValueError, match=deploy.FLOW_WORKING_DIRECTORY_ENV):
        deploy.required_flow_working_directory()

    monkeypatch.setenv(deploy.FLOW_WORKING_DIRECTORY_ENV, "relative/path")
    with pytest.raises(ValueError, match="absolute"):
        deploy.required_flow_working_directory()

    monkeypatch.setenv(deploy.FLOW_WORKING_DIRECTORY_ENV, str(tmp_path))
    assert deploy.required_flow_working_directory() == str(tmp_path)
    assert deploy.flow_pull_steps(str(tmp_path)) == [
        {"prefect.deployments.steps.set_working_directory": {"directory": str(tmp_path)}}
    ]


def test_managed_definition_entrypoint_targets_the_flow_file() -> None:
    """The applied deployment must carry an executable entrypoint.

    Without one, a Prefect process worker refuses every managed flow run with
    "does not have an entrypoint and can not be run" — the deployment library
    sends the entrypoint only when the definition supplies it.
    """
    assert MANAGED_DEFINITION.entrypoint is not None
    path_part, _, function_part = MANAGED_DEFINITION.entrypoint.rpartition(":")
    flow_file = Path(path_part)
    assert flow_file.is_absolute(), "entrypoint must encode the shared-installation path contract"
    assert flow_file.name == "flow.py"
    assert flow_file.is_file()
    assert function_part == "managed_sync_run"


def test_missing_context_uses_local_logger_without_constructing_a_bridge(monkeypatch: pytest.MonkeyPatch) -> None:
    def missing_context():
        raise MissingContextError

    def bridge_is_forbidden(_logger):
        msg = "RunLoggerBridge must not be constructed in the local fallback"
        raise AssertionError(msg)

    monkeypatch.setattr(managed_flow, "get_run_logger", missing_context)
    monkeypatch.setattr(managed_flow, "RunLoggerBridge", bridge_is_forbidden)

    run_logger, prefect_context = managed_flow._run_logger()
    with managed_flow._remote_log_bridge(run_logger, prefect_context=prefect_context):
        run_logger.info("local managed execution")

    assert isinstance(run_logger, logging.Logger)
    assert prefect_context is False


def test_direct_and_managed_log_bridges_serialize_ownership_and_restore_state(  # noqa: PLR0914, PLR0915
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Concurrent flow bridges must not share records or clobber logger state."""
    direct_canary = "direct-flow-secret-canary"
    managed_canary = "managed-flow-secret-canary"
    direct_logger = _RecordingRunLogger()
    managed_logger = _RecordingRunLogger()
    source_logger = logging.getLogger(managed_flow.SOURCE_LOGGER_NAME)
    child_logger = logging.getLogger(f"{managed_flow.SOURCE_LOGGER_NAME}.concurrency-test")
    sentinel_handler = logging.NullHandler()
    original_handlers = list(source_logger.handlers)
    original_level = source_logger.level
    original_propagate = source_logger.propagate
    direct_entered = Event()
    release_direct = Event()
    managed_entered = Event()
    release_managed = Event()
    direct_failures: list[BaseException] = []
    managed_failures: list[BaseException] = []

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

    def run_managed_bridge() -> None:
        try:
            with managed_flow._remote_log_bridge(
                managed_logger,
                prefect_context=True,
                secrets=(managed_canary,),
            ):
                managed_entered.set()
                child_logger.warning("managed record used %s", managed_canary)
                assert release_managed.wait(timeout=5)
        except BaseException as exc:  # noqa: BLE001 - retain thread failure for the main test.
            managed_failures.append(exc)

    monkeypatch.setattr("infrahub_sync.orchestration.flow.get_run_logger", lambda: direct_logger)
    monkeypatch.setattr("infrahub_sync.orchestration.flow.collect_secret_values", lambda: (direct_canary,))
    monkeypatch.setattr("infrahub_sync.orchestration.flow.run_remote_request", fail_direct_request)
    monkeypatch.setenv(managed_flow.CONFIG_DIR_ENV, str(tmp_path))
    original_ownership_lock = direct_flow._REMOTE_LOGGER_OWNERSHIP_LOCK
    assert managed_flow._REMOTE_LOGGER_OWNERSHIP_LOCK is original_ownership_lock
    ownership_probe = _LoggerOwnershipProbe(original_ownership_lock)
    monkeypatch.setattr(direct_flow, "_REMOTE_LOGGER_OWNERSHIP_LOCK", ownership_probe)
    monkeypatch.setattr(managed_flow, "_REMOTE_LOGGER_OWNERSHIP_LOCK", ownership_probe)
    source_logger.handlers = [sentinel_handler]
    source_logger.setLevel(logging.ERROR)
    source_logger.propagate = True

    direct_thread = Thread(target=run_direct, name="test-direct-flow")
    managed_thread = Thread(target=run_managed_bridge, name="test-managed-flow")
    try:
        direct_thread.start()
        assert direct_entered.wait(timeout=5)
        managed_thread.start()
        assert ownership_probe.managed_acquire_attempted.wait(timeout=5)
        child_logger.warning("direct record used %s", direct_canary)

        release_direct.set()
        direct_thread.join(timeout=5)
        assert not direct_thread.is_alive()
        assert managed_entered.wait(timeout=5)
        release_managed.set()
        managed_thread.join(timeout=5)
        assert not managed_thread.is_alive()

        rendered = "\n".join((*direct_logger.rendered, *managed_logger.rendered))
        expected_acquisition_order = [
            "test-direct-flow:acquire-attempted",
            "test-direct-flow:acquired",
            "test-managed-flow:acquire-attempted",
            "test-direct-flow:released",
            "test-managed-flow:acquired",
            "test-managed-flow:released",
        ]
        violations = [
            label
            for label, violated in (
                ("bridge ownership was not serialized", ownership_probe.events != expected_acquisition_order),
                (
                    "direct bridge received the managed record",
                    any("managed record" in line for line in direct_logger.rendered),
                ),
                (
                    "managed bridge received the direct record",
                    any("direct record" in line for line in managed_logger.rendered),
                ),
                ("direct canary reached a run logger", direct_canary in rendered),
                ("managed canary reached a run logger", managed_canary in rendered),
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
        assert managed_failures == []
    finally:
        release_direct.set()
        release_managed.set()
        direct_thread.join(timeout=5)
        managed_thread.join(timeout=5)
        source_logger.handlers = original_handlers
        source_logger.setLevel(original_level)
        source_logger.propagate = original_propagate


def test_managed_flow_redacts_worker_logs_exception_chain_and_failed_state(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    environment_canary = "worker-environment-token-canary"
    configuration_canary = "worker-configuration-token-canary"
    run_id = "run-managed-secret-failure"
    projection = _create_product_run(tmp_path.resolve(), run_id)
    run_logger = _RecordingRunLogger()
    instance = SimpleNamespace(
        name="inventory",
        source=SimpleNamespace(settings={"token": configuration_canary}),
        destination=SimpleNamespace(settings={}),
        store=None,
    )
    monkeypatch.setenv("NETBOX_TOKEN", environment_canary)
    monkeypatch.setattr(managed_flow, "_runtime", lambda: (str(tmp_path), projection))
    monkeypatch.setattr(managed_flow, "_run_logger", lambda: (run_logger, True))

    def resolve(_sync_name: str, *, directory: str):  # noqa: ARG001
        logging.getLogger("infrahub_sync.managed.worker").warning(
            "resolution used %s",
            environment_canary,
        )
        return instance

    def fail_plan(_instance, *, run_id: str, branch: str | None, composed_sync: bool):  # noqa: ARG001
        logging.getLogger("infrahub_sync.managed.worker").error(
            "execution used %s",
            configuration_canary,
        )
        cause_message = f"transport rejected {environment_canary}"
        failure_message = f"adapter rejected {configuration_canary}"
        raise ValueError(failure_message) from ConnectionError(cause_message)

    monkeypatch.setattr(managed_flow, "resolve_sync_instance", resolve)
    monkeypatch.setattr(managed_flow, "_plan", fail_plan)

    with pytest.raises(RuntimeError) as exc_info:
        managed_sync_run.fn(run_id, "inventory", "plan", "sha256:configuration")

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


def test_managed_apply_failure_retains_partial_write_evidence(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    run_id = "run-managed-apply-failure"
    projection = _create_product_run(tmp_path.resolve(), run_id)
    partial = ApplyRecord(applied_operations=("op-applied",), failed_operation="op-failed")
    monkeypatch.setattr(managed_flow, "_runtime", lambda: (str(tmp_path), projection))
    monkeypatch.setattr(managed_flow, "_run_logger", lambda: (logging.getLogger("test-managed"), False))
    monkeypatch.setattr(managed_flow, "resolve_sync_instance", _instance)
    monkeypatch.setattr(managed_flow, "collect_secret_values", lambda _instance=None: ())

    def fail_apply(*_args: object, **_kwargs: object) -> NoReturn:
        msg = "destination rejected operation"
        raise OperationApplyFailedError(msg, apply_record=partial)

    monkeypatch.setattr(managed_flow, "execute_run", fail_apply)

    with pytest.raises(RuntimeError):
        managed_sync_run.fn(
            run_id,
            "inventory",
            "apply",
            "sha256:configuration",
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


def test_managed_confirmed_sync_retains_the_semantic_sync_operation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    run_id = "run-managed-semantic-sync"
    projection = _create_product_run(tmp_path.resolve(), run_id, operation="sync")
    saved = _saved(run_id)
    monkeypatch.setattr(managed_flow, "_runtime", lambda: (str(tmp_path), projection))
    monkeypatch.setattr(managed_flow, "_run_logger", lambda: (logging.getLogger("test-managed"), False))
    monkeypatch.setattr(managed_flow, "resolve_sync_instance", _instance)
    monkeypatch.setattr(managed_flow, "collect_secret_values", lambda _instance=None: ())
    monkeypatch.setattr(managed_flow, "bounded_run_lock", lambda *_args, **_kwargs: nullcontext())

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

    monkeypatch.setattr(managed_flow, "execute_run", core)

    result = managed_sync_run.fn(
        run_id,
        "inventory",
        "sync",
        "sha256:configuration",
        confirm_writes=True,
    )

    assert result["operation"] == "sync"
    stored = projection.lookup_run(run_id).value
    assert stored is not None
    assert stored.operation == "sync"
    assert stored.results["operation"] == "sync"


def test_managed_plan_worker_updates_the_api_created_run_and_publishes_review(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    run_id = "run-managed-plan"
    projection = _create_product_run(tmp_path.resolve(), run_id)
    saved = _saved(run_id)
    seen: list[str] = []
    monkeypatch.setattr(managed_flow, "_runtime", lambda: (str(tmp_path), projection))
    monkeypatch.setattr(managed_flow, "_run_logger", lambda: (logging.getLogger("test-managed"), False))
    monkeypatch.setattr(managed_flow, "resolve_sync_instance", _instance)
    monkeypatch.setattr(managed_flow, "collect_secret_values", lambda _instance=None: ())

    def plan(_instance, *, run_id: str, branch: str | None, composed_sync: bool):  # noqa: ARG001
        seen.append(run_id)
        assert composed_sync is False
        return saved

    monkeypatch.setattr(managed_flow, "_plan", plan)

    result = managed_sync_run.fn(
        run_id,
        "inventory",
        "plan",
        "sha256:configuration",
    )

    assert seen == [run_id]
    assert result["run_id"] == run_id
    stored = projection.lookup_run(run_id).value
    assert stored is not None
    assert stored.phase == "planned"
    assert [artifact.artifact_id for artifact in stored.artifact_refs] == ["plan-review"]


def test_managed_verify_is_read_only_for_product_lifecycle(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    run_id = "run-managed-verify"
    projection = _create_product_run(tmp_path.resolve(), run_id)
    before = projection.lookup_run(run_id).value
    saved = _saved(run_id)
    monkeypatch.setattr(managed_flow, "_runtime", lambda: (str(tmp_path), projection))
    monkeypatch.setattr(managed_flow, "_run_logger", lambda: (logging.getLogger("test-managed"), False))
    monkeypatch.setattr(managed_flow, "resolve_sync_instance", _instance)
    monkeypatch.setattr(managed_flow, "collect_secret_values", lambda _instance=None: ())
    monkeypatch.setattr(managed_flow, "execute_run", lambda *_args, **_kwargs: saved)

    result = managed_sync_run.fn(
        run_id,
        "inventory",
        "verify",
        "sha256:configuration",
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
    assert after.model_dump(exclude={"results"}) == before.model_dump(exclude={"results"})
    assert after.results == {"verification": result}


def test_confirmed_managed_sync_calls_plan_verify_apply_in_order_on_one_run(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    run_id = "run-managed-sync"
    projection = _create_product_run(tmp_path.resolve(), run_id, operation="sync")
    saved = _saved(run_id)
    calls: list[tuple[str, str]] = []
    monkeypatch.setattr(managed_flow, "_runtime", lambda: (str(tmp_path), projection))
    monkeypatch.setattr(managed_flow, "_run_logger", lambda: (logging.getLogger("test-managed"), False))
    monkeypatch.setattr(managed_flow, "bounded_run_lock", lambda *_args, **_kwargs: nullcontext())
    monkeypatch.setattr(managed_flow, "resolve_sync_instance", _instance)
    monkeypatch.setattr(managed_flow, "collect_secret_values", lambda _instance=None: ())

    def plan(_instance, *, run_id: str, branch: str | None, composed_sync: bool):  # noqa: ARG001
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

    monkeypatch.setattr(managed_flow, "_plan", plan)
    monkeypatch.setattr(managed_flow, "execute_run", execute)

    managed_sync_run.fn(
        run_id,
        "inventory",
        "sync",
        "sha256:configuration",
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
        assert name == MANAGED_DEFINITION.key
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

    async def set_flow_run_state(self, flow_run_id: UUID, state: State[object]) -> SimpleNamespace:
        assert flow_run_id == self.flow_run.id
        self.flow_run.state = state
        return SimpleNamespace()


@pytest.mark.asyncio
async def test_prefect_extras_executor_receives_opaque_key_unchanged() -> None:
    client = _RemoteClient()
    gateway = PrefectOrchestration(client)  # type: ignore[arg-type] - offline fake implements the protocol.
    parameters: dict[str, object] = {
        "run_id": "run-001",
        "sync_name": "inventory",
        "stage": "plan",
        "configuration_reference": "sha256:configuration",
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
    cancelled = await gateway.cancel(str(uuid4()))

    assert observed == Observation(
        available=False,
        state=None,
        reason="prefect-read-unavailable",
    )
    assert cancelled == observed


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
async def test_prefect_extras_deployment_converges_the_managed_catalogue_offline() -> None:
    client = _DeploymentClient()

    report = await apply_deployments(CATALOGUE, work_pool_name="managed-pool", client=client)

    assert report.is_successful
    assert [result.status for result in report.results] == ["created"]
    assert client.created[0]["work_pool_name"] == "managed-pool"
