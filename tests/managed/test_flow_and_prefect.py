from __future__ import annotations

import inspect
import logging
from contextlib import nullcontext
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, Literal, NoReturn
from uuid import UUID, uuid4

import httpx
import pytest

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
from infrahub_sync.orchestration.flow import infrahub_sync_run
from infrahub_sync.plan.models import PlanManifest
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

    def log(self, _level: int, message: str, *args: object) -> None:
        self.rendered.append(message % args)

    def info(self, message: str, *args: object) -> None:
        self.rendered.append(message % args)


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


def test_managed_and_developer_preview_flow_schemas_are_separate_and_exact() -> None:
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
