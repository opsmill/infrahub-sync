"""Deterministic liveness policy boundaries."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, cast
from uuid import UUID, uuid4

import pytest

pytest.importorskip("prefect")

from prefect.states import Cancelled, Completed, Crashed, Failed, Running

from infrahub_sync.managed.liveness import LivenessPolicy, RunLivenessReconciler, select_cancellable_execution
from infrahub_sync.managed.orchestration import CancellationResult, Observation, PoolStatus, PoolWorker, Submission
from infrahub_sync.product_store import MutationReceipt, PrefectExecutionLink, ProductRun, local_product_projection

if TYPE_CHECKING:
    from opsmill_prefect_extras.executors import RemoteExecutionClient


def test_admission_ttl_and_prefect_query_define_liveness_formulae(monkeypatch: pytest.MonkeyPatch) -> None:
    """The two accepted environment values define all derived timing."""
    from infrahub_sync.managed.liveness import LivenessPolicy

    monkeypatch.setenv("INFRAHUB_SYNC_RUN_ADMISSION_TTL_SECONDS", "300")
    policy = LivenessPolicy.from_environment(worker_query_seconds="10")

    assert policy.admission_ttl_seconds == 300
    assert policy.stall_threshold_seconds == 30
    assert policy.cadence_seconds == 5


@pytest.mark.parametrize("value", ["0.1", "1", "1.0", "3600"])
def test_prefect_query_seconds_accepts_only_canonical_decimal_strings(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    """The query setting has one grammar before its numeric domain is evaluated."""
    monkeypatch.setenv("INFRAHUB_SYNC_RUN_ADMISSION_TTL_SECONDS", "300")
    assert LivenessPolicy.from_environment(worker_query_seconds=value).stall_threshold_seconds >= 30


@pytest.mark.parametrize("value", [" 1", "1 ", "+1", "-1", "1e1", "01", ".1", "1.", "NaN", "Infinity", "0", "3600.1"])
def test_prefect_query_seconds_refuses_noncanonical_or_out_of_domain_values(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    monkeypatch.setenv("INFRAHUB_SYNC_RUN_ADMISSION_TTL_SECONDS", "300")
    with pytest.raises(ValueError, match=r"^managed liveness settings are invalid$"):
        LivenessPolicy.from_environment(worker_query_seconds=value)


def test_prefect_query_seconds_refuses_hostile_exact_type_without_reading_it(monkeypatch: pytest.MonkeyPatch) -> None:
    class _HostileValue:
        def __str__(self) -> str:
            message = "hostile values must not be coerced"
            raise AssertionError(message)

    monkeypatch.setenv("INFRAHUB_SYNC_RUN_ADMISSION_TTL_SECONDS", "300")
    with pytest.raises(ValueError, match=r"^managed liveness settings are invalid$"):
        LivenessPolicy.from_environment(worker_query_seconds=cast("str", _HostileValue()))


def test_admission_ttl_refuses_oversized_digits_with_fixed_unchained_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("INFRAHUB_SYNC_RUN_ADMISSION_TTL_SECONDS", "9" * 5000)

    with pytest.raises(ValueError, match=r"^managed liveness settings are invalid$") as caught:
        LivenessPolicy.from_environment(worker_query_seconds="10")

    assert caught.value.__cause__ is None


class _Orchestration:
    def __init__(self, pool: PoolStatus, observation: Observation | None = None) -> None:
        self.pool = pool
        self.observation = observation or Observation(available=True, state="running")
        self.observed_flow_run_ids: list[str] = []

    async def pool_status(self, work_pool_name: str, now: datetime) -> PoolStatus:
        del work_pool_name, now
        return self.pool

    async def submit(self, parameters: dict[str, object], *, idempotency_key: str) -> Submission:  # noqa: PLR6301
        del parameters, idempotency_key
        return Submission(flow_run_id="unused", state="running")

    async def observe(self, flow_run_id: str) -> Observation:
        self.observed_flow_run_ids.append(flow_run_id)
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


@pytest.mark.parametrize(
    ("receipt_id", "older_receipt_id", "expected_flow_run_id"),
    [
        ("receipt-new", "receipt-old", "flow-new"),
        ("receipt-old", "receipt-old", None),
        (None, None, None),
    ],
    ids=("matching", "different", "absent"),
)
def test_newest_nonterminal_execution_owns_cancellation_selection(
    receipt_id: str | None,
    older_receipt_id: str | None,
    expected_flow_run_id: str | None,
) -> None:
    """An unmatched intent on the newest link cannot expose an older link."""
    now = datetime(2026, 8, 29, 12, tzinfo=timezone.utc)

    def link(flow_run_id: str, cancellation_receipt_id: str | None) -> PrefectExecutionLink:
        return PrefectExecutionLink(
            flow_run_id=flow_run_id,
            purpose="plan",
            attempt=1,
            submitted_at=now,
            cancellation_requested_at=now if cancellation_receipt_id is not None else None,
            cancellation_recovery_deadline_at=(
                now + timedelta(seconds=30) if cancellation_receipt_id is not None else None
            ),
            cancellation_receipt_id=cancellation_receipt_id,
        )

    run = ProductRun(
        run_id="run-cancellation-owner",
        operation="plan",
        configuration_reference="sha256:configuration",
        actor="operator@example.com",
        started_at=now,
        phase="planning",
        prefect_executions=(link("flow-old", older_receipt_id), link("flow-new", "receipt-new")),
    )
    orchestration = _Orchestration(PoolStatus(detail_available=False, queue_depth=None, observed_at=None))

    selected = asyncio.run(select_cancellable_execution(run, receipt_id, orchestration))

    assert (selected.flow_run_id if selected is not None else None) == expected_flow_run_id
    assert orchestration.observed_flow_run_ids == []


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
        recovery_seconds=30,
        expected_latest_position=0,
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
            PoolWorker(owner, "offline", now, 10.0),
            PoolWorker(str(uuid4()), "online", now, 10.0),
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
        workers=(PoolWorker(owner, "online", now + timedelta(hours=1), 10.0),),
    )
    reconciler = RunLivenessReconciler(
        projection, _Orchestration(pool), LivenessPolicy(300, 30, 5), "pool", clock=lambda: now - timedelta(days=1)
    )

    asyncio.run(reconciler.reconcile_once())

    run = projection.lookup_run("run-fresh-owner").value
    assert run is not None
    assert run.prefect_executions[0].terminal_at is None


@pytest.mark.parametrize(
    ("state", "canonical_state"),
    [
        (Completed(name="Cached"), "completed"),
        (Failed(name="Retry Exhausted"), "failed"),
        (Crashed(name="Infrastructure Lost"), "crashed"),
        (Cancelled(name="Externally Stopped"), "cancelled"),
    ],
    ids=("completed", "failed", "crashed", "cancelled"),
)
def test_custom_named_terminal_prefect_states_interrupt_a_claimed_execution(
    tmp_path, state: object, canonical_state: str
) -> None:
    """Terminal StateType remains authoritative when Prefect supplies a custom name."""
    from infrahub_sync.managed.orchestration import PrefectOrchestration

    now = datetime(2026, 8, 29, 12, tzinfo=timezone.utc)
    owner_id = uuid4()
    flow_run_id = uuid4()

    class _PrefectClient:
        @staticmethod
        async def read_flow_run(requested_id: UUID) -> SimpleNamespace:
            assert requested_id == flow_run_id
            return SimpleNamespace(state=state)

        async def read_workers_for_work_pool(self, _pool: str):  # noqa: PLR6301
            return [
                SimpleNamespace(
                    id=owner_id,
                    status="ONLINE",
                    last_heartbeat_time=now,
                    heartbeat_interval_seconds=10,
                )
            ]

        async def get_scheduled_flow_runs_for_work_pool(self, _pool: str):  # noqa: PLR6301
            return []

    projection = local_product_projection(tmp_path)
    run_id = f"run-custom-{canonical_state}"
    projection.create_run(
        _run(
            run_id,
            PrefectExecutionLink(
                flow_run_id=str(flow_run_id),
                purpose="plan",
                attempt=1,
                submitted_at=now - timedelta(minutes=10),
                claimed_at=now - timedelta(minutes=1),
                claiming_worker_id=str(owner_id),
            ),
        )
    )
    orchestration = PrefectOrchestration(cast("RemoteExecutionClient", _PrefectClient()))
    reconciler = RunLivenessReconciler(
        projection,
        orchestration,
        LivenessPolicy(300, 30, 5),
        "pool",
        clock=lambda: now,
    )

    asyncio.run(reconciler.reconcile_once())

    run = projection.lookup_run(run_id).value
    assert run is not None
    link = run.prefect_executions[0]
    assert link.terminal_state == "interrupted"
    assert link.last_observed_state == canonical_state


def test_custom_named_nonterminal_prefect_observation_preserves_its_name() -> None:
    """A nonterminal custom name remains useful live orchestration detail."""
    from infrahub_sync.managed.orchestration import PrefectOrchestration

    flow_run_id = uuid4()

    class _PrefectClient:
        @staticmethod
        async def read_flow_run(requested_id: UUID) -> SimpleNamespace:
            assert requested_id == flow_run_id
            return SimpleNamespace(state=Running(name="Awaiting Cache"))

    observation = asyncio.run(
        PrefectOrchestration(cast("RemoteExecutionClient", _PrefectClient())).observe(str(flow_run_id))
    )

    assert observation == Observation(available=True, state="awaiting cache")


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
                workers=(PoolWorker(new_owner, "online", now, 10.0),),
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


@pytest.mark.parametrize(
    ("interval", "expected_state"),
    [
        (105_179_299_199, "available"),
        (105_179_299_200, "unavailable"),
        (10**10_000, "unavailable"),
    ],
    ids=("maximum-usable", "one-past-maximum", "oversized-integer"),
)
def test_pool_status_bounds_heartbeat_interval_to_the_usable_datetime_age(interval: int, expected_state: str) -> None:
    """Three heartbeat intervals must fit within the representable datetime age."""
    now = datetime(2026, 8, 29, 12, tzinfo=timezone.utc)

    class _PoolClient:
        async def read_workers_for_work_pool(self, _pool: str):  # noqa: PLR6301
            return [
                SimpleNamespace(
                    id=uuid4(),
                    status="ONLINE",
                    last_heartbeat_time=now,
                    heartbeat_interval_seconds=interval,
                )
            ]

        async def get_scheduled_flow_runs_for_work_pool(self, _pool: str):  # noqa: PLR6301
            return []

    from infrahub_sync.managed.orchestration import PrefectOrchestration

    snapshot = asyncio.run(PrefectOrchestration(cast("RemoteExecutionClient", _PoolClient())).pool_status("pool", now))

    if expected_state == "available":
        assert snapshot.detail_available
        assert timedelta(seconds=3 * interval) <= datetime.max.replace(tzinfo=timezone.utc) - datetime.min.replace(
            tzinfo=timezone.utc
        )
    else:
        assert snapshot == PoolStatus(detail_available=False, queue_depth=None, observed_at=None)


@pytest.mark.parametrize("status", ["ONLINE", "online", "OFFLINE", "offline"])
def test_pool_status_accepts_only_the_pinned_worker_status_vocabulary(status: str) -> None:
    """Exact string doubles model only values shipped by Prefect's WorkerStatus enum."""
    now = datetime(2026, 8, 29, 12, tzinfo=timezone.utc)

    class _PoolClient:
        async def read_workers_for_work_pool(self, _pool: str):  # noqa: PLR6301
            return [SimpleNamespace(id=uuid4(), status=status, last_heartbeat_time=now, heartbeat_interval_seconds=10)]

        async def get_scheduled_flow_runs_for_work_pool(self, _pool: str):  # noqa: PLR6301
            return []

    from infrahub_sync.managed.orchestration import PrefectOrchestration

    snapshot = asyncio.run(PrefectOrchestration(cast("RemoteExecutionClient", _PoolClient())).pool_status("pool", now))
    assert snapshot.detail_available


@pytest.mark.parametrize("status", ["idle", "online ", " ONLINE", "unknown", 1, True])
def test_pool_status_malformed_status_makes_the_whole_snapshot_unavailable(status: object) -> None:
    now = datetime(2026, 8, 29, 12, tzinfo=timezone.utc)

    class _PoolClient:
        async def read_workers_for_work_pool(self, _pool: str):  # noqa: PLR6301
            return [SimpleNamespace(id=uuid4(), status=status, last_heartbeat_time=now, heartbeat_interval_seconds=10)]

        async def get_scheduled_flow_runs_for_work_pool(self, _pool: str):  # noqa: PLR6301
            return []

    from infrahub_sync.managed.orchestration import PrefectOrchestration

    snapshot = asyncio.run(PrefectOrchestration(cast("RemoteExecutionClient", _PoolClient())).pool_status("pool", now))
    assert snapshot == PoolStatus(detail_available=False, queue_depth=None, observed_at=None)


@pytest.mark.parametrize("scheduled", [{}, (), {"flow": object()}])
def test_pool_status_rejects_non_list_scheduled_run_shapes(scheduled: object) -> None:
    """Only the pinned Prefect list response can supply a public queue count."""
    now = datetime(2026, 8, 29, 12, tzinfo=timezone.utc)

    class _PoolClient:
        async def read_workers_for_work_pool(self, _pool: str):  # noqa: PLR6301
            return []

        async def get_scheduled_flow_runs_for_work_pool(self, _pool: str):  # noqa: PLR6301
            return scheduled

    from infrahub_sync.managed.orchestration import PrefectOrchestration

    snapshot = asyncio.run(PrefectOrchestration(cast("RemoteExecutionClient", _PoolClient())).pool_status("pool", now))
    assert snapshot == PoolStatus(detail_available=False, queue_depth=None, observed_at=None)


@pytest.mark.parametrize("workers", [{}, (), {"worker": object()}])
def test_pool_status_rejects_non_list_worker_shapes(workers: object) -> None:
    """Only the pinned Prefect list response can supply worker evidence."""
    now = datetime(2026, 8, 29, 12, tzinfo=timezone.utc)

    class _PoolClient:
        async def read_workers_for_work_pool(self, _pool: str):  # noqa: PLR6301
            return workers

        async def get_scheduled_flow_runs_for_work_pool(self, _pool: str):  # noqa: PLR6301
            return []

    from infrahub_sync.managed.orchestration import PrefectOrchestration

    snapshot = asyncio.run(PrefectOrchestration(cast("RemoteExecutionClient", _PoolClient())).pool_status("pool", now))
    assert snapshot == PoolStatus(detail_available=False, queue_depth=None, observed_at=None)


def test_pool_status_maps_invalid_observation_time_to_unavailable() -> None:
    """An invalid adapter snapshot cannot escape as partially available detail."""

    class _PoolClient:
        async def read_workers_for_work_pool(self, _pool: str):  # noqa: PLR6301
            return []

        async def get_scheduled_flow_runs_for_work_pool(self, _pool: str):  # noqa: PLR6301
            return []

    from infrahub_sync.managed.orchestration import PrefectOrchestration

    snapshot = asyncio.run(
        PrefectOrchestration(cast("RemoteExecutionClient", _PoolClient())).pool_status(
            "pool",
            datetime(2026, 8, 29, 12),  # noqa: DTZ001 - deliberate malformed provider timestamp.
        )
    )
    assert snapshot == PoolStatus(detail_available=False, queue_depth=None, observed_at=None)


@pytest.mark.parametrize("worker_order", ["online-first", "offline-first"])
def test_duplicate_worker_uuid_snapshot_is_unavailable_and_cannot_drive_reconciliation(
    tmp_path, worker_order: str
) -> None:
    """Conflicting records for one worker identity invalidate the whole pool snapshot."""
    from infrahub_sync.managed.orchestration import PrefectOrchestration
    from infrahub_sync.managed.service import _service_status  # noqa: PLC2701 - public boundary behavior test.

    now = datetime(2026, 8, 29, 12, tzinfo=timezone.utc)
    worker_id = uuid4()
    workers = [
        SimpleNamespace(id=worker_id, status="ONLINE", last_heartbeat_time=now, heartbeat_interval_seconds=10),
        SimpleNamespace(id=worker_id, status="OFFLINE", last_heartbeat_time=now, heartbeat_interval_seconds=10),
    ]
    if worker_order == "offline-first":
        workers.reverse()

    class _PoolClient:
        @staticmethod
        async def read_workers_for_work_pool(_pool: str) -> list[SimpleNamespace]:
            return workers

        async def get_scheduled_flow_runs_for_work_pool(self, _pool: str):  # noqa: PLR6301
            return []

    snapshot = asyncio.run(PrefectOrchestration(cast("RemoteExecutionClient", _PoolClient())).pool_status("pool", now))
    projection = local_product_projection(tmp_path)
    projection.create_run(
        _run(
            "run-duplicate-workers",
            PrefectExecutionLink(
                flow_run_id="flow-duplicate-workers",
                purpose="plan",
                attempt=1,
                submitted_at=now - timedelta(seconds=30),
            ),
        )
    )
    reconciler = RunLivenessReconciler(
        projection, _Orchestration(snapshot), LivenessPolicy(300, 30, 5), "pool", clock=lambda: now
    )

    status = _service_status(snapshot)
    asyncio.run(reconciler.reconcile_once())

    assert snapshot == PoolStatus(detail_available=False, queue_depth=None, observed_at=None)
    assert status.worker.detail_available is False
    stored = projection.lookup_run("run-duplicate-workers").value
    assert stored is not None
    assert stored.prefect_executions[0].stalled_at is None


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("worker_id", "not-a-uuid"),
        ("worker_id", type("WorkerId", (str,), {})("8c1da53d-0e6b-4d3d-a0f1-97b6a9ccebf0")),
        ("status", "ONLINE"),
        ("status", "unknown"),
        ("last_heartbeat", datetime(2026, 8, 29, 12)),  # noqa: DTZ001 - deliberate naive provider value.
        ("last_heartbeat", type("Heartbeat", (datetime,), {})(2026, 8, 29, 12, tzinfo=timezone.utc)),
        ("heartbeat_interval_seconds", -1.0),
        ("heartbeat_interval_seconds", 10),
        ("heartbeat_interval_seconds", float("nan")),
        ("heartbeat_interval_seconds", 105_179_299_200.0),
    ],
    ids=(
        "invalid-uuid",
        "uuid-string-subclass",
        "unnormalized-status",
        "unknown-status",
        "naive-heartbeat",
        "heartbeat-subclass",
        "negative-interval",
        "integer-interval",
        "nonfinite-interval",
        "oversized-interval",
    ),
)
def test_pool_status_refuses_every_malformed_nested_worker_field(field: str, value: object) -> None:
    now = datetime(2026, 8, 29, 12, tzinfo=timezone.utc)
    worker: dict[str, object] = {
        "worker_id": "8c1da53d-0e6b-4d3d-a0f1-97b6a9ccebf0",
        "status": "online",
        "last_heartbeat": now,
        "heartbeat_interval_seconds": 10.0,
    }
    worker[field] = value

    with pytest.raises(ValueError):
        PoolStatus(
            detail_available=True,
            queue_depth=0,
            observed_at=now,
            workers=(PoolWorker(**cast("Any", worker)),),
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("worker_id", "not-a-uuid"),
        ("status", "ONLINE"),
        ("last_heartbeat", datetime(2026, 8, 29, 12)),  # noqa: DTZ001 - deliberate naive provider value.
        ("heartbeat_interval_seconds", -1.0),
    ],
)
def test_malformed_pool_snapshot_is_unavailable_and_causes_no_liveness_transition(
    tmp_path,
    field: str,
    value: object,
) -> None:
    """Provider-supplied invalid worker detail cannot drive status or product state."""
    from infrahub_sync.managed.service import _service_status  # noqa: PLC2701 - public boundary behavior test.

    now = datetime(2026, 8, 29, 12, tzinfo=timezone.utc)
    worker: dict[str, object] = {
        "worker_id": "8c1da53d-0e6b-4d3d-a0f1-97b6a9ccebf0",
        "status": "online",
        "last_heartbeat": now,
        "heartbeat_interval_seconds": 10.0,
    }
    worker[field] = value
    malformed = cast(
        "PoolStatus",
        SimpleNamespace(
            detail_available=True,
            queue_depth=0,
            observed_at=now,
            workers=(SimpleNamespace(**worker),),
        ),
    )
    projection = local_product_projection(tmp_path)
    projection.create_run(
        _run(
            "run-malformed-pool",
            PrefectExecutionLink(
                flow_run_id="flow-malformed-pool",
                purpose="plan",
                attempt=1,
                submitted_at=now - timedelta(seconds=30),
            ),
        )
    )
    reconciler = RunLivenessReconciler(
        projection,
        _Orchestration(malformed),
        LivenessPolicy(300, 30, 5),
        "pool",
        clock=lambda: now,
    )

    status = _service_status(malformed)
    asyncio.run(reconciler.reconcile_once())

    assert status.worker.model_dump() == {
        "state": "unavailable",
        "detail_available": False,
        "live_workers": None,
        "queue_depth": None,
        "observed_at": None,
    }
    run = projection.lookup_run("run-malformed-pool").value
    assert run is not None
    assert run.prefect_executions[0].stalled_at is None
    assert run.prefect_executions[0].terminal_at is None


@pytest.mark.parametrize(
    "snapshot",
    [
        {"detail_available": True, "queue_depth": -1, "observed_at": datetime(2026, 8, 29, tzinfo=timezone.utc)},
        {"detail_available": True, "queue_depth": 0, "observed_at": None},
        {
            "detail_available": False,
            "queue_depth": 0,
            "observed_at": datetime(2026, 8, 29, tzinfo=timezone.utc),
        },
    ],
)
def test_pool_status_refuses_count_and_availability_invariant_violations(snapshot: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        PoolStatus(**cast("Any", snapshot))


@pytest.mark.parametrize(
    "payload",
    [
        {
            "state": "unavailable",
            "detail_available": False,
            "live_workers": 0,
            "queue_depth": None,
            "observed_at": None,
        },
        {
            "state": "ready",
            "detail_available": True,
            "live_workers": -1,
            "queue_depth": 0,
            "observed_at": datetime(2026, 8, 29, tzinfo=timezone.utc),
        },
        {
            "state": "busy",
            "detail_available": True,
            "live_workers": 1,
            "queue_depth": 0,
            "observed_at": datetime(2026, 8, 29, tzinfo=timezone.utc),
        },
        {
            "state": "ready",
            "detail_available": True,
            "live_workers": True,
            "queue_depth": 0,
            "observed_at": datetime(2026, 8, 29, tzinfo=timezone.utc),
        },
    ],
)
def test_public_worker_status_refuses_count_and_availability_invariant_violations(payload: dict[str, object]) -> None:
    from pydantic import ValidationError

    from infrahub_sync.managed.models import WorkerStatusResource

    with pytest.raises(ValidationError):
        WorkerStatusResource.model_validate(payload)


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
