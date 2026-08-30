"""Pure timing policy for managed execution liveness."""

from __future__ import annotations

import os
from collections.abc import Callable  # noqa: TC003 - constructor default is evaluated at runtime.
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation

from infrahub_sync.product_store import (  # noqa: TC001 - runtime protocol boundary.
    PrefectExecutionLink,
    ProductProjection,
    ProductRun,
)

from .orchestration import (
    ManagedOrchestration,
    PoolStatus,
    normalized_pool_status,
)

RUN_ADMISSION_TTL_ENV = "INFRAHUB_SYNC_RUN_ADMISSION_TTL_SECONDS"
_POLICY_ERROR = "managed liveness settings are invalid"
_TERMINAL_STATES = frozenset({"completed", "failed", "crashed", "cancelled"})


class CancellationSelectionUnavailableError(RuntimeError):
    """Signal that Prefect could not classify a candidate without carrying provider detail."""


async def select_cancellable_execution(
    run: ProductRun,
    receipt_id: str | None,
    orchestration: ManagedOrchestration,
) -> PrefectExecutionLink | None:
    """Select an eligible link without writing product or receipt state."""
    for candidate in reversed(run.prefect_executions):
        if candidate.terminal_at is not None:
            continue
        if candidate.cancellation_requested_at is not None:
            if receipt_id is not None and candidate.cancellation_receipt_id == receipt_id:
                return candidate
            return None
        observed = await orchestration.observe(candidate.flow_run_id)
        if not observed.available:
            if observed.reason == "prefect-execution-unavailable":
                continue
            raise CancellationSelectionUnavailableError
        if observed.state not in _TERMINAL_STATES:
            return candidate
    return None


@dataclass(frozen=True, slots=True)
class LivenessPolicy:
    """Validated code-owned timing values for one managed service instance."""

    admission_ttl_seconds: int
    stall_threshold_seconds: float
    cadence_seconds: float

    @classmethod
    def from_environment(cls, *, worker_query_seconds: str = "10") -> LivenessPolicy:
        """Build the policy from the admission TTL and Prefect worker-query settings."""
        try:
            ttl_value = int(os.environ.get(RUN_ADMISSION_TTL_ENV, "300"))
        except ValueError:
            raise ValueError(_POLICY_ERROR) from None
        if not 1 <= ttl_value <= 86400:
            raise ValueError(_POLICY_ERROR)
        try:
            query = Decimal(worker_query_seconds)
        except InvalidOperation:
            raise ValueError(_POLICY_ERROR) from None
        if not query.is_finite() or not Decimal(0) < query <= Decimal(3600):
            raise ValueError(_POLICY_ERROR)
        threshold = max(float(query * 3), 30.0)
        return cls(ttl_value, threshold, max(0.25, min(5.0, threshold / 2)))


def age(now: datetime, anchor: datetime) -> float:
    """Return non-negative elapsed seconds; clock reversal cannot accelerate a verdict."""
    return max(0.0, (now - anchor).total_seconds())


class RunLivenessReconciler:
    """Conservatively terminalize durable executions; never submit or replay work.

    ``reconcile_execution`` evaluates one link against the rules below in order.
    The first matching rule decides the link; later rules do not run for it.

    ===  ==============================================================  =========================================
    #    Condition                                                       Durable transition
    ===  ==============================================================  =========================================
    0    ``terminal_at`` is set                                          none; a terminal verdict is final
    1    intent + acknowledged + observed ``cancelled``                   ``cancelled`` / ``cancelled``
    2    intent + ``now >= cancellation_recovery_deadline_at``            claimed: ``interrupted`` / ``ambiguous``;
                                                                         unclaimed: ``abandoned`` / ``abandoned``
    3    intent, deadline not reached                                    none; await acknowledgement or deadline
    4    unclaimed + ``age(submitted_at) >= admission_ttl_seconds``       ``abandoned`` / ``abandoned``
    5    unclaimed + ``age(submitted_at) >= stall_threshold_seconds``     ``stalled_at`` marker; stays claimable
         + pool detail available
    6    claimed + Prefect observes a terminal state                     ``interrupted`` / ``ambiguous``
    7    claimed + ``age(claimed_at) < stall_threshold_seconds``          none; owner grace period
    8    claimed + pool detail available + owner not fresh               ``interrupted`` / ``ambiguous``
    ===  ==============================================================  =========================================

    "intent" is a non-null ``cancellation_requested_at``. Rules 1-3 outrank 4-8,
    so within reconciliation intent is the only authority over a link that
    carries it: rules 4-8 never act on an intent-owned link. Rules 5 and 8 need
    available pool detail; an unavailable snapshot makes no transition. An owner
    is fresh when the pool reports the exact claiming worker UUID ``online``
    with a heartbeat inside ``max(3 * interval, 30)`` seconds.

    The five legal ``(terminal_state, terminal_outcome)`` verdicts are
    ``(completed, succeeded)`` and ``(failed, failed)``, written only by the
    claiming worker, plus ``(cancelled, cancelled)``, ``(abandoned, abandoned)``
    and ``(interrupted, ambiguous)``. Those three have two writers:
    reconciliation here, and request-time cancellation recovery in
    ``ManagedRunService.cancel_run``, which expires an intent-owned link at the
    same inclusive deadline as rule 2. Both writers go through the same durable
    compare-and-set, so only one of them commits a verdict. Neither submits or
    resubmits work: an execution is never replayed after any verdict.
    """

    def __init__(
        self,
        projection: ProductProjection,
        orchestration: ManagedOrchestration,
        policy: LivenessPolicy,
        work_pool_name: str,
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ) -> None:
        self._projection = projection
        self._orchestration = orchestration
        self._policy = policy
        self._work_pool_name = work_pool_name
        self._clock = clock

    @property
    def cadence_seconds(self) -> float:
        """Return the bounded background-loop cadence."""
        return self._policy.cadence_seconds

    async def reconcile_once(self) -> None:
        """Apply one bounded snapshot to every non-terminal execution."""
        now = self._clock()
        pool = await self._orchestration.pool_status(self._work_pool_name, now)
        for run_id, link in self._projection.pending_executions():
            await self.reconcile_execution(run_id, link, pool, now)

    async def reconcile_run(self, run_id: str) -> None:
        """Refresh every pending link of one requested run before it is rendered."""
        run = self._projection.lookup_run(run_id).value
        if run is None:
            return
        now = self._clock()
        pool = await self._orchestration.pool_status(self._work_pool_name, now)
        for link in run.prefect_executions:
            if link.terminal_at is None:
                await self.reconcile_execution(run_id, link, pool, now)

    async def reconcile_execution(  # noqa: PLR0911  # pylint: disable=too-many-return-statements
        self, run_id: str, link: PrefectExecutionLink, pool: PoolStatus | None = None, now: datetime | None = None
    ) -> None:
        """Reconcile one link, suitable for request-time freshness before rendering."""
        now = now or self._clock()
        pool = normalized_pool_status(pool or await self._orchestration.pool_status(self._work_pool_name, now))
        observed = await self._orchestration.observe(link.flow_run_id)
        if observed.available:
            self._projection.observe_prefect_execution(run_id, link.flow_run_id, state=observed.state)
        refreshed_run = self._projection.lookup_run(run_id).value
        if refreshed_run is None:
            return
        refreshed = next(
            (candidate for candidate in refreshed_run.prefect_executions if candidate.flow_run_id == link.flow_run_id),
            None,
        )
        if refreshed is None or refreshed.terminal_at is not None:
            return
        link = refreshed
        if link.cancellation_requested_at is not None:
            if (
                link.cancellation_acknowledged_at is not None
                and link.last_observed_state == "cancelled"
                and self._projection.cancel_execution(run_id, link.flow_run_id, terminal_at=now)
            ):
                return
            assert link.cancellation_recovery_deadline_at is not None
            if now >= link.cancellation_recovery_deadline_at:
                self._projection.expire_execution_cancellation(run_id, link.flow_run_id, terminal_at=now)
            return
        if link.claimed_at is None:
            if link.submitted_at is not None and age(now, link.submitted_at) >= self._policy.admission_ttl_seconds:
                self._projection.abandon_execution(run_id, link.flow_run_id, terminal_at=now)
                return
            if (
                link.submitted_at is not None
                and age(now, link.submitted_at) >= self._policy.stall_threshold_seconds
                and pool.detail_available
            ):
                self._projection.mark_execution_stalled(run_id, link.flow_run_id, stalled_at=now)
            return
        if observed.available and observed.state in _TERMINAL_STATES:
            self._projection.interrupt_execution(run_id, link.flow_run_id, terminal_at=now)
            return
        if link.claimed_at is None or age(now, link.claimed_at) < self._policy.stall_threshold_seconds:
            return
        if pool.detail_available and not _owner_is_fresh(link.claiming_worker_id, pool, now):
            self._projection.interrupt_execution(run_id, link.flow_run_id, terminal_at=now)


def _owner_is_fresh(worker_id: str | None, pool: PoolStatus, now: datetime) -> bool:
    """Only the exact claiming UUID can keep a claimed execution alive."""
    if worker_id is None:
        return False
    for worker in pool.workers:
        if worker.worker_id != worker_id or worker.status != "online":
            continue
        if worker.last_heartbeat is None or worker.heartbeat_interval_seconds is None:
            return False
        if worker.heartbeat_interval_seconds <= 0:
            return False
        return age(now, worker.last_heartbeat) <= max(3 * worker.heartbeat_interval_seconds, 30)
    return False
