"""Deterministic liveness policy boundaries."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from types import SimpleNamespace
from typing import TYPE_CHECKING, cast
from uuid import uuid4

from infrahub_sync.managed.liveness import LivenessPolicy, RunLivenessReconciler
from infrahub_sync.managed.orchestration import CancellationResult, Observation, PoolStatus, PoolWorker, Submission
from infrahub_sync.product_store import MutationReceipt, PrefectExecutionLink, ProductRun, local_product_projection

if TYPE_CHECKING:
    import pytest
    from opsmill_prefect_extras.executors import RemoteExecutionClient


def test_admission_ttl_and_prefect_query_define_liveness_formulae(monkeypatch: pytest.MonkeyPatch) -> None:
    """The two accepted environment values define all derived timing."""
    from infrahub_sync.managed.liveness import LivenessPolicy

    monkeypatch.setenv("INFRAHUB_SYNC_RUN_ADMISSION_TTL_SECONDS", "300")
    policy = LivenessPolicy.from_environment(worker_query_seconds="10")

    assert policy.admission_ttl_seconds == 300
    assert policy.stall_threshold_seconds == 30
    assert policy.cadence_seconds == 5


class _Orchestration:
    def __init__(self, pool: PoolStatus, observation: Observation | None = None) -> None:
        self.pool = pool
        self.observation = observation or Observation(available=True, state="running")

    async def pool_status(self, work_pool_name: str, now: datetime) -> PoolStatus:
        del work_pool_name, now
        return self.pool

    async def submit(self, parameters: dict[str, object], *, idempotency_key: str) -> Submission:  # noqa: PLR6301
        del parameters, idempotency_key
        return Submission(flow_run_id="unused", state="running")

    async def observe(self, flow_run_id: str) -> Observation:
        del flow_run_id
        return self.observation

    async def cancel(self, flow_run_id: str) -> CancellationResult:  # noqa: PLR6301
        del flow_run_id
        return CancellationResult(acknowledged=True)


def _run(run_id: str, link: PrefectExecutionLink) -> ProductRun:
    return ProductRun(
        run_id=run_id,
        operation="plan",
        configuration_reference="sha256:configuration",
        actor="operator@example.com",
        audit_links=("ticket:42",),
        started_at=datetime(2026, 8, 29, tzinfo=timezone.utc),
        phase="planning",
        prefect_executions=(link,),
    )


def _request_cancellation(projection, run_id: str, flow_run_id: str, now: datetime, *, acknowledge: bool) -> None:
    receipt = MutationReceipt(
        receipt_id=f"mutation-{run_id}",
        actor="operator",
        key_digest=sha256(run_id.encode()).hexdigest(),
        operation="cancel",
        target_run_id=run_id,
        request_fingerprint=sha256(f"cancel:{run_id}".encode()).hexdigest(),
        reason="operator requested stop",
        resource_id=run_id,
        run_id=run_id,
        prefect_key=sha256(f"prefect:{run_id}".encode()).hexdigest(),
        created_at=now,
        updated_at=now,
    )
    projection.reserve_mutation(receipt)
    assert projection.claim_mutation(receipt.receipt_id)
    assert projection.request_execution_cancellation(
        run_id,
        flow_run_id,
        requested_at=now - timedelta(seconds=30),
        recovery_deadline_at=now,
        receipt_id=receipt.receipt_id,
    )
    if acknowledge:
        assert projection.acknowledge_execution_cancellation(
            run_id,
            flow_run_id,
            acknowledged_at=now - timedelta(seconds=1),
            response_status=202,
            response_body={"run": {"run_id": run_id}, "orchestration": []},
        )


def test_reconciler_orders_clean_cancellation_before_inclusive_expiry(tmp_path) -> None:
    """Durable acknowledged terminal-cancelled observation wins the deadline race."""
    now = datetime(2026, 8, 29, 12, tzinfo=timezone.utc)
    projection = local_product_projection(tmp_path)
    projection.create_run(
        _run(
            "run-clean-cancel",
            PrefectExecutionLink(
                flow_run_id="flow-clean-cancel",
                purpose="plan",
                attempt=1,
                submitted_at=now - timedelta(minutes=10),
            ),
        )
    )
    _request_cancellation(projection, "run-clean-cancel", "flow-clean-cancel", now, acknowledge=True)
    reconciler = RunLivenessReconciler(
        projection,
        _Orchestration(
            PoolStatus(detail_available=False, queue_depth=None, observed_at=None),
            Observation(available=True, state="cancelled"),
        ),
        LivenessPolicy(1, 30, 5),
        "pool",
        clock=lambda: now,
    )

    asyncio.run(reconciler.reconcile_once())

    run = projection.lookup_run("run-clean-cancel").value
    assert run is not None
    assert (run.phase, run.outcome, run.prefect_executions[0].terminal_state) == (
        "cancelled",
        "cancelled",
        "cancelled",
    )


def test_reconciler_expires_unacknowledged_external_cancel_at_equality(tmp_path) -> None:
    """External cancelled state is ambiguous without acknowledgement at the fixed deadline."""
    now = datetime(2026, 8, 29, 12, tzinfo=timezone.utc)
    projection = local_product_projection(tmp_path)
    projection.create_run(
        _run(
            "run-external-cancel",
            PrefectExecutionLink(
                flow_run_id="flow-external-cancel",
                purpose="plan",
                attempt=1,
                submitted_at=now - timedelta(minutes=10),
            ),
        )
    )
    _request_cancellation(projection, "run-external-cancel", "flow-external-cancel", now, acknowledge=False)
    reconciler = RunLivenessReconciler(
        projection,
        _Orchestration(
            PoolStatus(detail_available=False, queue_depth=None, observed_at=None),
            Observation(available=True, state="cancelled"),
        ),
        LivenessPolicy(1, 30, 5),
        "pool",
        clock=lambda: now,
    )

    asyncio.run(reconciler.reconcile_once())

    run = projection.lookup_run("run-external-cancel").value
    receipt = projection.lookup_mutation("operator", sha256(b"run-external-cancel").hexdigest()).value
    assert run is not None
    assert receipt is not None
    assert run.prefect_executions[0].terminal_state == "abandoned"
    assert receipt.response_status == 503


def test_claimed_execution_requires_its_exact_fresh_owner(tmp_path) -> None:
    """A dead owner is interrupted even when an unrelated worker is fresh."""
    now = datetime(2026, 8, 29, 12, tzinfo=timezone.utc)
    owner = str(uuid4())
    projection = local_product_projection(tmp_path)
    projection.create_run(
        _run(
            "run-dead-owner",
            PrefectExecutionLink(
                flow_run_id="flow-dead-owner",
                purpose="plan",
                attempt=1,
                submitted_at=now - timedelta(minutes=10),
                claimed_at=now - timedelta(seconds=30),
                claiming_worker_id=owner,
            ),
        )
    )
    pool = PoolStatus(
        detail_available=True,
        queue_depth=0,
        observed_at=now,
        workers=(
            PoolWorker(owner, "offline", now, 10),
            PoolWorker(str(uuid4()), "online", now, 10),
        ),
    )
    reconciler = RunLivenessReconciler(
        projection, _Orchestration(pool), LivenessPolicy(300, 30, 5), "pool", clock=lambda: now
    )

    asyncio.run(reconciler.reconcile_once())

    run = projection.lookup_run("run-dead-owner").value
    assert run is not None
    link = run.prefect_executions[0]
    assert link.terminal_state == "interrupted"


def test_owner_boundaries_and_clock_reversal_do_not_interrupt_early(tmp_path) -> None:
    """A fresh exact owner and a reversed clock retain the claimed execution."""
    now = datetime(2026, 8, 29, 12, tzinfo=timezone.utc)
    owner = str(uuid4())
    projection = local_product_projection(tmp_path)
    projection.create_run(
        _run(
            "run-fresh-owner",
            PrefectExecutionLink(
                flow_run_id="flow-fresh-owner",
                purpose="plan",
                attempt=1,
                submitted_at=now,
                claimed_at=now,
                claiming_worker_id=owner,
            ),
        )
    )
    pool = PoolStatus(
        detail_available=True,
        queue_depth=0,
        observed_at=now - timedelta(seconds=1),
        workers=(PoolWorker(owner, "online", now + timedelta(hours=1), 10),),
    )
    reconciler = RunLivenessReconciler(
        projection, _Orchestration(pool), LivenessPolicy(300, 30, 5), "pool", clock=lambda: now - timedelta(days=1)
    )

    asyncio.run(reconciler.reconcile_once())

    run = projection.lookup_run("run-fresh-owner").value
    assert run is not None
    assert run.prefect_executions[0].terminal_at is None


def test_restarted_worker_identity_and_unavailable_pool_do_not_conflate_owners(tmp_path) -> None:
    """A new live UUID cannot revive the old owner, while unavailable detail remains conservative."""
    now = datetime(2026, 8, 29, 12, tzinfo=timezone.utc)
    old_owner, new_owner = str(uuid4()), str(uuid4())
    projection = local_product_projection(tmp_path)
    for run_id, owner in (("run-restarted", old_owner), ("run-unavailable", old_owner)):
        projection.create_run(
            _run(
                run_id,
                PrefectExecutionLink(
                    flow_run_id=f"flow-{run_id}",
                    purpose="plan",
                    attempt=1,
                    submitted_at=now - timedelta(minutes=10),
                    claimed_at=now - timedelta(seconds=30),
                    claiming_worker_id=owner,
                ),
            )
        )
    policy = LivenessPolicy(300, 30, 5)
    restarted = RunLivenessReconciler(
        projection,
        _Orchestration(
            PoolStatus(
                detail_available=True,
                queue_depth=0,
                observed_at=now,
                workers=(PoolWorker(new_owner, "online", now, 10),),
            )
        ),
        policy,
        "pool",
        clock=lambda: now,
    )
    unavailable = RunLivenessReconciler(
        projection,
        _Orchestration(PoolStatus(detail_available=False, queue_depth=None, observed_at=None)),
        policy,
        "pool",
        clock=lambda: now,
    )

    asyncio.run(restarted.reconcile_run("run-restarted"))
    asyncio.run(unavailable.reconcile_run("run-unavailable"))

    restarted_run = projection.lookup_run("run-restarted").value
    unavailable_run = projection.lookup_run("run-unavailable").value
    assert restarted_run is not None
    assert unavailable_run is not None
    assert restarted_run.prefect_executions[0].terminal_state == "interrupted"
    assert unavailable_run.prefect_executions[0].terminal_at is None


def test_duplicate_reconcilers_converge_through_execution_cas(tmp_path) -> None:
    """Two instances reaching the admission boundary leave one terminal verdict."""
    now = datetime(2026, 8, 29, 12, tzinfo=timezone.utc)
    projection = local_product_projection(tmp_path)
    projection.create_run(
        _run(
            "run-cas",
            PrefectExecutionLink(
                flow_run_id="flow-cas",
                purpose="plan",
                attempt=1,
                submitted_at=now - timedelta(seconds=300),
            ),
        )
    )
    orchestration = _Orchestration(PoolStatus(detail_available=False, queue_depth=None, observed_at=None))
    first = RunLivenessReconciler(projection, orchestration, LivenessPolicy(300, 30, 5), "pool", clock=lambda: now)
    second = RunLivenessReconciler(projection, orchestration, LivenessPolicy(300, 30, 5), "pool", clock=lambda: now)

    async def reconcile_together() -> None:
        await asyncio.gather(first.reconcile_once(), second.reconcile_once())

    asyncio.run(reconcile_together())

    run = projection.lookup_run("run-cas").value
    assert run is not None
    assert run.phase == "abandoned"
    assert run.prefect_executions[0].terminal_outcome == "abandoned"


def test_lifespan_continues_after_ordinary_failure_and_cancels_cleanly(tmp_path) -> None:
    """A transient provider failure does not kill the loop, and shutdown propagates cancellation."""
    from infrahub_sync.managed.app import create_app
    from infrahub_sync.managed.auth import Principal
    from infrahub_sync.managed.service import ManagedRunService

    class _Reconciler:
        cadence_seconds = 0.01

        def __init__(self) -> None:
            self.calls = 0
            self.continued = asyncio.Event()
            self.cancelled = False

        async def reconcile_once(self) -> None:
            self.calls += 1
            if self.calls == 1:
                message = "ordinary provider failure"
                raise RuntimeError(message)
            self.continued.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                self.cancelled = True
                raise

    class _Resolver:
        secret_values = ()

        @staticmethod
        def resolve(token: str) -> Principal:
            del token
            return Principal(actor="operator")

    projection = local_product_projection(tmp_path)
    reconciler = _Reconciler()
    app = create_app(
        ManagedRunService(
            projection, _Orchestration(PoolStatus(detail_available=False, queue_depth=None, observed_at=None))
        ),
        _Resolver(),
        reconciler=cast("RunLivenessReconciler", reconciler),
    )  # type: ignore[arg-type]

    async def exercise() -> None:
        async with app.router.lifespan_context(app):
            await asyncio.wait_for(reconciler.continued.wait(), timeout=1)

    asyncio.run(exercise())
    assert reconciler.calls == 2
    assert reconciler.cancelled


def test_pool_parsing_is_value_free_and_heartbeat_freshness_includes_equality() -> None:
    """Only native Prefect-shaped values are parsed, and equality is still fresh."""
    from infrahub_sync.managed.orchestration import PrefectOrchestration
    from infrahub_sync.managed.service import _service_status  # noqa: PLC2701 - public status behavior test.

    now = datetime(2026, 8, 29, 12, tzinfo=timezone.utc)

    class _PoolClient:
        def __init__(self, interval: object) -> None:
            self.worker = SimpleNamespace(
                id=uuid4(),
                status="ONLINE",
                last_heartbeat_time=now - timedelta(seconds=30),
                heartbeat_interval_seconds=interval,
            )

        async def read_workers_for_work_pool(self, _pool: str):
            return [self.worker]

        async def get_scheduled_flow_runs_for_work_pool(self, _pool: str):  # noqa: PLR6301 - protocol fake.
            return []

    valid = asyncio.run(PrefectOrchestration(cast("RemoteExecutionClient", _PoolClient(10))).pool_status("pool", now))
    assert valid.detail_available
    assert _service_status(valid).worker.state == "ready"

    class _HostileInterval:
        def __float__(self) -> float:
            message = "pool parsing must not coerce hostile values"
            raise AssertionError(message)

    invalid = asyncio.run(
        PrefectOrchestration(cast("RemoteExecutionClient", _PoolClient(_HostileInterval()))).pool_status("pool", now)
    )
    assert invalid == PoolStatus(detail_available=False, queue_depth=None, observed_at=None)


def test_request_time_reconciliation_terminalizes_each_pending_link(tmp_path) -> None:
    """A run read does not wait for the next background loop to apply admission TTL."""
    from fastapi.testclient import TestClient

    from infrahub_sync.managed.app import create_app
    from infrahub_sync.managed.auth import Principal
    from infrahub_sync.managed.service import ManagedRunService

    now = datetime(2026, 8, 29, 12, tzinfo=timezone.utc)
    projection = local_product_projection(tmp_path)
    projection.create_run(
        _run(
            "run-request-reconcile",
            PrefectExecutionLink(
                flow_run_id="flow-request-reconcile",
                purpose="plan",
                attempt=1,
                submitted_at=now - timedelta(seconds=300),
            ),
        )
    )
    orchestration = _Orchestration(PoolStatus(detail_available=False, queue_depth=None, observed_at=None))
    reconciler = RunLivenessReconciler(projection, orchestration, LivenessPolicy(300, 30, 5), "pool", clock=lambda: now)

    class _Resolver:
        secret_values = ()

        @staticmethod
        def resolve(token: str) -> Principal:
            del token
            return Principal(actor="operator")

    app = create_app(ManagedRunService(projection, orchestration), _Resolver(), reconciler=reconciler)  # type: ignore[arg-type]
    response = TestClient(app).get("/runs/run-request-reconcile", headers={"Authorization": "Bearer token"})

    assert response.status_code == 200
    assert response.json()["run"]["phase"] == "abandoned"
