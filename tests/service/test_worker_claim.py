"""Service worker claim ordering and Prefect attribution conformance."""

from __future__ import annotations

import asyncio
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, Literal, cast
from uuid import UUID

import httpx
import pytest
from typing_extensions import Self

pytest.importorskip("prefect")

from prefect import __version__ as prefect_version
from prefect.client.schemas.objects import WorkerStatus
from prefect.workers.process import ProcessJobConfiguration, ProcessWorker

from infrahub_sync.product_store import PrefectExecutionLink, ProductRun, local_product_projection
from infrahub_sync.service import flow as service_flow
from tests.service.execution_fixtures import append_execution

if TYPE_CHECKING:
    from prefect.client.schemas.objects import FlowRun

FLOW_ID = "ed4778cb-f2cf-4b1f-a87b-68be37659e93"
WORKER_ID = "8c1da53d-0e6b-4d3d-a0f1-97b6a9ccebf0"
WORKER_NAME = "infrahub-sync-service-test"
POOL_ID = "e0679e8a-9460-4ca7-8bf1-70bf967eed2d"
POOL_NAME = "service-pool"


class _PrefectWorkerClient:
    """Synchronous Prefect 3.8.1 worker-registry seam used immediately before claim."""

    def __init__(self, worker_id: str = WORKER_ID) -> None:
        self.worker_id = worker_id

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def read_flow_run(self, flow_run_id: UUID) -> SimpleNamespace:  # noqa: PLR6301 - fake client protocol.
        assert isinstance(flow_run_id, UUID)
        return SimpleNamespace(work_pool_id=UUID(POOL_ID), work_pool_name=POOL_NAME)

    def read_workers_for_work_pool(
        self,
        work_pool_name: str,
        worker_filter: object = None,  # noqa: ARG002 - pinned Prefect client shape.
        offset: int | None = None,
        limit: int | None = None,
    ) -> list[SimpleNamespace]:
        assert work_pool_name == POOL_NAME
        assert offset == 0
        assert limit == 200
        return [
            SimpleNamespace(
                id=UUID(self.worker_id),
                name=WORKER_NAME,
                work_pool_id=UUID(POOL_ID),
                status=WorkerStatus.ONLINE,
            )
        ]


@pytest.fixture(autouse=True)
def _current_prefect_worker(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _PrefectWorkerClient()
    monkeypatch.setenv("PREFECT__WORKER_NAME", WORKER_NAME)
    monkeypatch.setattr(service_flow, "get_client", lambda **_kwargs: client, raising=False)


def _projection(tmp_path: Path, *, submitted_at: datetime | None = None, migrated: bool = False):
    projection = local_product_projection(tmp_path)
    now = datetime.now(timezone.utc)
    effective_submitted_at = now if submitted_at is None else submitted_at
    projection.create_run(
        ProductRun(
            run_id="run-worker-claim",
            operation="plan",
            configuration_reference="legacy",
            started_at=now,
            phase="accepted",
        )
    )
    append_execution(
        projection,
        "run-worker-claim",
        PrefectExecutionLink(
            flow_run_id=FLOW_ID,
            purpose="plan",
            attempt=1,
            last_observed_at=effective_submitted_at if migrated else None,
            submitted_at=effective_submitted_at,
        ),
    )
    if migrated:
        with sqlite3.connect(tmp_path / "product-records.sqlite3") as connection:
            connection.execute(
                "UPDATE prefect_executions SET submitted_at = NULL WHERE run_id = ? AND flow_run_id = ?",
                ("run-worker-claim", FLOW_ID),
            )
    return projection


def test_prefect_381_process_configuration_preserves_worker_attribution_environment() -> None:
    """Pin the server UUID from job preparation through the process child environment."""
    assert prefect_version == "3.8.1"
    flow_run = SimpleNamespace(
        id=UUID(FLOW_ID),
        name="service-run",
        flow_id=UUID("d08f703b-ce73-4269-a7aa-1bfb00f8cc63"),
        deployment_id=None,
    )
    configuration = ProcessJobConfiguration(command="python -m prefect.engine", env={})
    typed_flow_run = cast("FlowRun", flow_run)
    configuration.prepare_for_flow_run(typed_flow_run, worker_id=UUID(WORKER_ID))
    child_environments: list[dict[str, str | None]] = []

    class Runner:
        async def execute_flow_run(  # noqa: PLR0913, PLR6301 - minimal pinned worker runner.
            self,
            *,
            flow_run_id: UUID,  # noqa: ARG002 - signature pins the worker seam.
            command: str | None,  # noqa: ARG002 - signature pins the worker seam.
            cwd: object,  # noqa: ARG002 - signature pins the worker seam.
            env: dict[str, str | None],
            stream_output: bool,  # noqa: ARG002 - signature pins the worker seam.
            task_status: object,  # noqa: ARG002 - signature pins the worker seam.
        ) -> SimpleNamespace:
            child_environments.append(env)
            return SimpleNamespace(returncode=0, pid=42)

    worker = object.__new__(ProcessWorker)
    worker._runner = Runner()

    result = asyncio.run(worker.run(typed_flow_run, configuration))

    assert result.status_code == 0
    assert child_environments[0]["PREFECT__WORKER_ID"] == WORKER_ID


def test_worker_claims_canonical_prefect_execution_before_runtime_work(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    projection = _projection(tmp_path)
    monkeypatch.setenv("PREFECT__WORKER_ID", WORKER_ID)
    monkeypatch.setattr(service_flow, "_prefect_flow_run_id", lambda: FLOW_ID)

    service_flow._claim_current_execution(projection, "run-worker-claim")

    link = projection.lookup_run("run-worker-claim").value.prefect_executions[0]  # type: ignore[union-attr]
    assert link.claiming_worker_id == WORKER_ID


def test_worker_registry_failure_is_value_free_and_does_not_claim(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    projection = _projection(tmp_path)
    client = _PrefectWorkerClient()
    canary = "worker-registry-secret-canary"

    def _fail(_flow_run_id: UUID) -> SimpleNamespace:
        raise httpx.ConnectError(canary)

    monkeypatch.setattr(client, "read_flow_run", _fail)
    monkeypatch.setattr(service_flow, "get_client", lambda **_kwargs: client)
    monkeypatch.setenv("PREFECT__WORKER_ID", WORKER_ID)
    monkeypatch.setattr(service_flow, "_prefect_flow_run_id", lambda: FLOW_ID)

    with pytest.raises(RuntimeError) as caught:
        service_flow._claim_current_execution(projection, "run-worker-claim")

    assert str(caught.value) == "service worker execution identity is unavailable"
    assert canary not in repr(caught.value)
    link = projection.lookup_run("run-worker-claim").value.prefect_executions[0]  # type: ignore[union-attr]
    assert link.claimed_at is None


@pytest.mark.parametrize("stage", ["plan", "verify", "apply", "sync"])
def test_expired_worker_claim_refusal_precedes_all_stage_runtime_construction(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    stage: Literal["plan", "verify", "apply", "sync"],
) -> None:
    """An expired admission cannot enter registry, package, adapter, cache, or artifact work."""
    projection = _projection(
        tmp_path,
        submitted_at=datetime.now(timezone.utc) - timedelta(seconds=2),
        migrated=True,
    )
    constructed: list[str] = []
    monkeypatch.setenv("PREFECT__WORKER_ID", WORKER_ID)
    monkeypatch.setenv("INFRAHUB_SYNC_RUN_ADMISSION_TTL_SECONDS", "1")
    monkeypatch.setattr(service_flow, "_prefect_flow_run_id", lambda: FLOW_ID)
    monkeypatch.setattr(service_flow, "_runtime", lambda: (str(tmp_path), projection))
    monkeypatch.setattr(service_flow, "_execute_stage", lambda *_args, **_kwargs: constructed.append(stage))

    with pytest.raises(RuntimeError, match="service worker execution claim was refused"):
        service_flow.service_sync_run.fn("run-worker-claim", stage)

    assert constructed == []


@pytest.mark.parametrize("state", ["missing", "claimed", "abandoned", "interrupted"])
def test_claim_refusal_prevents_registry_and_adapter_construction(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, state: str
) -> None:
    projection = _projection(tmp_path)
    flow_id = FLOW_ID
    if state == "missing":
        flow_id = "d08f703b-ce73-4269-a7aa-1bfb00f8cc63"
    elif state == "claimed":
        assert projection.claim_execution("run-worker-claim", FLOW_ID, worker_id=WORKER_ID)
    elif state == "abandoned":
        assert projection.abandon_execution("run-worker-claim", FLOW_ID)
    else:
        assert projection.claim_execution("run-worker-claim", FLOW_ID, worker_id=WORKER_ID)
        assert projection.interrupt_execution("run-worker-claim", FLOW_ID)
    constructed: list[object] = []
    monkeypatch.setenv("PREFECT__WORKER_ID", WORKER_ID)
    monkeypatch.setattr(service_flow, "_prefect_flow_run_id", lambda: flow_id)
    monkeypatch.setattr(service_flow, "_runtime", lambda: (str(tmp_path), projection))
    monkeypatch.setattr(service_flow, "resolve_runtime_instance", lambda *_args, **_kwargs: constructed.append(True))

    with pytest.raises(RuntimeError, match="service worker execution claim was refused"):
        service_flow.service_sync_run.fn("run-worker-claim", "plan")
    assert constructed == []


@pytest.mark.parametrize("value", [None, "not-a-uuid", FLOW_ID.upper()])
def test_worker_identity_rejects_noncanonical_values(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, value: str | None
) -> None:
    projection = _projection(tmp_path)
    if value is None:
        monkeypatch.delenv("PREFECT__WORKER_ID", raising=False)
    else:
        monkeypatch.setenv("PREFECT__WORKER_ID", value)
    monkeypatch.setattr(service_flow, "_prefect_flow_run_id", lambda: FLOW_ID)

    with pytest.raises(RuntimeError, match="service worker execution identity is invalid"):
        service_flow._claim_current_execution(projection, "run-worker-claim")
    assert projection.lookup_run("run-worker-claim").value.prefect_executions[0].claimed_at is None  # type: ignore[union-attr]
